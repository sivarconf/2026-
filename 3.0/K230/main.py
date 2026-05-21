"""
K230 扑克牌识别主程序
适配 CanMV K230 v1.6 + PipeLine 框架

功能概述：
- 双流 media buffer（通道0显示，通道2 AI推理）
- 55类分类（BLANK背景 + 54种扑克牌）
- 加权投票 + 冷却期防误识别
- 每轮识别生成独立日志文件

算法版本：v22（回顾一致性检查：S_10 vs C_8 覆写）
- 检测到有效牌后废弃前2帧（高置信度>=0.7可跳过废弃）
- 废弃后进入 VOTING，每帧计算权重 = min(1.0, 2 × (Top1 - Top2))
- 7帧全部收集（或实时达到门槛3.0），加权票数最高者确认
- 确认后15帧纯冷却，冷却期内所有帧丢弃
- 冷却期结束后重新进入TRACKING，等待下一张牌
- 日志：每轮识别生成一个文件，记录从START到STOP的完整过程

版本：3.0
日期：2026-05-21
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
LOG_DIR = "/data/data/logs/"

IMG_SIZE = (224, 224)

# ==================== 算法参数 ====================

# ---- 置信度过滤 ----
TOP_N = 3
MIN_CONFIDENCE = 0.25
TOP1_NOISE_THRESH = 0.55
TOP_GAP_THRESH = 0.12
BLANK_GAP = 0.30
WARMUP_FRAMES = 10

# ---- 加权投票配置（v20）----
DISCARD_FRAMES = 2          # 检测到牌后废弃的前2帧
DISCARD_SKIP_CONFIDENCE = 0.70  # 废弃期内置信度达到此值则跳过废弃
WINDOW_SIZE = 15             # 有效观察窗口大小
VOTE_WEIGHT_THRESH = 4.0    # 加权票数门槛，达到此值即确认
WEIGHT_MULTIPLIER = 2.0    # 权重乘数：weight = min(1.0, multiplier * gap)
COOLDOWN_FRAMES = 15        # 确认后冷却期帧数

# ---- 小王处理 ----
JOKER_GAP_THRESH = 0.25
JOKER_B_DETECT_MIN = 0.10
JOKER_B_BOOST_MULT = 1.3
JOKER_B_MAX_GAP = 0.25

# ---- C_8 回顾一致性检查（S_10 vs C_8 覆写）----
RETRO_C8_ENABLED = True           # 是否启用回顾检查
RETRO_C8_TARGET = "C_8"           # 覆写目标
RETRO_TRIGGER = "S_10"            # 触发词：确认结果为 S_10 时才检查
RETRO_COND_A_RATIO = 0.85         # 条件 A：S_10 top1 占比低于此值
RETRO_COND_B_SCORE = 0.05         # 条件 B：C_8 分数高于此值才算有效
RETRO_COND_B_FRAMES = 3           # 条件 B：C_8 有效帧数需达到此值
RETRO_COND_C_SCORE = 0.70         # 条件 C：S_10 分数高于此值才算高置信
RETRO_COND_C_RATIO = 0.50        # 条件 C：高置信帧占比低于此值才触发

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


BLANK_IDX = 0


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


# ==================== 日志系统（每帧记录 v22） ====================

class RecognitionLogger:
    """
    整个会话共用一个文件，记录每一帧的原始推理数据。
    保存路径：/data/yyyymmdd_HHMMSS.log
    """

    def __init__(self, log_dir=LOG_DIR):
        self.log_dir = log_dir
        self.log_file = None
        self.is_logging = False
        self.session_start_ms = 0
        self.round_num = 0
        self.frame_in_round = 0
        self._session_seq = 0

    def _write(self, s):
        if self.log_file is None:
            return
        try:
            self.log_file.write(s)
            self.log_file.flush()
        except Exception:
            pass

    def start_session(self):
        """开始会话：创建单个文件"""
        self.is_logging = True
        self.session_start_ms = ticks_ms()
        self.round_num = 0

        try:
            if not os.exists(self.log_dir):
                os.mkdir(self.log_dir)
        except Exception:
            pass

        self._session_seq += 1
        seq_str = "{:04d}".format(self._session_seq)

        filepath = self.log_dir + "/" + seq_str + ".log"
        try:
            self.log_file = open(filepath, 'w')
            self._write(
                "=" * 70 + "\n"
                "SESSION START  {}\n".format(seq_str) +
                "=" * 70 + "\n"
                "algo_version  : v22\n"
                "params        : DISCARD={} SKIP={:.2f} WIN={} WTH={:.1f} CD={} WARMUP={}\n".format(
                    DISCARD_FRAMES, DISCARD_SKIP_CONFIDENCE,
                    WINDOW_SIZE, VOTE_WEIGHT_THRESH, COOLDOWN_FRAMES, WARMUP_FRAMES) +
                "-" * 70 + "\n"
            )
        except Exception:
            self.log_file = None

    def log_frame(self, frame_idx, cls_idx, top_preds,
                   sm_state, sm_frame,
                   sm_best_idx, sm_best_weight,
                   sm_voting_collected,
                   sm_confirmed_cards,
                   action, confirmed_label, labels):
        """记录一帧的完整数据"""
        if not self.is_logging:
            return

        elapsed_s = (ticks_ms() - self.session_start_ms) / 1000.0

        state_map = {0: "IDLE", 1: "TRACK", 2: "DISC", 3: "VOTE", 4: "COOL"}
        sm_name = state_map.get(sm_state, "?")

        top_str = "  ".join(
            "{}:{:.4f}".format(l, float(s)) for _, l, s in top_preds[:5]
        )
        best_lbl = labels[sm_best_idx] if sm_best_idx >= 0 else "-"
        best_w = round(sm_best_weight, 3) if sm_best_idx >= 0 else 0
        confirmed_str = ",".join(str(c) for c in sm_confirmed_cards) if sm_confirmed_cards else "-"

        line = (
            "[{:8.3f}s] f={:05d}  sm={}({})  "
            "top=[{}]  "
            "best={}:{:.3f}  vote={}/{}  "
            "confirmed=[{}]  "
            "action={}"
        ).format(
            elapsed_s, frame_idx,
            sm_name, sm_frame,
            top_str,
            best_lbl, best_w,
            sm_voting_collected, WINDOW_SIZE,
            confirmed_str,
            action
        )

        if confirmed_label:
            line += "  >>> CONFIRMED: {}".format(confirmed_label)

        self._write(line + "\n")

    def end_session(self, all_recorded_cards):
        """结束会话：写入汇总并关闭文件"""
        self.is_logging = False
        elapsed = ticks_ms() - self.session_start_ms
        total = " -> ".join(str(c) for c in all_recorded_cards) if all_recorded_cards else "(none)"
        self._write(
            "\n" + "=" * 70 + "\n"
            "SESSION END\n"
            "=" * 70 + "\n"
            "total_rounds : {}\n"
            "confirmed    : {}\n"
            "duration     : {:.3f}s ({:.1f}min)\n".format(
                self.round_num, total, elapsed / 1000.0, elapsed / 60000.0) +
            "=" * 70 + "\n"
        )
        if self.log_file is not None:
            try:
                self.log_file.close()
            except Exception:
                pass
            self.log_file = None


# ==================== v22 加权投票状态机 ====================

class StabilityStateMachine:
    """
    加权投票 + 2帧废弃期 + 永久冷却排除 + 回顾一致性检查：

    核心思路：
    - 检测到有效牌 → 废弃前2帧（高置信度>=0.7可跳过废弃）
    - 废弃后进入 VOTING，收集最多7帧进行加权投票
    - 每帧根据过滤后有效牌的 Top1/Top2 差距计算权重
    - 实时检查：每帧收集后立即检查是否达到门槛
    - 7帧用尽后：选出加权票数最高的牌
    - 日志回调：每帧记录详细投票数据

    权重公式：weight = min(1.0, WEIGHT_MULTIPLIER × (Top1 - Top2))
    - 只有一张有效牌时 weight = 0.5（说明其他牌都被过滤，不可靠）
    - gap 越大 → 权重越高 → 识别越可靠

    确认逻辑：
    - 实时确认：加权票数 >= 门槛 → 立即确认
    - 7帧用尽：选出加权票数最高的牌（即使没达到门槛）

    状态流转：
        TRACKING（等待检测到有效牌）
            ↓
        DISCARDING（废弃前2帧）
            ├─ 若置信度>=0.7 → 跳过废弃，直接进入 VOTING
            └─ 若置信度<0.7 → 丢弃，等待下一帧
            ↓（2帧后或高置信度跳过）
        VOTING（收集最多7帧）
            ├─ 某牌加权票数 >= 门槛 → CONFIRMED → COOLDOWN
            └─ 7帧用尽 → 选出最高分牌 → COOLDOWN
            ↓
        COOLDOWN（15帧纯冷却，所有帧丢弃）
            ↓
        COOLDOWN结束 → 回到 TRACKING，等待下一张牌

    回顾一致性检查（S_10 vs C_8）：
    - 当投票确认 S_10 后，回顾检测窗口内所有帧
    - 条件 A：S_10 当 top1 的比例 < RETRO_COND_A_RATIO
    - 条件 B：C_8 分数 > RETRO_COND_B_SCORE 出现在 Top3 中 >= RETRO_COND_B_FRAMES 帧
    - 条件 C：S_10 > RETRO_COND_C_SCORE 的高置信帧占比 < RETRO_COND_C_RATIO
    - 三个条件全满足 → 覆写为 C_8
    """

    STATE_IDLE = 0
    STATE_TRACKING = 1
    STATE_DISCARDING = 2
    STATE_VOTING = 3
    STATE_COOLDOWN = 4

    def __init__(self, labels, logger=None):
        self.labels = labels
        self.logger = logger
        self.state = self.STATE_IDLE
        self.frame = 0
        self.round_frame_idx = 0  # 从TRACKING开始的帧计数

        self.confirmed_cards = set()
        self.confirmed_ranks = set()
        self.round_confirmed_cards = []

        self.vote_weights = {}     # {idx: total_weight}
        self.vote_history = []      # [{idx, label, weight, frame}]

        self.cooldown_end_frame = 0
        self.voting_frames_collected = 0
        self.discard_frames_collected = 0
        self.round_total_frames = 0
        self.round_discard_count = 0

        self.C_8_idx = None
        for idx, lbl in enumerate(labels):
            if lbl == RETRO_C8_TARGET:
                self.C_8_idx = idx
                break

    def reset(self):
        self.state = self.STATE_TRACKING
        self.frame = 0
        self.round_frame_idx = 0
        self.confirmed_cards = set()
        self.confirmed_ranks = set()
        self.round_confirmed_cards = []
        self.vote_weights = {}
        self.vote_history = []
        self.cooldown_end_frame = 0
        self.voting_frames_collected = 0
        self.discard_frames_collected = 0
        self.round_total_frames = 0
        self.round_discard_count = 0

    def _get_rank(self, label):
        if label.startswith("JOKER"):
            return label
        if "_" in label:
            return label.split("_", 1)[1]
        return label

    def _is_valid_candidate(self, idx, label):
        if idx in self.confirmed_cards:
            return False
        rank = self._get_rank(label)
        if rank in self.confirmed_ranks:
            return False
        return True

    def _get_valid_candidates(self, top_preds):
        """返回过滤后的有效候选列表 [(idx, label, score), ...]"""
        result = []
        for i, l, s in top_preds:
            if self._is_valid_candidate(i, l):
                result.append((i, l, s))
        return result

    def _calc_frame_weight(self, valid_candidates):
        """根据过滤后的有效候选列表计算权重"""
        if len(valid_candidates) == 0:
            return 0.0
        if len(valid_candidates) == 1:
            return 0.5
        top1_score = float(valid_candidates[0][2])
        top2_score = float(valid_candidates[1][2])
        gap = top1_score - top2_score
        weight = min(1.0, WEIGHT_MULTIPLIER * gap)
        return weight

    def _get_vote_weights_str(self):
        """生成投票权重的可读字符串"""
        lines = []
        for idx, w in sorted(self.vote_weights.items(), key=lambda x: -x[1]):
            lbl = self.labels.get(idx, "unknown") if isinstance(self.labels, dict) else self.labels[idx]
            lines.append("{}: {:.4f}".format(lbl, w))
        return "\n".join(lines) if lines else "(empty)"

    def _get_vote_history_str(self):
        """生成投票历史的可读字符串"""
        lines = []
        for vh in self.vote_history:
            lines.append("frame={} {} weight={:.4f}".format(
                vh["frame"], vh["label"], vh["weight"]))
        return "\n".join(lines) if lines else "(empty)"

    def _retrospective_s10_vs_c8_check(self, confirmed_idx, history):
        """
        回顾一致性检查：当确认 S_10 后，回顾窗口内所有帧，判断是否是 C_8 误识别。
        三个条件全满足 -> 覆写为 C_8。
        条件 A：S_10 当 top1 的比例 < RETRO_COND_A_RATIO
        条件 B：C_8 分数 > RETRO_COND_B_SCORE 出现在 Top3 中出现过 >= RETRO_COND_B_FRAMES 帧
        条件 C：S_10 > RETRO_COND_C_SCORE 的高置信帧占比 < RETRO_COND_C_RATIO
        """
        if not RETRO_C8_ENABLED:
            return False
        if self.C_8_idx is None:
            return False

        confirmed_label = self.labels[confirmed_idx] if isinstance(self.labels, dict) else self.labels[confirmed_idx]
        if confirmed_label != RETRO_TRIGGER:
            return False

        if not history:
            return False

        total_frames = len(history)
        if total_frames == 0:
            return False

        s10_top1_count = sum(1 for v in history if v["idx"] == confirmed_idx)
        s10_top1_ratio = s10_top1_count / total_frames

        c8_above_thresh = 0
        for v in history:
            top3 = v.get("top3", [])
            for ti, tl, ts in top3:
                if ti == self.C_8_idx and ts > RETRO_COND_B_SCORE:
                    c8_above_thresh += 1
                    break

        s10_high_conf = sum(
            1 for v in history if v["idx"] == confirmed_idx and v.get("score", 0) > RETRO_COND_C_SCORE
        )
        s10_high_conf_ratio = s10_high_conf / max(1, total_frames)

        cond_A = s10_top1_ratio < RETRO_COND_A_RATIO
        cond_B = c8_above_thresh >= RETRO_COND_B_FRAMES
        cond_C = s10_high_conf_ratio < RETRO_COND_C_RATIO

        if self.logger is not None and hasattr(self.logger, '_write'):
            self.logger._write(
                "[RetrospectiveCheck] {} confirmed, window={} frames | "
                "A(top1 ratio={:.2f}<{:.2f}? {}) "
                "B(C_8>{:.2f} count={}>={}? {}) "
                "C(S_10>{:.2f} ratio={:.2f}<{:.2f}? {}) "
                "-> override={}\n".format(
                    RETRO_TRIGGER, total_frames,
                    s10_top1_ratio, RETRO_COND_A_RATIO, cond_A,
                    RETRO_COND_B_SCORE, c8_above_thresh, RETRO_COND_B_FRAMES, cond_B,
                    RETRO_COND_C_SCORE, s10_high_conf_ratio, RETRO_COND_C_RATIO, cond_C,
                    cond_A and cond_B and cond_C
                )
            )

        return cond_A and cond_B and cond_C

    def _tick_confirm(self, idx, raw_top_preds, valid_candidates,
                      filtered_idx, filtered_label, filtered_score,
                      weight):
        label = self.labels[idx] if isinstance(self.labels, dict) else self.labels[idx]
        self.confirmed_cards.add(idx)
        self.confirmed_ranks.add(self._get_rank(label))
        self.state = self.STATE_COOLDOWN
        self.cooldown_end_frame = self.frame + COOLDOWN_FRAMES

        saved_history = list(self.vote_history)
        saved_voting = self.voting_frames_collected
        saved_discard = self.round_discard_count
        saved_total = self.round_total_frames

        self.vote_weights = {}
        self.vote_history = []
        self.voting_frames_collected = 0
        self.discard_frames_collected = 0
        self.round_total_frames = 0
        self.round_discard_count = 0

        should_override = self._retrospective_s10_vs_c8_check(idx, saved_history)

        return "confirm", idx, saved_history, saved_voting, saved_discard, saved_total, should_override

    def _tick_finalize(self, raw_top_preds, valid_candidates,
                        filtered_idx, filtered_label, filtered_score, weight):
        """7帧用尽，选出加权票数最高的牌"""
        if not self.vote_weights:
            self.state = self.STATE_COOLDOWN
            self.cooldown_end_frame = self.frame + COOLDOWN_FRAMES

            self.voting_frames_collected = 0
            self.discard_frames_collected = 0
            self.round_total_frames = 0
            self.round_discard_count = 0
            return "none", -1, [], 0, 0, 0, False

        best_idx = max(self.vote_weights, key=self.vote_weights.get)

        saved_history = list(self.vote_history)
        saved_voting = self.voting_frames_collected
        saved_discard = self.round_discard_count
        saved_total = self.round_total_frames

        label = self.labels[best_idx] if isinstance(self.labels, dict) else self.labels[best_idx]
        self.confirmed_cards.add(best_idx)
        self.confirmed_ranks.add(self._get_rank(label))
        self.state = self.STATE_COOLDOWN
        self.cooldown_end_frame = self.frame + COOLDOWN_FRAMES

        self.vote_weights = {}
        self.vote_history = []
        self.voting_frames_collected = 0
        self.discard_frames_collected = 0
        self.round_total_frames = 0
        self.round_discard_count = 0

        should_override = self._retrospective_s10_vs_c8_check(best_idx, saved_history)

        return "confirm", best_idx, saved_history, saved_voting, saved_discard, saved_total, should_override

    def tick(self, cls_idx, top_preds, softmax_res):
        """
        每帧调用。
        cls_idx: 本帧 Recognizer.run() 返回的 cls_idx（-1表示无效帧）
        top_preds: [(idx, label, score), ...]
        返回: (action, card_idx, vote_history, vote_count, discard_count, total_frames)
        """
        self.frame += 1
        self.round_frame_idx += 1

        if self.frame <= WARMUP_FRAMES:
            return "none", -1, [], 0, 0, 0, False

        if self.state == self.STATE_IDLE:
            return "none", -1, [], 0, 0, 0, False

        if self.state == self.STATE_COOLDOWN:
            if self.frame >= self.cooldown_end_frame:
                self.state = self.STATE_TRACKING
                self.vote_weights = {}
                self.vote_history = []
                self.cooldown_end_frame = 0
                self.voting_frames_collected = 0
                self.discard_frames_collected = 0
                self.round_total_frames = 0
                self.round_discard_count = 0
                self.round_frame_idx = 0
            return "none", -1, [], 0, 0, 0, False

        if self.state == self.STATE_TRACKING:
            if cls_idx < 0:
                return "none", -1, [], 0, 0, 0, False

            valid = self._get_valid_candidates(top_preds)
            if not valid:
                return "none", -1, [], 0, 0, 0, False

            filtered_idx = valid[0][0]
            filtered_label = valid[0][1]
            filtered_score = valid[0][2]

            if filtered_score >= DISCARD_SKIP_CONFIDENCE:
                weight = self._calc_frame_weight(valid)
                self.state = self.STATE_VOTING
                self.vote_weights = {filtered_idx: weight}
                self.vote_history = [{
                    "frame": self.round_frame_idx,
                    "idx": filtered_idx,
                    "label": filtered_label,
                    "score": float(filtered_score),
                    "weight": weight,
                    "gap": weight if len(valid) < 2 else float(valid[0][2]) - float(valid[1][2]),
                    "valid_count": len(valid),
                    "top3": [(vi, vl, float(vs)) for vi, vl, vs in valid[:3]]
                }]
                self.voting_frames_collected = 1
                self.round_total_frames = 1
                self.round_discard_count = 0

                if weight >= VOTE_WEIGHT_THRESH:
                    return self._tick_confirm(
                        filtered_idx, top_preds, valid,
                        filtered_idx, filtered_label, filtered_score, weight)

                return "none", -1, [], 0, 0, 0, False
            else:
                self.state = self.STATE_DISCARDING
                self.round_total_frames = 1
                self.round_discard_count = 1

                return "none", -1, [], 0, 0, 0, False

        if self.state == self.STATE_DISCARDING:
            self.round_total_frames += 1

            if self.round_total_frames > DISCARD_FRAMES + WINDOW_SIZE:
                self.state = self.STATE_VOTING
                self.vote_weights = {}
                self.vote_history = []
                self.voting_frames_collected = 0
                self.discard_frames_collected = 0
                return "none", -1, [], 0, self.round_discard_count, self.round_total_frames, False

            if cls_idx < 0:
                return "none", -1, [], 0, self.round_discard_count, self.round_total_frames, False

            valid = self._get_valid_candidates(top_preds)
            if not valid:
                return "none", -1, [], 0, self.round_discard_count, self.round_total_frames, False

            filtered_idx = valid[0][0]
            filtered_label = valid[0][1]
            filtered_score = valid[0][2]

            if filtered_score >= DISCARD_SKIP_CONFIDENCE:
                weight = self._calc_frame_weight(valid)
                self.state = self.STATE_VOTING
                self.vote_weights = {filtered_idx: weight}
                self.vote_history = [{
                    "frame": self.round_frame_idx,
                    "idx": filtered_idx,
                    "label": filtered_label,
                    "score": float(filtered_score),
                    "weight": weight,
                    "gap": weight if len(valid) < 2 else float(valid[0][2]) - float(valid[1][2]),
                    "valid_count": len(valid),
                    "top3": [(vi, vl, float(vs)) for vi, vl, vs in valid[:3]]
                }]
                self.voting_frames_collected = 1

                if weight >= VOTE_WEIGHT_THRESH:
                    return self._tick_confirm(
                        filtered_idx, top_preds, valid,
                        filtered_idx, filtered_label, filtered_score, weight)

                return "none", -1, [], 0, self.round_discard_count, self.round_total_frames, False
            else:
                self.discard_frames_collected += 1
                self.round_discard_count += 1

                if self.discard_frames_collected >= DISCARD_FRAMES:
                    weight = self._calc_frame_weight(valid)
                    self.state = self.STATE_VOTING
                    self.vote_weights = {filtered_idx: weight}
                    self.vote_history = [{
                        "frame": self.round_frame_idx,
                        "idx": filtered_idx,
                        "label": filtered_label,
                        "score": float(filtered_score),
                        "weight": weight,
                        "gap": weight if len(valid) < 2 else float(valid[0][2]) - float(valid[1][2]),
                        "valid_count": len(valid),
                        "top3": [(vi, vl, float(vs)) for vi, vl, vs in valid[:3]]
                    }]
                    self.voting_frames_collected = 1

                return "none", -1, [], 0, self.round_discard_count, self.round_total_frames, False

        if self.state == self.STATE_VOTING:
            self.round_total_frames += 1

            if cls_idx >= 0:
                valid = self._get_valid_candidates(top_preds)
                if valid:
                    weight = self._calc_frame_weight(valid)
                    filtered_idx = valid[0][0]
                    filtered_label = valid[0][1]
                    filtered_score = valid[0][2]
                    gap = weight if len(valid) < 2 else float(valid[0][2]) - float(valid[1][2])

                    self.vote_weights[filtered_idx] = \
                        self.vote_weights.get(filtered_idx, 0.0) + weight
                    self.vote_history.append({
                        "frame": self.round_frame_idx,
                        "idx": filtered_idx,
                        "label": filtered_label,
                        "score": float(filtered_score),
                        "weight": weight,
                        "gap": gap,
                        "valid_count": len(valid),
                        "top3": [(vi, vl, float(vs)) for vi, vl, vs in valid[:3]]
                    })
                    self.voting_frames_collected += 1

                    if self.vote_weights[filtered_idx] >= VOTE_WEIGHT_THRESH:
                        return self._tick_confirm(
                            filtered_idx, top_preds, valid,
                            filtered_idx, filtered_label, filtered_score, weight)

            if self.round_total_frames >= WINDOW_SIZE:
                return self._tick_finalize(
                    top_preds, [],
                    -1, None, 0.0, 0.0
                )

            return "none", -1, [], 0, self.round_discard_count, self.round_total_frames, False

        return "none", -1, [], 0, 0, 0, False

    def get_current_best(self):
        if self.state == self.STATE_VOTING and self.vote_weights:
            best_idx = max(self.vote_weights, key=self.vote_weights.get)
            best_weight = self.vote_weights[best_idx]
            return best_idx, round(best_weight, 2)
        return -1, 0.0

    def get_debug_info(self):
        return {
            "state": self.state,
            "frame": self.frame,
            "weights": {str(k): round(v, 2) for k, v in self.vote_weights.items()},
            "voting_collected": self.voting_frames_collected,
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

        if self.JOKER_B_idx is not None:
            jb_score = float(softmax_res[self.JOKER_B_idx])
            if jb_score >= JOKER_B_DETECT_MIN:
                top1_val = float(softmax_res[top_preds[0][0]]) if len(top_preds) > 0 else 0.0
                boosted_jb = jb_score * JOKER_B_BOOST_MULT
                if top1_val - boosted_jb < JOKER_B_MAX_GAP:
                    softmax_res[self.JOKER_B_idx] = boosted_jb

        top_preds = get_top_predictions(softmax_res, self.labels, TOP_N)

        cls_idx = -1
        score = 0.0
        is_weak = False

        if len(top_preds) > 0:
            top1_idx, top1_label, top1_score = top_preds[0]

            if top1_label == "BLANK":
                del results
                gc.collect()
                return cls_idx, score, top_preds, -1, 0.0, is_weak, softmax_res

            blank_score = float(softmax_res[BLANK_IDX])
            if top1_score - blank_score < BLANK_GAP:
                del results
                gc.collect()
                return cls_idx, score, top_preds, -1, 0.0, is_weak, softmax_res

            if top1_score < MIN_CONFIDENCE:
                del results
                gc.collect()
                return cls_idx, score, top_preds, -1, 0.0, is_weak, softmax_res

            adjusted_score = top1_score
            if len(top_preds) >= 2:
                gap = top1_score - top_preds[1][2]
                if gap < TOP_GAP_THRESH and top1_score < 0.65:
                    adjusted_score = top1_score * 0.7

            if adjusted_score >= MIN_CONFIDENCE:
                cls_idx = top1_idx
                score = adjusted_score

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

    print("K230 Poker v3.0 [v22] | DISCARD={} SKIP={:.2f} WIN={} WTH={:.1f} WM={:.1f} CD={} | CONF={:.2f} BG={:.2f} WU={}".format(
        DISCARD_FRAMES, DISCARD_SKIP_CONFIDENCE,
        WINDOW_SIZE, VOTE_WEIGHT_THRESH, WEIGHT_MULTIPLIER, COOLDOWN_FRAMES,
        MIN_CONFIDENCE, BLANK_GAP, WARMUP_FRAMES))

    logger = RecognitionLogger(LOG_DIR)

    state = 0  # 0=待机, 1=识别中, 2=展示结果
    record_list = []
    frame_idx = 0
    state_machine = None
    session_start_ms = 0

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
                            session_start_ms = ticks_ms()
                            logger.start_session()
                            state_machine = StabilityStateMachine(recognizer.labels, logger)
                            state_machine.reset()
                            state = 1
                            uart.send(b'\xA1')
                            print("START")
                        elif b == 0x02:
                            state = 2
                            uart.send(b'\xA2')
                            logger.end_session(record_list)
                            print("STOP:", record_list)
            except Exception as e:
                print("UART err:", e)

            if state == 0:
                # 待机状态：只获取帧并显示，不做识别
                frame = pl.get_frame()
                pl.osd_img.clear()
                pl.osd_img.draw_string_advanced(10, 10, 28, "FPS: {}".format(fps), (0, 255, 0))
                pl.osd_img.draw_string_advanced(10, 50, 32, "Waiting...", (200, 200, 200))
                pl.show_image()
                gc.collect()
                continue

            if state == 1:
                frame_idx += 1
                frame = pl.get_frame()

                cls_idx, score, top_preds, adj_idx, adj_score, is_weak, softmax_res = recognizer.run(frame)

                # ---- 状态机驱动 ----
                action = "none"
                confirmed_idx = -1
                sm_state = 0
                sm_frame = 0
                sm_best_idx = -1
                sm_best_weight = 0.0
                sm_voting_collected = 0
                sm_confirmed_cards = []
                confirmed_label = None
                if state_machine is not None:
                    action, confirmed_idx, vote_history, vote_count, discard_count, total_frames, should_override = \
                        state_machine.tick(cls_idx, top_preds, softmax_res)

                    sm_state = state_machine.state
                    sm_frame = state_machine.frame
                    sm_best_idx, sm_best_weight = state_machine.get_current_best()
                    sm_voting_collected = state_machine.voting_frames_collected
                    sm_confirmed_cards = list(state_machine.confirmed_cards)

                    if action == "confirm":
                        confirmed_label = recognizer.labels[confirmed_idx]

                        if should_override and state_machine.C_8_idx is not None:
                            confirmed_label = RETRO_C8_TARGET
                            confirmed_idx = state_machine.C_8_idx
                            if state_machine.logger is not None:
                                state_machine.logger._write(
                                    "[Override] {} -> {} confirmed\n".format(RETRO_TRIGGER, RETRO_C8_TARGET)
                                )
                            rank_8 = "8"
                            if rank_8 not in state_machine.confirmed_ranks:
                                state_machine.confirmed_ranks.add(rank_8)
                            if confirmed_idx not in state_machine.confirmed_cards:
                                state_machine.confirmed_cards.add(confirmed_idx)

                        record_list.append(confirmed_label)
                        print(">>> CONFIRMED: {} (idx={}) [override={}]".format(
                            confirmed_label, confirmed_idx, should_override))

                # ---- 记录每一帧 ----
                logger.log_frame(
                    frame_idx, cls_idx, top_preds,
                    sm_state, sm_frame,
                    sm_best_idx, sm_best_weight,
                    sm_voting_collected,
                    sm_confirmed_cards,
                    action, confirmed_label,
                    recognizer.labels
                )

                pl.osd_img.clear()
                pl.osd_img.draw_string_advanced(10, 10, 28, "FPS: {}".format(fps), (0, 255, 0))
                pl.osd_img.draw_string_advanced(10, 50, 32, "Recording...", (0, 255, 0))

                if frame_idx <= WARMUP_FRAMES:
                    remaining = WARMUP_FRAMES - frame_idx + 1
                    pl.osd_img.draw_string_advanced(
                        10, 90, 20, "[Warmup {}]".format(remaining), (255, 150, 0))

                if len(top_preds) > 0:
                    top_text = "Top: "
                    for i, (iidx, lbl, sc) in enumerate(top_preds[:TOP_N]):
                        marker = "*" if i == 0 else " "
                        top_text += "{}{} {:.2f}  ".format(marker, lbl, sc)
                    pl.osd_img.draw_string_advanced(10, 130, 20, top_text, (100, 255, 100))

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

                if state_machine is not None:
                    sm_names = {0: "IDLE", 1: "TRACK", 2: "DISC", 3: "VOTE", 4: "COOL"}
                    sm_state_str = sm_names.get(state_machine.state, "?")
                    pl.osd_img.draw_string_advanced(
                        10, 200, 18,
                        "[SM: {} f={}]".format(sm_state_str, state_machine.frame),
                        (180, 180, 180)
                    )

                    best_idx, best_weight = state_machine.get_current_best()
                    if best_idx >= 0 and best_weight > 0:
                        collected = state_machine.voting_frames_collected
                        pl.osd_img.draw_string_advanced(
                            10, 225, 18,
                            "Vote: {} {}/{} ({}/{})".format(
                                recognizer.labels[best_idx],
                                round(best_weight, 2), VOTE_WEIGHT_THRESH,
                                collected, WINDOW_SIZE),
                            (0, 200, 255)
                        )

                    dbg = state_machine.get_debug_info()
                    pl.osd_img.draw_string_advanced(
                        10, 250, 16,
                        "wt:{}".format(str(dbg["weights"])[1:-1]),
                        (150, 150, 150)
                    )

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

            if state == 2:
                frame = pl.get_frame()
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
                try:
                    pl.show_image()
                except Exception as e:
                    print("show_image err:", e)
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
