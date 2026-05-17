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

算法版本：1.2（删除日志文件 + LCD白屏展示）

版本：1.2
日期：2026-05-15
"""

import os
import ujson
from media.sensor import *
from media.display import *
from media.media import *
from libs.PipeLine import PipeLine
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

# ---- 难检牌专项配置（v27 新增）----
# 针对实测最难检的7张牌（C_10/C_6/C_8/D_Q/C_9/JOKER_B/C_K）的专属优化
# 根因分析：7张中有5张是梅花花色，模型对梅花存在系统性置信度偏低
#           + Top1 与 Top2/3 的分差不够大（top_preds[1] 紧随其后）
#           + 极低置信度帧（< 0.25）被完全丢弃

# 7张难检牌的模型类别索引（基于 labels.txtpoke9）
HARD_CARD_IDX = {1, 6, 8, 9, 12, 26, 40}   # C_10/C_6/C_8/C_9/C_K/D_Q/JOKER_B

# ---- 置信度过滤 ----
TOP_N = 3
MIN_CONFIDENCE = 0.18      # v34：0.20→0.18，接收更多弱识别帧减少漏牌
TOP1_NOISE_THRESH = 0.50   # Top1 低于此值视为弱识别
TOP_GAP_THRESH = 0.08      # Top1 与 Top2 差距小于此值时降权（进一步放宽）
BLANK_GAP = 0.10           # v34：0.12→0.10，更多弱帧能通过检测减少漏牌
WARMUP_FRAMES = 5          # 开机预热帧数

# ---- 投票确认配置 ----
VOTE_THRESH = 3             # 同一张牌出现 N 帧即确认（v32: 2→3，防止误确认）
MIN_VOTE_FRAMES = 2         # v32 新增：确认时该牌至少出现在这么多帧里（防止加权分够但帧数过少）
COOLDOWN_FRAMES = 10        # 确认后冷却期帧数（v32: 8→10，给候选池更多积累时间）
COOLDOWN_GAP_THRESH = 0.08  # Top1-Top2差距小于此值视为弱确认

# ---- Top2/3 积累权重（v27 新增，关键优化）----
# 难检牌的 Top1-Top2 分差极小（gap < 0.08），TOP1_NOISE 大量触发
# 但这些帧的 Top2/Top3 往往就是真牌，需要以一定权重加入候选池
TOP2_WEIGHT = 0.5          # Top2 每帧有效票数
TOP3_WEIGHT = 0.3          # Top3 每帧有效票数（更次选，权重更低）
TOP23_MIN_CONF = 0.18       # v34：0.20→0.18，与 MIN_CONFIDENCE 同步
JOKER_GAP_THRESH = 0.25     # v33 新增：S_A 与 JOKER 的分差阈值，超过此值才交换
JOKER_B_DETECT_MIN = 0.10   # v29 新增：JOKER_B 进入 Boost 的最低分值
JOKER_B_BOOST_MULT = 1.3     # v29 新增：JOKER_B 分值 Boost 系数（温和放大）
JOKER_B_MAX_GAP = 0.25       # v29 新增：JOKER_B Boost 后与 Top1 的最大允许差距

# ---- 难检牌候选池双重积累（v27 新增）----
# 普通牌：Top1 → 候选池（权重1.0）
# 难检牌：Top1 → 候选池（权重1.0）+ Top2 → 候选池（权重0.5）+ Top3 → 候选池（权重0.3）

# ---- 花色映射 ----
SUIT_MAP = {
    "S": ("黑桃", (0, 0, 0)),
    "H": ("红桃", (255, 0, 0)),
    "C": ("梅花", (0, 0, 0)),
    "D": ("方块", (255, 0, 0)),
    "JOKER_B": ("小王", (0, 0, 0)),
    "JOKER_R": ("大王", (255, 0, 0)),
}

# ---- 点数映射（中文化）----
RANK_DISPLAY = {
    "A": "A", "2": "2", "3": "3", "4": "4", "5": "5",
    "6": "6", "7": "7", "8": "8", "9": "9", "10": "10",
    "J": "J", "Q": "Q", "K": "K",
}

def label_to_display(label):
    """将标签转为中文显示文本和颜色，(text, color)"""
    if label.startswith("S_"):
        suit_name, color = SUIT_MAP["S"]
        rank = label[2:]
        return suit_name + RANK_DISPLAY.get(rank, rank), color
    elif label.startswith("H_"):
        suit_name, color = SUIT_MAP["H"]
        rank = label[2:]
        return suit_name + RANK_DISPLAY.get(rank, rank), color
    elif label.startswith("C_"):
        suit_name, color = SUIT_MAP["C"]
        rank = label[2:]
        return suit_name + RANK_DISPLAY.get(rank, rank), color
    elif label.startswith("D_"):
        suit_name, color = SUIT_MAP["D"]
        rank = label[2:]
        return suit_name + RANK_DISPLAY.get(rank, rank), color
    elif label == "JOKER_B":
        return SUIT_MAP["JOKER_B"][0], SUIT_MAP["JOKER_B"][1]
    elif label == "JOKER_R":
        return SUIT_MAP["JOKER_R"][0], SUIT_MAP["JOKER_R"][1]
    else:
        return label, (180, 180, 180)


def read_deploy_config(config_path):
    with open(config_path, 'r') as json_file:
        config = ujson.load(json_file)
    return config


def softmax(x):
    exp_x = np.exp(x - np.max(x))
    return exp_x / np.sum(exp_x)


BLANK_IDX = 0  # BLANK（空白/背景）类别索引

def _get_suit_from_label(label):
    """从标签提取花色（S/H/C/D/JOKER_B/JOKER_R）"""
    if label.startswith("JOKER"):
        return label
    if "_" in label:
        return label.split("_", 1)[0]
    return ""


def _compute_suit_sums(softmax_res, labels):
    """
    计算每种花色的 softmax 置信度总和（v33 新增，替代花色补偿）。
    返回：(suit_sums_dict, orig_scores_dict)
    - suit_sums: {suit: total_softmax} 所有花色的总置信度和
    - orig_scores: {idx: orig_score} 每张牌原始分值
    """
    n = len(softmax_res)
    suit_sums = {}
    orig_scores = {}
    for i in range(n):
        if i == BLANK_IDX:
            continue
        label = labels[i]
        suit = _get_suit_from_label(label)
        sc = float(softmax_res[i])
        orig_scores[i] = sc
        if suit:
            suit_sums[suit] = suit_sums.get(suit, 0.0) + sc
    return suit_sums, orig_scores


def _resolve_suit_conflict(top1_idx, top2_idx, labels, suit_sums):
    """
    v33 新增：裁决同颜色不同花色冲突。
    当 Top1 和 Top2 分值接近但花色不同时，用花色总置信度和裁决。
    返回最终裁决的牌索引。
    """
    suit1 = _get_suit_from_label(labels[top1_idx])
    suit2 = _get_suit_from_label(labels[top2_idx])

    # 只在同颜色不同花色时裁决（H vs D，S vs C）
    if suit1 == suit2:
        return top1_idx

    # 定义同颜色花色对
    red_suits = {"H", "D"}
    black_suits = {"S", "C"}
    if not ((suit1 in red_suits and suit2 in red_suits) or
            (suit1 in black_suits and suit2 in black_suits)):
        return top1_idx

    # 比较花色总置信度和
    sum1 = suit_sums.get(suit1, 0.0)
    sum2 = suit_sums.get(suit2, 0.0)

    if sum2 > sum1:
        return top2_idx
    return top1_idx


def _get_rank(label):
    """提取牌标签的 rank 部分"""
    if label.startswith("JOKER"):
        return label
    if "_" in label:
        return label.split("_", 1)[1]
    return label


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


# ==================== v27 难检牌专项状态机 ====================

class StabilityStateMachine:
    """
    v27 算法（难检牌专项优化）：

    7张难检牌：C_10/C_6/C_8/D_Q/C_9/JOKER_B/C_K
    - 其中5张是梅花花色，模型对梅花存在系统性置信度偏低
    - Top1 与 Top2/3 分差极小，TOP1_NOISE 大量触发
    - 极低置信度帧（0.20~0.30）占大量比例

    v27 三大核心改进：

    1. 花色级置信度补偿：
       - 对梅花/小王，将同花色所有牌的 softmax 分值累加到目标牌
       - 例如：C_10(0.35) + C_6(0.12) + C_8(0.08) → C_10 补偿分 ≈ 0.55
       - 裁决时用补偿后分值选择 Top1/2/3

    2. 候选池加权计数（替代简单列表）：
       - 普通牌：Top1 → +1票，Top2/3 不计入
       - 难检牌：Top1 → +1票，Top2 → +0.5票，Top3 → +0.3票
       - 候选池用加权分值统计，达到 VOTE_THRESH 即确认
       - 防止 Top1 被误降权后难检牌无法积累

    3. TRACKING 分支 Top2/3 降级投票（扩展）：
       - 原 v26：Top1 被过滤时降级到 Top2/3
       - v27：始终检查 Top2/3（即使是难检牌），优先用花色补偿分裁决
       - 难检牌 Top2 ≥ TOP23_MIN_CONF 即可参与投票

    状态流转：
        TRACKING → (某牌达到阈值) → COOLDOWN → TRACKING → ...
    """

    STATE_IDLE = 0
    STATE_TRACKING = 1
    STATE_COOLDOWN = 2

    def __init__(self, labels, recognizer):
        self.labels = labels
        self.recognizer = recognizer  # v31：缓存 recognizer 引用，用于访问 S_A_idx 等索引
        self.state = self.STATE_IDLE
        self.frame = 0

        # 已确认的牌索引（整次运行永久排除）
        self.confirmed_cards = set()
        # 已确认的 rank（仅在冷却期内临时排除，冷却后清空）
        self.cooldown_ranks = set()
        # 冷却期结束帧号
        self.cooldown_end_frame = 0
        # 每张牌的投票计数器（idx -> float count，v26 继承）
        self.vote_counts = {}
        # v32 新增：每张牌的实际帧数计数（确认时要求至少 MIN_VOTE_FRAMES 帧）
        self.vote_frame_counts = {}
        # 候选池：冷却期内加权积累 {idx: total_weight}
        self.candidate_pool_weighted = {}
        # v32 新增：候选池的帧数计数
        self.candidate_pool_frames = {}

    def reset(self):
        self.state = self.STATE_TRACKING
        self.frame = 0
        self.confirmed_cards = set()
        self.cooldown_ranks = set()
        self.cooldown_end_frame = 0
        self.vote_counts = {}
        self.vote_frame_counts = {}
        self.candidate_pool_weighted = {}
        self.candidate_pool_frames = {}

    def _is_hard_card(self, idx):
        """判断是否为难检牌"""
        return idx in HARD_CARD_IDX

    def _get_rank(self, label):
        if label.startswith("JOKER"):
            return label
        if "_" in label:
            return label.split("_", 1)[1]
        return label

    def _add_to_candidate_pool(self, idx, weight):
        """向候选池加入加权票（v32: 同时追踪帧数）"""
        if idx in self.confirmed_cards:
            rank = self._get_rank(self.labels[idx])
            if rank in self.cooldown_ranks:
                return
            return
        rank = self._get_rank(self.labels[idx])
        if rank in self.cooldown_ranks:
            return
        self.candidate_pool_weighted[idx] = self.candidate_pool_weighted.get(idx, 0.0) + weight
        self.candidate_pool_frames[idx] = self.candidate_pool_frames.get(idx, 0) + 1

    def _get_best_in_candidate_pool(self):
        """从候选池返回加权分最高的牌索引和分值"""
        if not self.candidate_pool_weighted:
            return -1, 0.0
        best_idx = max(self.candidate_pool_weighted, key=self.candidate_pool_weighted.get)
        return best_idx, self.candidate_pool_weighted[best_idx]

    def _get_vote_best(self):
        """从 vote_counts 返回票数最高的牌索引和票数"""
        if not self.vote_counts:
            return -1, 0.0
        best_idx = max(self.vote_counts, key=self.vote_counts.get)
        return best_idx, self.vote_counts[best_idx]

    def tick(self, cls_idx, top_preds, softmax_res):
        """
        每帧调用。
        cls_idx: 本帧 Recognizer.run() 返回的 cls_idx（-1表示无效帧）
        top_preds: [(idx, label, score), ...] 原始 softmax 排序
        返回: (action, card_idx)
        """
        self.frame += 1

        if self.state == self.STATE_IDLE:
            return "none", -1

        # ---- 计算花色总置信度和（v33 核心，替代花色补偿）----
        suit_sums, orig_scores = _compute_suit_sums(softmax_res, self.labels)

        # ---- 获取 Top1 信息（用原始 softmax）----
        top1_idx = -1
        top1_score = 0.0
        if top_preds:
            top1_idx = top_preds[0][0]
            top1_score = orig_scores.get(top1_idx, 0.0)

        # ---- 获取 Top2 信息（用于花色裁决）----
        top2_idx = -1
        top2_score = 0.0
        if len(top_preds) >= 2:
            top2_idx = top_preds[1][0]
            top2_score = orig_scores.get(top2_idx, 0.0)

        # ---- v33：花色裁决：当 Top1/2 分差小且花色冲突时，用花色总和对来裁决----
        vote_idx = top1_idx
        vote_score = top1_score
        orig_gap = top1_score - top2_score if (top1_idx >= 0 and top2_idx >= 0) else 0.0
        if top1_idx >= 0 and top2_idx >= 0:
            gap = orig_gap
            if gap < 0.20:
                resolved_idx = _resolve_suit_conflict(top1_idx, top2_idx, self.labels, suit_sums)
                if resolved_idx != top1_idx:
                    vote_idx = resolved_idx
                    vote_score = orig_scores.get(vote_idx, 0.0)
                    # 花色裁决后 vote_idx 变了，需要重新算 gap（对 top2_idx 用 top3）
                    if vote_idx == top2_idx and len(top_preds) >= 3:
                        top3_score = orig_scores.get(top_preds[2][0], 0.0)
                        gap = vote_score - top3_score
                    else:
                        gap = 0.0
        else:
            gap = 0.0

        # ---- COOLDOWN ----
        if self.state == self.STATE_COOLDOWN:
            # 每帧检查候选池是否达到阈值（快速响应）
            best_idx, best_score = self._get_best_in_candidate_pool()
            best_frames = self.candidate_pool_frames.get(best_idx, 0)
            # v32: 确认要求加权分 >= VOTE_THRESH 且帧数 >= MIN_VOTE_FRAMES
            if best_score >= VOTE_THRESH and best_frames >= MIN_VOTE_FRAMES:
                confirmed_label = self.labels[best_idx]
                confirmed_rank = self._get_rank(confirmed_label)
                self.confirmed_cards.add(best_idx)
                self.cooldown_ranks.add(confirmed_rank)
                self.state = self.STATE_COOLDOWN
                self.cooldown_end_frame = self.frame + COOLDOWN_FRAMES
                self.vote_counts = {}
                self.vote_frame_counts = {}
                self.candidate_pool_weighted = {}
                self.candidate_pool_frames = {}
                return "confirm", best_idx

            if self.frame >= self.cooldown_end_frame:
                self.cooldown_ranks = set()
                self.state = self.STATE_TRACKING
                if best_score >= VOTE_THRESH and best_frames >= MIN_VOTE_FRAMES:
                    confirmed_label = self.labels[best_idx]
                    confirmed_rank = self._get_rank(confirmed_label)
                    self.confirmed_cards.add(best_idx)
                    self.cooldown_ranks.add(confirmed_rank)
                    self.state = self.STATE_COOLDOWN
                    self.cooldown_end_frame = self.frame + COOLDOWN_FRAMES
                    self.vote_counts = {}
                    self.vote_frame_counts = {}
                    self.candidate_pool_weighted = {}
                    self.candidate_pool_frames = {}
                    return "confirm", best_idx
                else:
                    self.vote_counts = {}
                    self.vote_frame_counts = {}
                    self.candidate_pool_weighted = {}
                    self.candidate_pool_frames = {}
            else:
                # 冷却期内：向候选池积累
                self._cooldown_accumulate(top1_idx, top1_score)
            return "none", -1

        # ---- TRACKING ----
        if self.state == self.STATE_TRACKING:
            # vote_idx/vote_score 已在上面通过花色裁决确定
            if vote_idx < 0:
                return "none", -1

            # ---- 检查 vote_idx 是否已被确认/冷却 ----
            vote_rank = self._get_rank(self.labels[vote_idx])
            vote_confirmed = vote_idx in self.confirmed_cards
            vote_in_cooldown_rank = vote_rank in self.cooldown_ranks

            # 如果裁决出的牌已经被确认/冷却，降级到 Top2/3
            if vote_confirmed or vote_in_cooldown_rank:
                backup_idx, backup_score = self._find_backup_candidate(top_preds, orig_scores)
                if backup_idx >= 0:
                    vote_idx = backup_idx
                    vote_score = backup_score
                else:
                    return "none", -1

            # ---- 投票判断：gap 计算用原始分 ----
            gap = 0.0
            if top2_idx >= 0 and vote_idx != top2_idx:
                gap = vote_score - top2_score
            elif len(top_preds) >= 2:
                gap = vote_score - orig_scores.get(top_preds[1][0], 0.0)

            is_hard = self._is_hard_card(vote_idx)

            # v33：困难牌 Top1 保护（保留 v31 逻辑）
            hard_protected = False
            if is_hard and top2_idx >= 0:
                if top2_score <= vote_score * 1.2:
                    hard_protected = True

            # v34: 普通牌门槛微调（gap 0.20→0.15，更容易确认减少漏牌）
            if is_hard:
                confirm_cond = (vote_score >= 0.25)
            else:
                confirm_cond = (vote_score >= 0.28 and (vote_score >= 0.35 or gap >= 0.15))

            if confirm_cond:
                self.vote_counts[vote_idx] = self.vote_counts.get(vote_idx, 0.0) + 1.0
                self.vote_frame_counts[vote_idx] = self.vote_frame_counts.get(vote_idx, 0) + 1
                best_v_idx, best_v_cnt = self._get_vote_best()
                # v32: 确认时要求加权分 >= VOTE_THRESH 且帧数 >= MIN_VOTE_FRAMES
                best_v_frames = self.vote_frame_counts.get(best_v_idx, 0)
                if best_v_cnt >= VOTE_THRESH and best_v_frames >= MIN_VOTE_FRAMES:
                    confirmed_label = self.labels[best_v_idx]
                    confirmed_rank = self._get_rank(confirmed_label)
                    self.confirmed_cards.add(best_v_idx)
                    self.cooldown_ranks.add(confirmed_rank)
                    self.state = self.STATE_COOLDOWN
                    self.cooldown_end_frame = self.frame + COOLDOWN_FRAMES
                    self.vote_counts = {}
                    self.vote_frame_counts = {}
                    self.candidate_pool_weighted = {}
                    self.candidate_pool_frames = {}
                    return "confirm", best_v_idx
            return "none", -1

        return "none", -1

    def _cooldown_accumulate(self, top1_idx, top1_score):
        """v33：COOLDOWN 分支的候选池积累（只计 Top1）"""
        top1_rank = self._get_rank(self.labels[top1_idx]) if top1_idx >= 0 else ""
        top1_in_cooldown = (top1_idx in self.confirmed_cards or top1_rank in self.cooldown_ranks)
        s_a_in_cooldown = (top1_idx == self.recognizer.S_A_idx) if self.recognizer.S_A_idx is not None else False

        if top1_idx >= 0 and not top1_in_cooldown and top1_score >= MIN_CONFIDENCE and not s_a_in_cooldown:
            self._add_to_candidate_pool(top1_idx, 1.0)

    def _find_backup_candidate(self, top_preds, orig_scores):
        """v33：在 Top2/3 中找有效候选（用原始分值）"""
        for i, (cand_idx, cand_label, _) in enumerate(top_preds):
            if cand_idx in self.confirmed_cards:
                continue
            cand_rank = self._get_rank(cand_label)
            if cand_rank in self.cooldown_ranks:
                continue
            sc = orig_scores.get(cand_idx, 0.0)
            if sc < MIN_CONFIDENCE:
                continue
            if i == 0:
                continue
            if sc < TOP23_MIN_CONF:
                continue
            return cand_idx, sc
        return -1, 0.0

    def get_current_best(self):
        """返回当前 vote_counts 中票数最多的牌索引及其票数"""
        return self._get_vote_best()

    def get_debug_info(self):
        """返回调试信息"""
        return {
            "state": self.state,
            "frame": self.frame,
            "best": self.get_current_best(),
            "vote_counts": dict(self.vote_counts),
            "vote_frame_counts": dict(self.vote_frame_counts),
            "confirmed_cards": list(self.confirmed_cards),
            "cooldown_ranks": list(self.cooldown_ranks),
            "candidate_pool": {k: round(v, 2) for k, v in self.candidate_pool_weighted.items()},
            "candidate_pool_frames": dict(self.candidate_pool_frames),
        }

    def get_current_vote_frames(self, idx):
        """返回某张牌的投票帧数"""
        return self.vote_frame_counts.get(idx, 0)


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
        # v33：JOKER_GAP_THRESH 0.15→0.25，更容易把 S_A 换下去（小王更容易正确识别）
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

    recognizer = Recognizer()

    from ybUtils.YbUart import YbUart
    uart = YbUart()
    uart.send(b'\xA0')

    state = 0  # 0=待机, 1=识别中, 2=结果展示
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
                            state_machine = StabilityStateMachine(recognizer.labels, recognizer)
                            state_machine.reset()
                            state = 1
                            uart.send(b'\xA1')
                        elif b == 0x02:
                            state = 2
                            uart.send(b'\xA2')
            except Exception:
                pass

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

                # ---- OSD ----
                pl.osd_img.clear()
                pl.osd_img.draw_string_advanced(10, 10, 24, "FPS: {}".format(fps), (80, 120, 255))

                if state == 0:
                    pl.osd_img.draw_string_advanced(
                        10, DISPLAY_HEIGHT // 2 - 20, 32,
                        "Ready, waiting for START...", (180, 180, 180)
                    )
                    pl.show_image()
                    gc.collect()
                    continue

                if state == 1:
                    # 当前正在计算的候选牌（Top-1）
                    if cls_idx >= 0:
                        label = recognizer.labels[cls_idx]
                        disp_text, disp_color = label_to_display(label)
                        score_color = (0, 255, 0) if not is_weak else (255, 200, 0)
                        weak_tag = " [W]" if is_weak else ""
                        hard_tag = " [H]" if cls_idx in HARD_CARD_IDX else ""
                        pl.osd_img.draw_string_advanced(
                            10, 40, 40,
                            "{}{}{}  {:.2f}".format(disp_text, weak_tag, hard_tag, float(score)),
                            score_color
                        )

                    # 已确认列表（中文 + 分色）
                    if record_list:
                        row_y = 90
                        for i, card_label in enumerate(record_list):
                            disp_text, disp_color = label_to_display(card_label)
                            pl.osd_img.draw_string_advanced(
                                10, row_y + i * 34, 28,
                                "{}. {}".format(i + 1, disp_text),
                                disp_color
                            )

                pl.show_image()
                gc.collect()
                continue

            elif state == 2:
                pl.osd_img.clear()
                total = len(record_list)
                pl.osd_img.draw_string_advanced(
                    10, 10, 40,
                    "Done! {} card(s)".format(total), (0, 255, 0)
                )
                COL_Y = 65
                ROW_H = 50
                MAX_COL1 = 8
                for i, card_label in enumerate(record_list):
                    disp_text, disp_color = label_to_display(card_label)
                    if i < MAX_COL1:
                        col_x = 10
                        row_y = COL_Y + i * ROW_H
                    else:
                        col_x = 330
                        row_y = COL_Y + (i - MAX_COL1) * ROW_H
                    pl.osd_img.draw_string_advanced(
                        col_x, row_y, 46,
                        "{}. {}".format(i + 1, disp_text),
                        disp_color
                    )
                pl.show_image()
                gc.collect()
                continue

    except Exception:
        pass
    finally:
        pl.destroy()
        nn.shrink_memory_pool()
        gc.collect()


if __name__ == "__main__":
    main()
