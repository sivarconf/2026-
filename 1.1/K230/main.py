"""
K230 扑克牌 YOLO 识别主程序
适配 CanMV K230 v1.6 + PipeLine 框架

功能概述：
- 双流 media buffer（通道0显示，通道2 AI推理）
- 55类分类（BLANK背景 + 54种扑克牌）
- 滑动窗口投票 + 永久冷却排除防误识别

串口协议（STM32 ↔ K230）：
- STM32 → K230: 0x01(START) / 0x02(STOP)
- K230 → STM32: 0xA0(握手) / 0xA1(ACK START) / 0xA2(ACK STOP)

算法版本：v24
- 极度简化：移除 BLANK_GAP 和 TOP_GAP 两层人为限制
- 只保留最核心的一层门槛：MIN_CONFIDENCE=0.25 + BLANK 过滤
- 55类分类中 top1 与 BLANK/top2 差值小是常态，不应作为过滤条件
- 众数裁决天然抗噪声，大量帧进入投票才能发挥效果
- 帧计数超时控制（替代时间超时）+ 移除冷却期

版本：1.4
日期：2026-05-14
"""

import os
import ujson
from media.sensor import *
from media.display import *
from media.media import *
from libs.PipeLine import PipeLine
from libs.Utils import ScopedTiming
from time import ticks_ms, ticks_diff
import nncase_runtime as nn
import ulab.numpy as np
import gc

LOG_FILE = "/data/poker_log.txt"

_log_file = None

def _open_log():
    global _log_file
    try:
        _log_file = open(LOG_FILE, "w")
        _log_file.write("=== Poker Log Started ===\n")
        _log_file.flush()
    except Exception as e:
        print("Log open error:", e)

def log_write(msg):
    print(msg)
    if _log_file:
        try:
            _log_file.write(msg + "\n")
            _log_file.flush()
        except Exception:
            pass

# ---- 分辨率配置 ----
DISPLAY_WIDTH = 640
DISPLAY_HEIGHT = 480
RGB888P_SIZE = [DISPLAY_WIDTH, DISPLAY_HEIGHT]

root_path = "/sdcard/mp_deployment_source/"
config_path = root_path + "deploy_config.json"

IMG_SIZE = (224, 224)

# ==================== 算法参数 ====================

# ---- 置信度过滤（简化版：只留最核心门槛）----
TOP_N = 3
MIN_CONFIDENCE = 0.25      # 单帧最低门槛（降低以接纳更多帧进入投票）
# 移除 BLANK_GAP：55类中 top1 与 BLANK 差值小是常态，不应作为过滤条件
# 移除 TOP_GAP：top1/top2 接近是常态，由众数裁决兜底，不提前降权
WARMUP_FRAMES = 20         # 开机预热帧数（缩短）

# ---- 区间检测配置 ----
COLLECT_TIMEOUT = 15        # 连续 N 帧无有效牌 → 结束当前区间（约0.5秒@30fps）
MIN_VALID_FRAMES = 2       # 有效帧少于这个数 → 区间作废

# ---- 花色映射 ----
SUIT_MAP = {
    "S": ("黑桃", (0, 0, 0)),
    "H": ("红桃", (255, 0, 0)),
    "C": ("梅花", (0, 0, 0)),
    "D": ("方块", (255, 0, 0)),
    "JOKER_B": ("小王", (0, 0, 0)),
    "JOKER_R": ("大王", (255, 0, 0)),
}


def card_label_to_display(label):
    if label == "BLANK":
        return None, None
    elif label.startswith("S_"):
        suit, color = SUIT_MAP["S"]
        return suit + label[2:], color
    elif label.startswith("H_"):
        suit, color = SUIT_MAP["H"]
        return suit + label[2:], color
    elif label.startswith("C_"):
        suit, color = SUIT_MAP["C"]
        return suit + label[2:], color
    elif label.startswith("D_"):
        suit, color = SUIT_MAP["D"]
        return suit + label[2:], color
    elif label == "JOKER_B":
        return SUIT_MAP["JOKER_B"]
    elif label == "JOKER_R":
        return SUIT_MAP["JOKER_R"]
    else:
        return label, (0, 0, 0)


def read_deploy_config(config_path):
    with open(config_path, 'r') as json_file:
        config = ujson.load(json_file)
    return config


def softmax(x):
    exp_x = np.exp(x - np.max(x))
    return exp_x / np.sum(exp_x)


BLANK_IDX = 0  # BLANK（空白/背景）类别索引


def get_top_predictions(softmax_res, labels, top_n=3):
    softmax_np = np.array(softmax_res)
    n = len(softmax_np)
    top_preds = []
    count = 0
    idx = 0
    while count < min(top_n, n - 1) and idx < n:
        if idx == BLANK_IDX:
            idx += 1
            continue
        max_idx = idx
        max_val = float(softmax_np[idx])
        for j in range(idx + 1, n):
            if j == BLANK_IDX:
                continue
            v = float(softmax_np[j])
            if v > max_val:
                max_val = v
                max_idx = j
        top_preds.append((max_idx, labels[max_idx], max_val))
        softmax_np[max_idx] = -999999.0
        count += 1
        idx += 1
    return top_preds


# ==================== 扑克牌收集器（帧计数超时 + 众数裁决）====================

class CardCollector:
    """
    v24 算法（帧计数超时 + 众数裁决）：

    核心思路：
    - 每帧累计连续无有效牌帧数
    - 检测到有效牌 → 重置无牌帧计数，继续收集
    - 连续 COLLECT_TIMEOUT 帧无有效牌 → 立刻裁决
    - 确认后直接回 IDLE，不设冷却期（赛道上的牌不会折返）

    状态流转：
        IDLE → (检测到有效牌) → COLLECTING → (无牌帧数达到TO) → confirm/discard → IDLE
    """

    STATE_IDLE = 0
    STATE_COLLECTING = 1

    def __init__(self, labels):
        self.labels = labels
        self.reset()

    def reset(self):
        self.state = self.STATE_IDLE
        self.frame = 0
        self.confirmed_cards = set()
        self.confirmed_ranks = set()
        self.vote_bucket = []
        self.no_card_frames = 0   # 累计连续无有效牌帧数

    def _get_rank(self, label):
        if label.startswith("JOKER"):
            return label
        if "_" in label:
            return label.split("_", 1)[1]
        return label

    def _get_mode(self, lst):
        if not lst:
            return -1, 0
        counts = {}
        for x in lst:
            counts[x] = counts.get(x, 0) + 1
        max_idx = -1
        max_cnt = 0
        for k, v in counts.items():
            if v > max_cnt:
                max_cnt = v
                max_idx = k
        return max_idx, max_cnt

    def _is_valid_candidate(self, idx, label):
        if idx in self.confirmed_cards:
            return False
        rank = self._get_rank(label)
        if rank in self.confirmed_ranks:
            return False
        return True

    def tick(self, cls_idx, top_preds):
        """
        每帧调用。
        cls_idx: 本帧 Recognizer.run() 返回的 cls_idx（-1表示无效帧）
        返回: (action, card_idx)
            action: "none" | "confirm" | "discard"
        """
        self.frame += 1

        # ---- IDLE：等待首次有效牌 ----
        if self.state == self.STATE_IDLE:
            if cls_idx < 0:
                return "none", -1
            label = self.labels[cls_idx]
            if self._is_valid_candidate(cls_idx, label):
                self.state = self.STATE_COLLECTING
                self.vote_bucket = [cls_idx]
                self.no_card_frames = 0
            return "none", -1

        # ---- COLLECTING：收集区间 ----
        if self.state == self.STATE_COLLECTING:
            if cls_idx >= 0:
                label = self.labels[cls_idx]
                if self._is_valid_candidate(cls_idx, label):
                    self.vote_bucket.append(cls_idx)
                    self.no_card_frames = 0   # 有有效牌，重置无牌帧计数
                else:
                    # Top1 是已确认牌，尝试降级到次选
                    filtered = [(i, l) for i, l, s in top_preds if self._is_valid_candidate(i, l)]
                    if filtered:
                        self.vote_bucket.append(filtered[0][0])
                        self.no_card_frames = 0
                    else:
                        self.no_card_frames += 1   # 有牌但被过滤，算无有效牌
            else:
                self.no_card_frames += 1

            # 连续 N 帧无有效牌 → 立刻裁决
            if self.no_card_frames >= COLLECT_TIMEOUT:
                return self._finish_collect()

            return "none", -1

        return "none", -1

    def _finish_collect(self):
        valid_frames = len(self.vote_bucket)
        self.state = self.STATE_IDLE

        if valid_frames < MIN_VALID_FRAMES:
            self.vote_bucket = []
            return "discard", -1

        mode_idx, mode_count = self._get_mode(self.vote_bucket)
        if mode_count >= MIN_VALID_FRAMES:
            self.confirmed_cards.add(mode_idx)
            rank = self._get_rank(self.labels[mode_idx])
            self.confirmed_ranks.add(rank)
            self.vote_bucket = []
            return "confirm", mode_idx

        self.vote_bucket = []
        return "discard", -1


# ==================== YOLO 识别器 ====================

class Recognizer:
    def __init__(self):
        self.deploy_conf = read_deploy_config(config_path)
        self.kmodel_name = self.deploy_conf["kmodel_path"]
        self.labels = self.deploy_conf["categories"]
        self.confidence_threshold = self.deploy_conf["confidence_threshold"]
        self.num_classes = self.deploy_conf["num_classes"]

        self.kpu = nn.kpu()
        self.ai2d = nn.ai2d()
        self.kpu.load_kmodel(root_path + self.kmodel_name)
        self.ai2d.set_dtype(
            nn.ai2d_format.NCHW_FMT,
            nn.ai2d_format.NCHW_FMT,
            np.uint8, np.uint8
        )
        self.ai2d.set_resize_param(True, nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        self.ai2d_builder = self.ai2d.build(
            [1, 3, RGB888P_SIZE[1], RGB888P_SIZE[0]],
            [1, 3, IMG_SIZE[1], IMG_SIZE[0]]
        )
        data = np.ones((1, 3, IMG_SIZE[1], IMG_SIZE[0]), dtype=np.uint8)
        self.ai2d_output_tensor = nn.from_numpy(data)

    def run(self, rgb888p_img):
        """
        返回: (cls_idx, score, top_preds, adjusted_idx, adjusted_score, is_weak, softmax_res)
        """
        ai2d_input_tensor = nn.from_numpy(rgb888p_img)
        self.ai2d_builder.run(ai2d_input_tensor, self.ai2d_output_tensor)
        self.kpu.set_input_tensor(0, self.ai2d_output_tensor)
        del ai2d_input_tensor

        self.kpu.run()

        results = []
        for i in range(self.kpu.outputs_size()):
            output_data = self.kpu.get_output_tensor(i)
            result = output_data.to_numpy()
            results.append(result)

        softmax_res = softmax(results[0][0])
        top_preds = get_top_predictions(softmax_res, self.labels, TOP_N)

        cls_idx = -1
        score = 0.0
        is_weak = False

        if len(top_preds) > 0:
            top1_idx, top1_label, top1_score = top_preds[0]

            # 第一关：Top1 是 BLANK 直接跳过
            if top1_label == "BLANK":
                del results
                gc.collect()
                return cls_idx, score, top_preds, -1, 0.0, is_weak, softmax_res

            # 第二关：单帧最低置信度门槛
            if top1_score < MIN_CONFIDENCE:
                del results
                gc.collect()
                return cls_idx, score, top_preds, -1, 0.0, is_weak, softmax_res

            # 通过门槛，交给众数裁决
            cls_idx = top1_idx
            score = top1_score

        del results
        gc.collect()
        return cls_idx, score, top_preds, cls_idx, score, is_weak, softmax_res


# ==================== 主程序 ====================

def main():
    _open_log()

    pl = PipeLine(
        rgb888p_size=RGB888P_SIZE,
        display_mode="lcd",
        display_size=[DISPLAY_WIDTH, DISPLAY_HEIGHT],
        osd_layer_num=2,
        debug_mode=0
    )
    pl.create()

    log_write("Pipeline OK")

    log_write("Loading kmodel...")
    recognizer = Recognizer()

    log_write("Recognizer OK")

    log_write("Init UART...")
    from ybUtils.YbUart import YbUart
    uart = YbUart()
    uart.send(b'\xA0')

    log_write("K230 Poker v24 | WU={} MIN={:.2f} | TO={}fr MIN_V={}".format(
        WARMUP_FRAMES, MIN_CONFIDENCE, COLLECT_TIMEOUT, MIN_VALID_FRAMES))

    state = 0  # 0=待机, 1=识别中, 2=展示结果
    record_list = []
    frame_idx = 0
    collector = None

    fps = 0
    fps_start = 0
    fps_count = 0
    diag_counter = 0

    try:
        while True:
            os.exitpoint()

            now_ms = ticks_ms()
            if fps_start == 0:
                fps_start = now_ms
            fps_count += 1
            if fps_count >= 100:
                fps = fps_count * 1000 // max(1, ticks_diff(now_ms, fps_start))
                fps_count = 0
                fps_start = now_ms

            # ---- 串口接收 ----
            try:
                recv_data = uart.read()
                if recv_data:
                    for b in recv_data:
                        if b == 0x01:
                            record_list = []
                            frame_idx = WARMUP_FRAMES
                            collector = CardCollector(recognizer.labels)
                            collector.reset()
                            state = 1
                            uart.send(b'\xA1')
                            log_write("START")
                        elif b == 0x02:
                            state = 2
                            uart.send(b'\xA2')
                            log_write("STOP: {}".format(record_list))
            except Exception as e:
                log_write("UART err: {}".format(e))

            if state == 0 or state == 1:
                frame_idx += 1
                frame = pl.get_frame()

                cls_idx, score, top_preds, adj_idx, adj_score, is_weak, softmax_res = recognizer.run(frame)

                # ---- 诊断打印（每60帧一次，有牌时更多打印）----
                diag_counter += 1
                if diag_counter >= 60 or (cls_idx >= 0 and diag_counter >= 15):
                    diag_counter = 0
                    if len(top_preds) > 0:
                        t1 = top_preds[0]
                        bscore = float(softmax_res[BLANK_IDX])
                        gap_blank = float(t1[2]) - bscore
                        if len(top_preds) >= 2:
                            gap12 = float(t1[2]) - float(top_preds[1][2])
                        else:
                            gap12 = 999.0
                        log_write("[DIAG] top1={} {:.3f} blank={:.3f} gapB={:.3f} gap12={:.3f} cls={} thresh={:.3f}".format(
                            t1[1], float(t1[2]), bscore, gap_blank, gap12,
                            cls_idx, MIN_CONFIDENCE))
                    else:
                        bscore = float(softmax_res[BLANK_IDX])
                        log_write("[DIAG] no_pred blank={:.3f}".format(bscore))

                # ---- 状态机驱动（预热期内跳过）----
                action = "none"
                confirmed_idx = -1
                if collector is not None and frame_idx > WARMUP_FRAMES:
                    action, confirmed_idx = collector.tick(cls_idx, top_preds)

                    if action == "confirm":
                        confirmed_label = recognizer.labels[confirmed_idx]
                        record_list.append(confirmed_label)
                        log_write(">>> CONFIRMED: {} (idx={}) frames={}".format(
                            confirmed_label, confirmed_idx, len(collector.vote_bucket)))

                # ---- OSD ----
                pl.osd_img.clear()
                pl.osd_img.draw_string_advanced(10, 10, 28, "FPS: {}".format(fps), (0, 255, 0))

                status_color = (200, 200, 200) if state == 0 else (0, 255, 0)
                status_text = "Waiting..." if state == 0 else "Recording..."
                pl.osd_img.draw_string_advanced(10, 50, 32, status_text, status_color)

                # 预热期提示
                if state == 1 and frame_idx <= WARMUP_FRAMES:
                    remaining = WARMUP_FRAMES - frame_idx + 1
                    pl.osd_img.draw_string_advanced(
                        10, 90, 20, "[Warmup {}]".format(remaining), (255, 150, 0))

                if state == 1:
                    # Top-N 显示
                    if len(top_preds) > 0:
                        top_text = "Top: "
                        for i, (iidx, lbl, sc) in enumerate(top_preds[:TOP_N]):
                            marker = "*" if i == 0 else " "
                            top_text += "{}{} {:.2f}  ".format(marker, lbl, sc)
                        pl.osd_img.draw_string_advanced(10, 130, 20, top_text, (100, 255, 100))

                    # 当前识别结果
                    if cls_idx >= 0:
                        label = recognizer.labels[cls_idx]
                        score_color = (0, 255, 0) if not is_weak else (255, 200, 0)
                        weak_tag = " [WEAK]" if is_weak else ""
                        pl.osd_img.draw_string_advanced(
                            10, 160, 28,
                            "now: {}{} {:.2f}".format(label, weak_tag, float(score)),
                            score_color
                        )
                    else:
                        pl.osd_img.draw_string_advanced(10, 160, 28, "now: ----", (100, 100, 100))

                    # 收集器状态
                    if collector is not None:
                        state_names = {0: "IDLE", 1: "COLL"}
                        sm_state_str = state_names.get(collector.state, "?")
                        pl.osd_img.draw_string_advanced(
                            10, 200, 18,
                            "[SM: {} f={}]".format(sm_state_str, collector.frame),
                            (180, 180, 180)
                        )

                        # 区间收集进度（基于帧计数）
                        bucket_len = len(collector.vote_bucket)
                        if collector.state == collector.STATE_COLLECTING:
                            no_card = collector.no_card_frames
                            pl.osd_img.draw_string_advanced(
                                10, 225, 18,
                                "Collect: {} fr  no_card:{}/{}".format(bucket_len, no_card, COLLECT_TIMEOUT),
                                (0, 200, 255)
                            )

                        # 调试信息
                        dbg_y = 250
                        pl.osd_img.draw_string_advanced(
                            10, dbg_y, 16,
                            "confirmed:{} ranks:{}".format(
                                "/".join(str(r) for r in list(collector.confirmed_cards)[-3:]),
                                "/".join(str(r) for r in list(collector.confirmed_ranks)[-3:])),
                            (150, 150, 150)
                        )

                    # 已记录列表
                    if record_list:
                        dbg_y2 = DISPLAY_HEIGHT - 60
                        for i, card_label in enumerate(record_list):
                            pl.osd_img.draw_string_advanced(
                                10, dbg_y2 - i * 20, 16,
                                "{}. {}".format(i + 1, card_label),
                                (255, 255, 0)
                            )

                    pl.osd_img.draw_string_advanced(
                        10, DISPLAY_HEIGHT - 15, 12,
                        "Frame:{} Rec:{}".format(frame_idx, len(record_list)),
                        (80, 80, 80)
                    )

                pl.show_image()
                gc.collect()
                continue

            elif state == 2:
                pl.osd_img.clear()
                total = len(record_list)
                if total == 0:
                    pl.osd_img.draw_string_advanced(
                        DISPLAY_WIDTH // 2 - 160, DISPLAY_HEIGHT // 2 - 20, 40,
                        "No cards detected", (255, 255, 255)
                    )
                else:
                    pl.osd_img.draw_string_advanced(
                        10, 10, 28,
                        "Total: {} card(s)".format(total), (255, 255, 255)
                    )

                    row_y = 50
                    line_height = 40
                    for i, card_label in enumerate(record_list):
                        disp, color = card_label_to_display(card_label)
                        pl.osd_img.draw_string_advanced(
                            10, row_y + i * line_height, 40,
                            "{}. {}".format(i + 1, disp),
                            color
                        )

                pl.osd_img.draw_string_advanced(
                    10, DISPLAY_HEIGHT - 30, 18,
                    "Press KEY on STM32 to restart", (200, 200, 200)
                )
                pl.show_image()
                gc.collect()
                continue

    except Exception as e:
        log_write("Exception: {}".format(e))
    finally:
        pl.destroy()
        nn.shrink_memory_pool()
        gc.collect()
        if _log_file:
            try:
                _log_file.write("=== Log Ended ===\n")
                _log_file.close()
            except Exception:
                pass

    log_write("End")


if __name__ == "__main__":
    main()
