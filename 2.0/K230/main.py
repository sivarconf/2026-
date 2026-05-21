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

算法版本：v16（滑动窗口投票 + 永久冷却排除）
- 滑动窗口投票（5帧窗口，3帧确认）
- 永久冷却排除（confirmed_cards/ranks整次运行有效）
- 冷却期候选积累（冷却期内仍允许有效帧加入 vote_window）
- 开机预热30帧 + 冷却期20帧
- BLANK分差门槛0.30防误识别

v3小王优化（从v28/v29移植）：
- S_A ↔ JOKER 混淆修正（分差<0.25时交换）
- JOKER_B Boost 机制（分值>=0.10时×1.3放大）

版本：2.0
日期：2026-05-17
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
MIN_CONFIDENCE = 0.35      # v16：提高最低门槛
TOP1_NOISE_THRESH = 0.45   # Top1 低于此值视为弱识别
TOP_GAP_THRESH = 0.12      # Top1 与 Top2 差距小于此值时降权
BLANK_GAP = 0.30           # Top1 必须比 BLANK 高至少此值（大幅提高防误识别）
WARMUP_FRAMES = 10         # 开机预热帧数

# ---- 稳定性确认配置 ----
WINDOW_SIZE = 5             # 滑动窗口大小
VOTE_THRESH = 3              # 窗口内同一牌达到 N 次即确认
COOLDOWN_FRAMES = 15        # 确认后冷却期帧数
STRICT_MODE = True          # 严格模式：过滤后无候选时丢弃整帧

# ---- 小王处理（v28/v29/v30）----
JOKER_GAP_THRESH = 0.25     # S_A 与 JOKER 的分差阈值，超过此值才交换
JOKER_B_DETECT_MIN = 0.10   # JOKER_B 进入 Boost 的最低分值
JOKER_B_BOOST_MULT = 1.3    # JOKER_B 分值 Boost 系数（温和放大）
JOKER_B_MAX_GAP = 0.25      # JOKER_B Boost 后与 Top1 的最大允许差距

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


# ==================== v16 稳定性确认状态机（回退至1.0算法）====================

class StabilityStateMachine:
    """
    滑动窗口投票 + 永久冷却排除：

    核心思路：
    - 滑动窗口投票：记录最近5帧识别结果，窗口内同一牌达到3次即确认
    - 永久冷却排除：confirmed_cards/ranks 在整次运行中持续生效
    - 冷却期候选积累：冷却期内仍允许有效帧加入 vote_window

    状态流转：
        WARMUP → TRACKING → (窗口投票通过) → COOLDOWN → TRACKING → ...

    滑动窗口逻辑（允许跳帧）：
    1. 维护一个 vote_window 列表，记录最近5帧的有效 Top1 idx
    2. 每帧先将新 idx 入队，再弹出队首（保持窗口大小为5）
    3. 统计窗口内各 idx 的出现次数，达到 VOTE_THRESH(3) 次即确认
    4. 窗口内不同牌互不干扰，各自独立计数
    """

    STATE_IDLE = 0
    STATE_TRACKING = 1
    STATE_COOLDOWN = 2

    def __init__(self, labels):
        self.labels = labels
        self.state = self.STATE_IDLE
        self.frame = 0

        # 已确认的牌（整次运行永久排除）
        self.confirmed_cards = set()
        # 已确认的 rank（冷却期后清空）
        self.confirmed_ranks = set()

        # TRACKING 滑动窗口数据
        self.vote_window = []       # 最近 WINDOW_SIZE 帧的 (idx, score) 元组
        self.vote_counts = {}       # 各 idx 的出现次数
        self.vote_scores = {}        # 各 idx 的置信度总和（用于平票裁决）

        # COOLDOWN 状态数据
        self.cooldown_end_frame = 0

    def reset(self):
        self.state = self.STATE_TRACKING
        self.frame = 0
        self.confirmed_cards = set()
        self.confirmed_ranks = set()
        self.vote_window = []
        self.vote_counts = {}
        self.vote_scores = {}
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

    def _get_filtered_top1(self, top_preds):
        """从 top_preds 中找到第一个有效的候选 idx"""
        for i, l, s in top_preds:
            if self._is_valid_candidate(i, l):
                return i, l
        return -1, None

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
                # 冷却期结束，重置追踪状态，但保留已确认牌的排除记录
                # 已确认的牌（confirmed_cards/confirmed_ranks）在整次运行中永久生效
                self.state = self.STATE_TRACKING
                self.vote_window = []
                self.vote_counts = {}
                self.cooldown_end_frame = 0
            return "none", -1

        # ---- TRACKING ----
        if self.state == self.STATE_TRACKING:
            if cls_idx < 0:
                # 无效帧：本帧不加入窗口（冻结当前窗口，不清空）
                return "none", -1

            # 获取过滤后的 Top1（排除已确认牌）
            filtered_idx, filtered_label = self._get_filtered_top1(top_preds)
            if filtered_idx < 0:
                # 无有效候选：本帧不加入窗口
                return "none", -1

            # 获取 Top1 的置信度（用于平票裁决）
            filtered_score = 0.0
            for i, l, s in top_preds:
                if i == filtered_idx:
                    filtered_score = float(s)
                    break

            # 入队：移除旧帧的计数，加入新帧
            if len(self.vote_window) >= WINDOW_SIZE:
                old_idx, old_score = self.vote_window[0]
                self.vote_counts[old_idx] = self.vote_counts.get(old_idx, 1) - 1
                self.vote_scores[old_idx] = self.vote_scores.get(old_idx, 0.0) - old_score
                if self.vote_counts[old_idx] <= 0:
                    del self.vote_counts[old_idx]
                    if old_idx in self.vote_scores:
                        del self.vote_scores[old_idx]
                self.vote_window.pop(0)

            self.vote_window.append((filtered_idx, filtered_score))
            self.vote_counts[filtered_idx] = self.vote_counts.get(filtered_idx, 0) + 1
            self.vote_scores[filtered_idx] = self.vote_scores.get(filtered_idx, 0.0) + filtered_score

            # 检查是否有牌达到投票阈值，多牌同时达到时选置信度最高者
            confirmed = None
            for idx, cnt in list(self.vote_counts.items()):
                if cnt >= VOTE_THRESH:
                    if confirmed is None:
                        confirmed = (idx, cnt)
                    else:
                        # 票数相同则比较置信度总和
                        cur_score = self.vote_scores.get(idx, 0.0)
                        best_score = self.vote_scores.get(confirmed[0], 0.0)
                        if cnt > confirmed[1] or (cnt == confirmed[1] and cur_score > best_score):
                            confirmed = (idx, cnt)

            if confirmed is not None:
                idx = confirmed[0]
                label = self.labels[idx]
                self.confirmed_cards.add(idx)
                self.confirmed_ranks.add(self._get_rank(label))
                self.state = self.STATE_COOLDOWN
                self.cooldown_end_frame = self.frame + COOLDOWN_FRAMES
                # 确认后清空窗口，避免同一帧多次确认
                self.vote_window = []
                self.vote_counts = {}
                self.vote_scores = {}
                return "confirm", idx

            return "none", -1

        return "none", -1

    def get_current_best(self):
        """返回窗口内票数最高的候选（用于OSD）"""
        if self.state == self.STATE_TRACKING and self.vote_counts:
            best_idx = max(self.vote_counts, key=self.vote_counts.get)
            best_cnt = self.vote_counts[best_idx]
            return best_idx, best_cnt
        return -1, 0

    def get_debug_info(self):
        """返回调试信息"""
        return {
            "state": self.state,
            "frame": self.frame,
            "window": [idx for idx, _ in self.vote_window],
            "counts": dict(self.vote_counts),
            "scores": {str(k): round(v, 2) for k, v in self.vote_scores.items()},
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

        # v28 新增：缓存 S_A / JOKER 索引（避免每帧遍历）
        self.S_A_idx = None
        self.JOKER_B_idx = None
        self.JOKER_R_idx = None
        for i, lbl in enumerate(self.labels):
            if lbl == "S_A":
                self.S_A_idx = i
            elif lbl == "JOKER_B":
                self.JOKER_B_idx = i
            elif lbl == "JOKER_R":
                self.JOKER_R_idx = i

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

        # 先取一次 top_preds，用于判断 Top1 是否为 S_A
        top_preds = get_top_predictions(softmax_res, self.labels, TOP_N)

        # ---- v28 新增：S_A ↔ JOKER_* 混淆修正（关键修复）----
        # 实测：小王(JOKER_B) 经常被误识别为 S_A(黑桃A)，大王(JOKER_R) 也可能被误识别
        # 修正策略：Top1=S_A 且 S_A-JOKER 分差 < JOKER_GAP_THRESH 时，交换两者 softmax 分值
        if self.S_A_idx is not None:
            S_A_score = float(softmax_res[self.S_A_idx])
            if self.JOKER_B_idx is not None:
                J_B_score = float(softmax_res[self.JOKER_B_idx])
                if len(top_preds) > 0 and top_preds[0][0] == self.S_A_idx:
                    if S_A_score - J_B_score < JOKER_GAP_THRESH:
                        softmax_res[self.S_A_idx] = J_B_score
                        softmax_res[self.JOKER_B_idx] = S_A_score
            if self.JOKER_R_idx is not None:
                J_R_score = float(softmax_res[self.JOKER_R_idx])
                if len(top_preds) > 0 and top_preds[0][0] == self.S_A_idx:
                    if S_A_score - J_R_score < JOKER_GAP_THRESH:
                        softmax_res[self.S_A_idx] = J_R_score
                        softmax_res[self.JOKER_R_idx] = S_A_score

        # ---- v29 新增：JOKER_B 识别 Boost（关键修复）----
        # 根因：JOKER_B 自身 softmax 分值过低，即使出现在帧中也无法通过识别阈值
        # 策略：
        #   1. JOKER_B 分值 >= JOKER_B_DETECT_MIN 才进入 Boost
        #   2. Boost 后 JOKER_B 必须仍与 Top1 保持 JOKER_B_MAX_GAP 内才有效
        #   3. Boost 系数从 1.5 改为 1.3（温和放大，减少误推普通牌）
        if self.JOKER_B_idx is not None:
            jb_score = float(softmax_res[self.JOKER_B_idx])
            if jb_score >= JOKER_B_DETECT_MIN:
                top1_val = float(softmax_res[top_preds[0][0]]) if len(top_preds) > 0 else 0.0
                boosted_jb = jb_score * JOKER_B_BOOST_MULT
                # Boost 后 JOKER_B 与 Top1 的差距必须在 JOKER_B_MAX_GAP 内才生效
                if top1_val - boosted_jb < JOKER_B_MAX_GAP:
                    softmax_res[self.JOKER_B_idx] = boosted_jb

        # 交换后重新排序
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

    print("K230 Poker v2.0 | WIN={} VOTE={} CD={} | CONF={:.2f} BG={:.2f} WU={}".format(
        WINDOW_SIZE, VOTE_THRESH, COOLDOWN_FRAMES,
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
                        best_idx, best_cnt = state_machine.get_current_best()
                        if best_idx >= 0 and best_cnt > 0:
                            bar_len = min(best_cnt * 5, 100)
                            bar = "#" * bar_len
                            pl.osd_img.draw_string_advanced(
                                10, 225, 18,
                                "Vote: {} [{}{}/{}]".format(
                                    recognizer.labels[best_idx], bar, best_cnt, VOTE_THRESH),
                                (0, 200, 255)
                            )

                        # 调试信息
                        dbg = state_machine.get_debug_info()
                        dbg_y = 250
                        pl.osd_img.draw_string_advanced(
                            10, dbg_y, 16,
                            "win:{} cnt:{}".format(
                                str(dbg["window"])[1:-1],
                                str(dbg["counts"])[1:-1]),
                            (150, 150, 150)
                        )
                        pl.osd_img.draw_string_advanced(
                            10, dbg_y + 18, 16,
                            "sco:{}".format(str(dbg["scores"])[1:-1]),
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
