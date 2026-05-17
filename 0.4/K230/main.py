"""
K230 扑克牌 YOLO 识别主程序（适配 CanMV K230 v1.6）
- 使用 PipeLine 框架管理双流 media buffer（通道0显示，通道2 AI）
- STM32 按下K2启动F题后，K230等待 0x01
- 收到 0x01 后开始记录识别结果

v14 算法（激进版：严格窗口 + 无牌防护 + 连续性要求）：
核心改变：
1. 窗口重置改为单向推进（不可清零重计），防止"积分游戏"
2. 提高 BLANK_GAP 到 0.30，大幅减少无牌误识别
3. 冷却期结束后重置 confirmed_cards 和 confirmed_ranks
4. 确认条件：Top1连续出现N帧才算确认（非窗口统计）
5. 参数：预热30帧/连续5帧确认/冷却20帧/BLANK_GAP=0.30
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

# ---- 分辨率配置 ----
DISPLAY_WIDTH = 640
DISPLAY_HEIGHT = 480
RGB888P_SIZE = [DISPLAY_WIDTH, DISPLAY_HEIGHT]

root_path = "/sdcard/mp_deployment_source/"
config_path = root_path + "deploy_config.json"

IMG_SIZE = (224, 224)

# ==================== 算法参数 ====================

# ---- 置信度过滤 ----
TOP_N = 3
MIN_CONFIDENCE = 0.45      # 提高最低门槛
TOP1_NOISE_THRESH = 0.55   # Top1 低于此值视为弱识别
TOP_GAP_THRESH = 0.12      # Top1 与 Top2 差距小于此值时降权
BLANK_GAP = 0.30           # Top1 必须比 BLANK 高至少此值（大幅提高防误识别）
WARMUP_FRAMES = 30         # 开机预热帧数

# ---- 稳定性确认配置 ----
CONSECUTIVE_THRESH = 5      # 同一张牌 Top1 连续出现 N 帧才确认
COOLDOWN_FRAMES = 20       # 确认后冷却期帧数
STRICT_MODE = True          # 严格模式：过滤后无候选时丢弃整帧

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


# ==================== 严格版稳定性确认状态机 ====================

class StabilityStateMachine:
    """
    v14 算法（连续帧确认 + 单向窗口 + 永久冷却排除）：

    核心思路：
    - 不再使用"窗口内统计多张牌"的方式
    - 改为追踪当前候选牌(X)的连续 Top1 出现次数
    - 只有当 X 连续出现 CONSECUTIVE_THRESH 帧时才确认
    - 出现不同牌时，候选重置为那张新牌，计数从0开始

    状态流转：
        TRACKING → (连续N帧同一牌) → CONFIRMED → COOLDOWN → TRACKING → ...

    关键改进：
    1. 连续性要求：打断即重置，防止"积分游戏"
    2. 永久冷却排除：confirmed_cards 在整次运行中持续生效
    3. 单向推进：无清零重计，每帧只累加
    4. 冷却后重置：冷却期结束后清空所有排除集合
    """

    STATE_IDLE = 0
    STATE_TRACKING = 1   # 追踪当前候选牌的连续帧数
    STATE_COOLDOWN = 2

    def __init__(self, labels):
        self.labels = labels
        self.state = self.STATE_IDLE
        self.frame = 0

        # 已确认的牌（整次运行永久排除）
        self.confirmed_cards = set()
        # 已确认的 rank（冷却期后清空）
        self.confirmed_ranks = set()

        # TRACKING 状态追踪
        self.current_candidate_idx = -1      # 当前候选牌 idx
        self.current_candidate_consec = 0   # 连续出现次数
        self.seen_any_card = False          # 是否见过有效牌

        # COOLDOWN 状态数据
        self.cooldown_end_frame = 0

    def reset(self):
        self.state = self.STATE_TRACKING
        self.frame = 0
        self.confirmed_cards = set()
        self.confirmed_ranks = set()
        self.current_candidate_idx = -1
        self.current_candidate_consec = 0
        self.seen_any_card = False
        self.cooldown_end_frame = 0

    def _get_rank(self, label):
        if label.startswith("JOKER"):
            return label
        if "_" in label:
            return label.split("_", 1)[1]
        return label

    def _is_valid_candidate(self, idx, label):
        """检查 idx/label 是否可作为候选"""
        if idx in self.confirmed_cards:
            return False
        rank = self._get_rank(label)
        if rank in self.confirmed_ranks:
            return False
        return True

    def tick(self, cls_idx, top_preds, softmax_res):
        """
        每帧调用。
        cls_idx: 本帧 Recognizer.run() 返回的 cls_idx（-1表示无效帧）
        top_preds: [(idx, label, score), ...]
        返回: (action, card_idx)
        """
        self.frame += 1

        # 预热期：跳过但不进入追踪
        if self.frame <= WARMUP_FRAMES:
            return "none", -1

        if self.state == self.STATE_IDLE:
            return "none", -1

        # ---- COOLDOWN ----
        if self.state == self.STATE_COOLDOWN:
            if self.frame >= self.cooldown_end_frame:
                # 冷却期结束，重置所有状态（重新开始追踪）
                self.state = self.STATE_TRACKING
                self.confirmed_cards = set()
                self.confirmed_ranks = set()
                self.current_candidate_idx = -1
                self.current_candidate_consec = 0
                self.seen_any_card = False
                self.cooldown_end_frame = 0
            return "none", -1

        # ---- TRACKING ----
        if self.state == self.STATE_TRACKING:
            if cls_idx < 0:
                # 无效帧：当前候选的连续计数冻结（不重置）
                return "none", -1

            label = self.labels[cls_idx]

            # 检查是否应该切换候选
            if self._is_valid_candidate(cls_idx, label):
                if cls_idx == self.current_candidate_idx:
                    # 同一张牌，累加连续计数
                    self.current_candidate_consec += 1
                    self.seen_any_card = True

                    # 连续 N 帧达到阈值 → 确认！
                    if self.current_candidate_consec >= CONSECUTIVE_THRESH:
                        self.confirmed_cards.add(cls_idx)
                        self.confirmed_ranks.add(self._get_rank(label))
                        self.state = self.STATE_COOLDOWN
                        self.cooldown_end_frame = self.frame + COOLDOWN_FRAMES
                        return "confirm", cls_idx
                else:
                    # 换了一张不同的牌（idx不同 或 被过滤后换了）
                    # 注意：这里 cls_idx 是 Recognizer 返回的，但可能已被 _filter 替换
                    # 由于我们传的是 cls_idx（原始），需要检查它是否有效
                    self.current_candidate_idx = cls_idx
                    self.current_candidate_consec = 1
                    self.seen_any_card = True
            else:
                # 当前 cls_idx 已被排除（confirmed），查找下一个有效候选
                filtered = [
                    (i, l) for i, l, s in top_preds
                    if self._is_valid_candidate(i, l)
                ]
                if filtered:
                    # 有有效候选：从过滤后的 Top1 开始追踪
                    new_idx, new_label = filtered[0]
                    if new_idx == self.current_candidate_idx:
                        self.current_candidate_consec += 1
                    else:
                        self.current_candidate_idx = new_idx
                        self.current_candidate_consec = 1
                    self.seen_any_card = True

                    if self.current_candidate_consec >= CONSECUTIVE_THRESH:
                        self.confirmed_cards.add(new_idx)
                        self.confirmed_ranks.add(self._get_rank(new_label))
                        self.state = self.STATE_COOLDOWN
                        self.cooldown_end_frame = self.frame + COOLDOWN_FRAMES
                        return "confirm", new_idx
                else:
                    # 无有效候选：冻结当前候选（不重置连续计数）
                    pass

            return "none", -1

        return "none", -1

    def get_current_best(self):
        """返回当前追踪的候选（用于OSD）"""
        if self.state == self.STATE_TRACKING and self.current_candidate_idx >= 0:
            return self.current_candidate_idx, self.current_candidate_consec
        return -1, 0

    def get_debug_info(self):
        """返回调试信息"""
        return {
            "state": self.state,
            "frame": self.frame,
            "candidate": self.current_candidate_idx,
            "consec": self.current_candidate_consec,
            "confirmed_cards": list(self.confirmed_cards),
            "confirmed_ranks": list(self.confirmed_ranks),
        }


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

            # 第二关：Top1 必须明显高于 BLANK
            blank_score = float(softmax_res[BLANK_IDX])
            if top1_score - blank_score < BLANK_GAP:
                del results
                gc.collect()
                return cls_idx, score, top_preds, -1, 0.0, is_weak, softmax_res

            # 第三关：Top1 必须超过最低置信度
            if top1_score < MIN_CONFIDENCE:
                del results
                gc.collect()
                return cls_idx, score, top_preds, -1, 0.0, is_weak, softmax_res

            # 第四关：Top1 与 Top2 差距不够大时降权
            adjusted_score = top1_score
            if len(top_preds) >= 2:
                gap = top1_score - top_preds[1][2]
                if gap < TOP_GAP_THRESH and top1_score < 0.65:
                    adjusted_score = top1_score * 0.7

            # 降权后仍需超过最低置信度
            if adjusted_score >= MIN_CONFIDENCE:
                cls_idx = top1_idx
                score = adjusted_score

            # 弱识别标记
            if top1_score < TOP1_NOISE_THRESH:
                is_weak = True

        del results
        gc.collect()
        return cls_idx, score, top_preds, cls_idx, score, is_weak, softmax_res


# ==================== 主程序 ====================

def main():
    pl = PipeLine(
        rgb888p_size=RGB888P_SIZE,
        display_mode="lcd",
        display_size=[DISPLAY_WIDTH, DISPLAY_HEIGHT],
        osd_layer_num=2,
        debug_mode=0
    )
    pl.create()

    print("Pipeline OK")

    print("Loading kmodel...")
    recognizer = Recognizer()

    print("Recognizer OK")

    print("Init UART...")
    from ybUtils.YbUart import YbUart
    uart = YbUart()
    uart.send(b'\xA0')

    print("K230 Poker v14 | CONS={} CD={} | CONF={:.2f} BG={:.2f} WU={}".format(
        CONSECUTIVE_THRESH, COOLDOWN_FRAMES,
        MIN_CONFIDENCE, BLANK_GAP, WARMUP_FRAMES))

    state = 0  # 0=待机, 1=识别中, 2=展示结果
    record_list = []
    frame_idx = 0
    state_machine = None

    fps = 0
    fps_start = 0
    fps_count = 0

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
                            frame_idx = 0
                            state_machine = StabilityStateMachine(recognizer.labels)
                            state_machine.reset()
                            state = 1
                            uart.send(b'\xA1')
                            print("START")
                        elif b == 0x02:
                            state = 2
                            uart.send(b'\xA2')
                            print("STOP:", record_list)
            except Exception as e:
                print("UART err:", e)

            if state == 0 or state == 1:
                frame_idx += 1
                frame = pl.get_frame()

                cls_idx, score, top_preds, adj_idx, adj_score, is_weak, softmax_res = recognizer.run(frame)

                # ---- 状态机驱动 ----
                action = "none"
                confirmed_idx = -1
                if state_machine is not None:
                    action, confirmed_idx = state_machine.tick(cls_idx, top_preds, softmax_res)

                    if action == "confirm":
                        confirmed_label = recognizer.labels[confirmed_idx]
                        record_list.append(confirmed_label)
                        print(">>> CONFIRMED: {} (idx={})".format(confirmed_label, confirmed_idx))

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

                    # 状态机状态
                    if state_machine is not None:
                        sm_names = {0: "IDLE", 1: "TRACK", 2: "COOL"}
                        sm_state_str = sm_names.get(state_machine.state, "?")
                        pl.osd_img.draw_string_advanced(
                            10, 200, 18,
                            "[SM: {} f={}]".format(sm_state_str, state_machine.frame),
                            (180, 180, 180)
                        )

                        # 当前追踪的候选
                        best_idx, consec = state_machine.get_current_best()
                        if best_idx >= 0 and consec > 0:
                            bar_len = min(consec * 6, 100)
                            bar = "#" * (bar_len // 2)
                            pl.osd_img.draw_string_advanced(
                                10, 225, 18,
                                "Tracking: {} [{}{}/{}]".format(
                                    recognizer.labels[best_idx], bar, consec, CONSECUTIVE_THRESH),
                                (0, 200, 255)
                            )

                        # 调试信息
                        dbg = state_machine.get_debug_info()
                        dbg_y = 250
                        pl.osd_img.draw_string_advanced(
                            10, dbg_y, 16,
                            "cand:{} consec:{} rank:{}".format(
                                best_idx, consec,
                                "/".join(str(r) for r in dbg["confirmed_ranks"][-3:])),
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
        print("Exception:", e)
    finally:
        pl.destroy()
        nn.shrink_memory_pool()
        gc.collect()

    print("End")


if __name__ == "__main__":
    main()
