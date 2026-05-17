# 代码修改日志

## 2026-05-14（晚）

### K230 识别算法 v24 — 帧计数超时 + 移除冷却期

**问题回顾（v23 日志分析）**：
- 10张牌一张都没确认，STOP 时 record_list=[]
- `cls=-1` 的帧消失了（v23 简化过滤有效），但确认数为 0
- 根因1：超时机制基于 `ticks_diff(ms)` 时间，逻辑反了——`last_card_time` 每帧有牌就重置，所以永远不会超时，`_finish_collect()` 永远不触发
- 根因2：冷却期 5 帧太短，根本来不及凑够 `MIN_VALID_FRAMES=2` 就结束
- 根因3：预热期逻辑失效——START 时 `frame_idx=WARMUP_FRAMES(20)`，再 tick 一次就超出预热，`frame <= WARMUP_FRAMES` 只跳过 1 帧

**`K230/main.py` v24 核心修复**：

1. **改用帧计数控制超时**：用 `no_card_frames` 累计连续无有效牌帧数，有有效牌就重置为 0。连续 `COLLECT_TIMEOUT` 帧无有效牌 → 触发裁决
2. **移除冷却期**：同一张牌确认后直接回 IDLE，不需要冷却（赛道上的牌不会折返）
3. **移除 `last_card_time`/`collect_start_frame`/`cooldown_end_frame`**：不再依赖时间戳
4. **主循环跳过预热期**：只在 `frame_idx > WARMUP_FRAMES` 时才调用 `collector.tick()`

**v24 vs v23 关键差异**：

| 项目 | v23 | v24 |
|------|-----|-----|
| 超时机制 | 基于时间（不工作） | 基于帧计数 |
| 冷却期 | 有（5帧，太短） | 无 |
| 预热处理 | 主循环不减frame | 主循环跳过collector.tick() |
| 状态数 | 3个（IDLE/Collecting/Cooldown） | 2个（IDLE/Collecting） |

## 2026-05-14（晚）

### K230 识别算法 v23 — 极度简化：移除 BLANK_GAP + TOP_GAP，只留单门槛 + 众数裁决

**问题回顾（v22 日志分析）**：
- 10张牌一张都没确认，90%的帧 `cls=-1` 被过滤
- `gapB`（top1 与 BLANK 差值）经常只有 0.02~0.05，远低于门槛 0.20
- `gap12`（top1 与 top2 差值）经常只有 0.001~0.005，几乎分不开
- 根因：55类分类任务中，top1 与 BLANK/top2 接近是常态而非噪声
- 多层过滤（BLANK_GAP → MIN_CONF → TOP_GAP）叠加导致大量有效帧被误杀

**`K230/main.py` v23 核心修复**：

1. **移除 `BLANK_GAP`**：删除 Top1 必须比 BLANK 高至少 0.20 的限制
2. **移除 `TOP_GAP_THRESH` 和 `TOP_GAP_PENALTY`**：删除 Top1 与 Top2 接近时的降权机制
3. **移除 `BLANK_MAX`**：删除 BLANK 概率本身的上限限制
4. **`MIN_CONFIDENCE` 降低**：0.30 → **0.25**（降低门槛让更多帧进入投票）
5. **`WARMUP_FRAMES` 缩短**：30 → **20**（加快响应）
6. **` Recognizer.run()` 简化为只有两层检查**：BLANK 过滤 → MIN_CONFIDENCE 门槛
7. **只保留众数裁决作为核心抗噪声机制**：大量帧进入投票，众数自然胜出

**v22 vs v23 过滤层级对比**：

| 版本 | 第一关 | 第二关 | 第三关 | 第四关 |
|------|--------|--------|--------|--------|
| v22 | BLANK过滤 | BLANK_GAP差值 | MIN_CONF | TOP_GAP降权 |
| v23 | BLANK过滤 | MIN_CONF | **众数裁决兜底** | - |

**v23 状态流转（不变）**：
```
IDLE → (检测到有效牌) → COLLECTING → (无牌超时) → 直接返回 confirm/discard
confirm/discard → COOLDOWN → IDLE
```

## 2026-05-14（晚）

### K230 识别算法 v22 — 移除TOP_GAP惩罚 + 立刻确认 + 降低置信度门槛

**问题回顾（v21 日志分析）**：
- 10张牌一张都没确认，全部 `cls=-1` 被过滤
- 模型输出有效（D_4 0.872、D_7 0.588 等），但全部在 `Recognizer.run()` 中被过滤
- 根因：`MIN_CONFIDENCE=0.40` + `TOP_GAP_PENALTY=0.85` 组合过于严格
  - 当 top1/top2 接近时（gap < 0.20）触发降权，0.85 倍率损失较大
  - 55类分类任务，top1/top2 接近是常态而非噪声
  - 即使 v21 放宽了门槛和倍率，大量帧仍然被误拒

**`K230/main.py` v22 核心修复**：

1. **`MIN_CONFIDENCE` 放宽**：0.40 → **0.30**（让更多帧进入区间统计）
2. **`TOP_GAP_PENALTY` 设为1.0**：完全移除降权惩罚，top1接近top2也接受
3. **`COLLECT_TIMEOUT` 缩短**：45帧 → **30帧**（加快响应，约1秒@30fps）
4. **诊断打印增强**：新增 `gap12`（top1-top2差值），便于下次分析
5. **消除 EVALUATING/CONFIRMED 中间状态**：超时后在本帧内立刻完成评估并返回确认，不再等下一帧
6. **冷却期重置简化**：冷却期满后直接回 IDLE，不再调用 `_reset_for_next()`

**状态流转（v22）**：
```
IDLE → (检测到有效牌) → COLLECTING → (无牌超时) → 直接返回 confirm/discard
confirm/discard → COOLDOWN → IDLE
```
比 v21 减少两个中间状态，确认更及时。

## 2026-05-14（晚）

### K230 识别算法 v21 — TOP_GAP 门槛收紧，减少误拒

**问题回顾（v20 日志分析）**：
- 10张牌一张都没确认，全部 cls=-1 被过滤
- 根因：大量真实牌的 Top1-Top2 gap 在 0.001~0.05 之间，远低于 TOP_GAP_THRESH=0.10
- 当 gap<0.10 且 top1<0.65 时，被 ×0.7 惩罚 → `H_J 0.418×0.7=0.293 < MIN_CONF=0.40` → 被拒
- 相当于：模型输出的 Top1/Top2 差距极小时，降权惩罚太狠，导致本来可以接受的帧被误杀

**`K230/main.py` v21 核心修复**：

1. **`TOP_GAP_THRESH` 提高**：0.10 → **0.20**（gap 低于 0.20 才触发降权，减少误杀）
2. **`TOP_GAP_PENALTY` 提高**：0.7 → **0.85**（降权后损失更小，减少误拒）
3. **`BLANK_GAP` 提高**：0.15 → **0.20**（稍微收紧防误识别）
4. **删除 `TOP1_NOISE_THRESH`**：整合到 MIN_CONFIDENCE 逻辑中，简化代码

### K230 识别算法 v17 — 区间检测 + 众数裁决（替代连续帧/滑动窗口）



**问题回顾（v16 仍需优化）**：
- 连续 N 帧确认策略：中间模型偶发一跳帧就前功尽弃，连续计数重置
- 不够尊重物理现实：小车经过一张牌有持续时间（不是只闪现几帧就消失）
- 固定帧窗口不够贴合物理过程

**`K230/main.py` v17 核心重写**：

**1. 算法完全重写（区间检测 + 众数裁决）**：
- 旧算法：同一张牌必须连续出现 N 帧才确认（或滑动窗口投票）
- 新算法：
  - 首次检测到有效牌 → 开始持续收集该区间的所有识别结果
  - 检测不到有效牌超过 `COLLECT_TIMEOUT=25` 帧 → 结束区间
  - 统计区间内出现次数最多的牌（众数）作为确认结果
- 物理窗口贴合：小车经过一张牌有持续时间，完整收集该时间窗口内的所有数据再裁决

**2. 状态流转重设计**：
- `IDLE` → 检测到有效牌 → `COLLECTING`
- `COLLECTING` → 无牌超时 → `EVALUATING`
- `EVALUATING` → 众数>=MIN_VALID → `CONFIRMED` → `COOLDOWN` → `IDLE`
- `EVALUATING` → 众数<MIN_VALID → `DISCARD` → `IDLE`

**3. 参数调整**：
- `COLLECT_TIMEOUT = 25`（无有效帧超过25帧 → 结束区间）
- `MIN_VALID_FRAMES = 3`（有效帧少于3帧 → 区间作废）
- `CONFIRM_MIN_FRAMES = 2`（确认后冷却2帧）
- 删除：原 `CONSECUTIVE_THRESH`、`COOLDOWN_FRAMES`、`STRICT_MODE`

**4. 众数裁决 `_get_mode()`**：
- 统计 `vote_bucket` 列表中每个 idx 出现的次数
- 返回出现次数最多的 idx 和计数
- 天然抗噪声：模型在窗口内短期跳变不影响最终裁决

**5. 永久冷却排除保持不变**：
- `confirmed_cards` 和 `confirmed_ranks` 在整次运行中持续生效
- 冷却期结束后重置 `vote_bucket`，但不清空 confirmed 集合

**6. OSD 显示重写**：
- 显示新状态名：IDLE / COLL / EVAL / CONF / COOL
- COLLECTING 状态：显示收集帧数和当前 no_card 计数器
- EVALUATING 状态：显示评估中提示
- CONFIRMED 状态：显示确认的牌和收集总帧数
- COOLDOWN 状态：显示剩余冷却帧数

**7. 理论优势**：
- 容忍识别模型在窗口内的短期跳变（H_7 跳了 2 帧 D_7 不影响最终结果）
- 利用完整的物理时间窗口，数据量更丰富
- 众数裁决天然抗噪声，参数少（只有超时阈值和最低帧数）

## 2026-05-14（上午）

### OLED页面精简：删除运行页，基础页整合灰度/ERR/PWM显示

**需求**：删除独立的OLED运行页，将灰度检测内容整合到待机页。

**`0_began/Core/Inc/bsp_oled.h`**：
- 删除 `OLED_PAGE_RUN` 枚举

**`0_began/Core/Src/bsp_oled.c`**：
- 删除 `Page_Run()` 函数及其声明
- 删除 `Page_Run()` 在 `BSP_OLED_Task()` 中的 case 分支
- **`Page_Standby()` 完全重构**（待机页整合）：
  - **第一行**：`ti: B 00000000` / `ti: F 00000000`，8位灰度digital从左到右为bit0→bit7
  - **第二行**：`Dis:xxxcm`，行驶距离
  - **第三行**：`Tim:xx.xxs`，行驶用时
  - **第四行**：`L:xxxx  R:xxxx`，左右电机PWM占空比

**`0_began/Core/Src/user_main.c`**：
- K2 启动时删除 `BSP_OLED_SwitchPage(OLED_PAGE_RUN)` 调用，保持待机页显示

## 2026-05-13（晚）

### 代码整理 + 注释规范化 + 版本1.0封装

**文件头注释补全**：为所有C源文件添加标准文件头注释（@file、@brief、功能概述、版本、日期）。

**`0_began/Core/Inc/app_state.h`**：
- 修正 B题/F题 注释描述（B题：循迹一圈后停车；F题：循迹一圈后声光提示，再跑完一圈停车）

**`0_began/Core/Inc/app_line.h`**：
- `LineParams_t` 结构体中 Kp/Ki/Kd 类型从 `float` 改为 `int16_t`（配合参数扩大10倍存储）
- `App_Line_SetParams()` 声明参数从 `float` 改为 `int16_t`

**`0_began/Core/Src/app_line.c`**：
- PID 参数扩大10倍存储（B_KP=120/B_KD=50/F_KP=60/F_KD=40）
- 去除重复的 PID 参数赋值代码块（精简逻辑）
- 添加文件头注释
- 参数宏注释修正

**`0_began/Core/Src/bsp_oled.c`**：
- `Page_Param()` 改用整数运算替代 `%f` 浮点格式化（避免 STM32 链接浮点库问题）
- 添加文件头注释

**`0_began/Core/Src/user_main.c` / `app_control.c` / `app_state.c` / `bsp_uart_k230.c`**：
- 添加文件头注释

**`0_began/Core/Src/bsp_*.c`**（encoder/beep/led/key/delay/gray）：
- 添加文件头注释

### 圆形赛道循迹优化（误差平滑 + 积分项）

**问题**：圆形赛道循迹时，小车基本靠边上两三路灰度跑，小幅晃动；全白场期间返回 error=0 直行导致贴边。

**`0_began/Core/Src/app_line.c`**：
- **F题启用积分项**：`F_KI` 从 0 改为 3（消除稳态误差，修正贴边跑）
- **新增低通滤波变量** `s_filt_error`（预留滤波平滑误差使用，当前代码中已声明但未激活）
- **B题 BASE_PWM** 从 1250 调整为 1300

### PID 参数扩大10倍存储（支持±0.1微调）

**需求**：加1超了减1少了，需要能更细粒度地微调 PID 参数。

**方案**：PID 参数内部扩大10倍存储（int16_t），OLED 显示时除以 10.0。

这样 K3/K4 每次 ±1，对应实际 PID 值变化 ±0.1，非常适合微调。

**`0_began/Core/Src/app_line.c`**：
- PID 宏定义从整数改为10倍值：`B_KP=120(12.0)`、`B_KD=50(5.0)`、`F_KP=60(6.0)`、`F_KD=40(4.0)`
- PID 计算公式改为除以10：`s_Kp * error / 10`（P/I/D三项均除10）

**`0_began/Core/Src/bsp_oled.c`**：
- `Page_Param()` 中 KP/KI/KD 显示改为整数运算，精确到小数点后1位

## 2026-05-13（下午）

### OLED 上电初始化延时修复

**问题**：更换新 OLED 模块后，每次上电 OLED 不显示，必须复位重启单片机才能显示。

**根因**：新 OLED 屏幕内部有更长的上电稳定时间，STM32 启动过快，I2C 初始化命令在 OLED 内部还未就绪时就发出了，导致 OLED 无法正确响应。

**修复**：`OLED_Init()` 开头增加 100ms 延时，等待 OLED 内部电路完全启动后再发送初始化命令序列。

**`0_began/Core/Src/OLED.c`**
- `OLED_Init()` 函数开头增加 `HAL_Delay(100);`

### K230 识别算法 v16 — 解决"一张牌检测出多张牌"问题

**问题回顾（v15 根因）**：
- 一张牌被检测出多张（如 H_7 被重复记录）
- 根因：冷却期结束后重置 `confirmed_cards` 和 `confirmed_ranks`（第244-250行），
  导致同一张牌在冷却结束后再次达到投票阈值时被重新确认
- 次要问题：候选替换时未检查窗口中是否已有该 idx，可能导致重复计数

**`K230/main.py` v16 核心修复**：

1. **永久排除（关键）**：
   - `confirmed_cards` 和 `confirmed_ranks` 在整次运行中永久生效
   - 冷却期结束后**不再清空**，只清空 `vote_window` 为下一张牌准备
   - 同一张牌冷却后依然被永久排除，无法再次确认

2. **冷却期候选积累**：
   - 冷却期内仍允许**有效帧**加入 `vote_window`（积累下一张牌的候选）
   - 冷却结束后直接回到 TRACKING，窗口中已有下一张牌的候选数据
   - 冷却结束时不清空 `confirmed_*`，但清空 `vote_window`
   - 冷却期间如果下一张牌已积累满窗口，冷却结束后可立即确认（无缝衔接）

3. **新增 `_add_to_window()` 方法**：
   - 同时检查 `_is_valid()` 和去重（窗口中已有该 idx 不重复加入）
   - 替代原来 TRACKING 中手动 append 的逻辑

4. **`_get_best_in_window()` 双重排除**：
   - 同时检查 `idx in confirmed_cards` 和 `rank in confirmed_ranks`
   - 确保排除逻辑严密

5. **新增 WARMUP 状态**：
   - `STATE_WARMUP = 0`（替代原来的 `STATE_IDLE`）
   - 预热期间状态为 WARMUP，预热结束后自动切换到 TRACKING
   - 冷却期内若达到冷却时间，直接回 TRACKING（不清空 confirmed）

6. **冷却期 OSD 显示**：
   - 冷却期间显示 `Cooling: N fr`（剩余冷却帧数）
   - 状态名：`WU`（预热）/`TRK`（追踪）/`CDN`（冷却）

### K230 识别算法 v15 — 滑动窗口投票（丢牌率优化）

**问题回顾（v14仍需优化）**：
- 连续5帧确认太刚性，模型偶发一跳帧就重置，导致5%丢牌率

**`K230/main.py` v15 核心改进**：

1. **滑动窗口投票替代严格连续**：
   - 旧算法：同一张牌必须**连续**出现5帧才确认，中间打断即重置
   - 新算法：记录最近5帧的识别结果，**3帧同牌即确认**
   - 允许模型在5帧中跳1-2帧，减少丢牌，同时保持对噪声的容忍度

2. **参数调整**：
   - `WINDOW_SIZE = 5`（窗口大小）
   - `VOTE_THRESH = 3`（3帧同牌确认）
   - `COOLDOWN_FRAMES = 18`（冷却期）
   - `WARMUP_FRAMES = 25`（预热缩短，加快响应）

3. **永久排除**：
   - `confirmed_cards` 和 `confirmed_ranks` 在整次运行中持续生效
   - 冷却期结束后完全重置（清空两个集合），允许识别之前确认过的牌

4. **OSD显示**：`Vote: S_7 [####/3/5/5]` 格式，直观显示当前投票进度

### K230 识别算法 v14 — 彻底重写状态机（连续帧确认）

**问题回顾（v13仍严重）**：
- 10张牌识别出15张，且识别出的牌经常不是正确那张
- 根因：窗口可重置导致"积分游戏"——每15帧满了就清零，H_7占8帧→清零→D_7占8帧→清零→S_7占8帧→确认，任何牌都能凑够6帧

**实测效果**：成功率约90%，丢牌率约5%。从150%超识别大幅改善。

**`K230/main.py` v14 核心重写**：

1. **算法完全重写（连续帧确认替代窗口统计）**：
   - 旧算法：窗口内统计每张牌的Top1出现次数，最多者确认
   - 新算法：追踪当前候选牌的**连续**Top1出现次数，只有连续N帧出现才确认
   - 出现不同牌时，候选重置为新牌，计数从0开始——打断即重置，从根本上堵住积分游戏漏洞

2. **参数调整**：
   - `CONSECUTIVE_THRESH = 5`（连续5帧确认）
   - `COOLDOWN_FRAMES = 20`（冷却期延长）
   - `MIN_CONFIDENCE = 0.45`（提高最低门槛）
   - `BLANK_GAP = 0.30`（大幅提高，减少无牌误识别）

3. **冷却后完全重置**：
   - 冷却期结束后清空 `confirmed_cards` 和 `confirmed_ranks`
   - 允许识别之前确认过的牌（因为可能是赛道上的另一张同rank牌）

4. **OSD调试增强**：
   - 显示追踪进度条（`Tracking: S_7 [####/5]`）
   - 显示 confirmed_ranks 列表

5. **实测效果（2026-05-13 下午）**：
   - 成功率约90%，丢牌率约5%
   - 从原来的10张识别出15张（150%超识）大幅改善

### K230 识别算法 v13 — 修复一牌多结果 + 防无牌误识别增强

**问题回顾**：
- 一牌多结果：模型把同一张 H_7 交替识别成 H_7、D_7 等同 rank 不同 suit 的牌，分别达到确认条件被记录
- 无牌也识别出牌：BLANK_GAP=0.20 不够高，无牌时某牌 Top1 分差仍能超过门槛

**`K230/main.py` v13 核心改进**：

1. **confirmed_ranks 集合（关键新增）**：
   - `confirmed_cards` 过滤特定牌索引，`confirmed_ranks` 过滤同 rank 所有牌
   - H_7 确认后，`confirmed_ranks` 记录 "7"，S_7/D_7/C_7 全部被过滤
   - 冷却期结束后**清空 `confirmed_ranks`**（`confirmed_cards` 保留）
   - 下一轮可以识别同 rank 的不同牌（因为可能是赛道上另一张）

2. **`_get_rank()` 函数**：提取牌标签的 rank 部分（S_7→7, H_K→K, JOKER_B→JOKER）

3. **`_filter_confirmed()` 增强**：同时检查 `confirmed_cards` 和 `confirmed_ranks`

4. **BLANK_GAP 提高**：0.20 → **0.25**，减少无牌时误识别

### K230 识别算法 v12 — 确认后立即排除已确认牌

**问题回顾**：
- v11 rank 级冲突裁决的思路错了
- 真正的问题：确认 C_8 后，后续帧模型继续输出 C_8（因为还是这张牌），
  这些帧应该被排除，让 Top2 进入统计。否则冷却结束后又被误确认。

**`K230/main.py` v12 核心改进**：

1. **confirmed_cards 集合**（关键机制）：
   - `reset()` 时清空 `confirmed_cards`
   - `_check_confirm()` 确认后立即把 idx 加入 `confirmed_cards`
   - 每帧 tick 时先调用 `_filter_confirmed()`：把 Top-N 中已确认的牌过滤掉
   - 如果 Top1 是已确认的牌，自动替换为 Top2（Top2 也确认则换 Top3……）
   - `confirmed_cards` 是**永久集合**，整次运行中持续排除，不会误确认同一张牌两次

2. **冷却期结束后清空窗口**：
   - 冷却期内不更新计数，冷却期结束后清空 `card_top1_count/card_topn_count/card_conf_sum`
   - 配合 confirmed_cards 机制，保证数据干净

3. **简化综合评分**：
   - `score = top1_cnt + conf_sum / window_size`
   - Top1 次数为主，置信度累计为辅（归一化到 0-1 范围）

### K230 识别算法 v10 — 解决 H_7/D_7 互抢丢牌 + 花色置信度裁决

**问题回顾**：
- 蓝色/绿色显示了但没有记录——根因是 Top-N 把 H_7 和 D_7 作为两张不同牌分别计次，
  15帧内 H_7 占Top1有3帧、D_7 占Top1有3帧、D_3 占Top1有2帧……
  没有任何一张达到 CONFIRM_THRESH=6 的窗口条件，全部丢牌
- 花色混淆：红桃/方块、黑桃/梅花容易弄反，需要用 softmax 置信度裁决

**`K230/main.py` v10 核心改进**：

1. **去掉 Top-N 辅助计数**（已删除 `TOPN_CONFIRM_THRESH`）：
   - 每帧只计最终 Top1 的出现次数，不再把 Top2/Top3 也计入统计
   - 消除了 H_7/D_7 同 rank 不同 suit 互相抢计数的问题
   - 综合评分简化为只比较 Top1 出现次数

2. **新增 `_resolve_frame()` 花色置信度裁决**：
   - 每帧检测 Top-N 中是否存在同 rank 不同 suit 的冲突
   - 如果 H_7(softmax=0.60) vs D_7(softmax=0.55)，选 H_7
   - 裁决在状态机 tick() 之前完成，只记录最终被选中的那张牌
   - 独立函数 `resolve_suit_conflict()` 提供跨帧的全局裁决接口（备用）

3. **`softmax_res` 传递到状态机**：
   - `Recognizer.run()` 返回值增加第7项 `softmax_res`
   - `state_machine.tick(cls_idx, top_preds, softmax_res, labels)` 接收 softmax 原始结果
   - 用于 `_resolve_frame()` 中的花色裁决

4. **参数微调**：
   - `TOPN_CONFIRM_THRESH` 已删除（不再使用）
   - `resolve_suit_conflict()` 函数可独立调用做跨帧全局裁决（备用）

### K230 识别算法 v9 — 参数优化（针对前两张牌丢牌率高）

**问题回顾**：
- v8算法丢牌率30%，且识别到的情况下正确率只有70%左右
- 根因：预热期50帧太长（前5-10秒全浪费）、窗口20帧太大、确认阈值8帧太严格

**`K230/main.py` v9 参数优化**：

1. **预热期缩短**：`WARMUP_FRAMES` 50帧 → **30帧**（减少前两张牌丢帧）
2. **窗口缩小**：`CONFIRM_WINDOW` 20帧 → **15帧**（加快响应速度）
3. **确认阈值降低**：`CONFIRM_THRESH` 8次 → **6次**（更容易确认）
4. **冷却期缩短**：`COOLDOWN_FRAMES` 20帧 → **15帧**（加快下一张牌检测）
5. **BLANK_GAP 放宽**：`BLANK_GAP` 0.22 → **0.20**（减少真实牌被误杀）
6. **占比阈值降低**：`TOP1_RATIO_THRESH` 0.45 → **0.40**（降低确认门槛）
7. **Top1噪声阈值略降**：`TOP1_NOISE_THRESH` 0.55 → **0.52**
8. **最低置信度略降**：`MIN_CONFIDENCE` 0.45 → **0.42**

**理论时序估算**（假设30fps）：
- 预热30帧 ≈ 1秒后开始统计
- 窗口15帧 ≈ 0.5秒填满
- 确认6帧 ≈ 识别到第6帧时即可确认（约0.2秒内）
- 冷却15帧 ≈ 0.5秒后开始检测下一张
- 理论上第一张牌最快约1.5秒可确认（比v8快很多）

**OSD调试信息**：版本号改为v9，显示参数配置

### K230 识别算法全面重写 v8 — 稳定性确认 + 冷却期状态机

**问题回顾**：
- 模型测试集准确率 100%，但实车识别时：读错牌、有牌没读到、没牌读出牌、一张牌读出多种
- 根因：训练集与实车场景的域差（角度/运动模糊/光照/部分遮挡），导致模型输出不稳定

**`K230/main.py` 完全重写**：

**v8 核心算法："稳定性确认 + 冷却期"状态机**

1. **`StabilityStateMachine` 类**（替代原来的 `SlidingWindowManager`）：
   - 状态流转：`IDLE → CONFIRMING → COOLDOWN → CONFIRMING`
   - `CONFIRMING`：20帧统计窗口，统计每张牌的 Top1 出现次数和 Top-N 出现次数
   - 确认条件（需同时满足）：
     - Top1 出现 >= 8 次
     - Top-N 出现 >= 5 次
     - Top1 次数占窗口比例 >= 45%（防止多种牌交替占 Top1）
   - `COOLDOWN`：确认后 20 帧冷却期，完全忽略所有识别结果，防止同一张牌被重复记录
   - 冷却期结束后回到 `CONFIRMING`，开始新的统计窗口

2. **彻底解决"一张牌读出多种"问题**：
   - 冷却期机制：确认一张牌后，20帧内所有识别结果被忽略，即使同一张牌被读成B又读成A，也不会重复记录
   - `last_confirmed_idx` 跳过：冷却期结束后，重新检测时也会跳过上一张刚确认的牌

3. **彻底解决"有牌没读到"问题**：
   - Top-N 辅助确认：Top2/Top3 出现次数 >= 5 也计入该牌的出现次数，增加鲁棒性
   - 窗口可滚动重置：20帧后自动重置窗口，不要求连续N帧

4. **更严格的置信度门槛**：
   - `BLANK_GAP` 从 0.17 提高到 **0.22**，减少无牌读出牌
   - `MIN_CONFIDENCE` 从 0.40 提高到 **0.45**
   - `TOP1_NOISE_THRESH` 从 0.50 提高到 **0.55**
   - `WARMUP_FRAMES` 延长到 **50帧**

5. **参数配置**：
   - `CONFIRM_WINDOW = 20`（统计窗口）
   - `CONFIRM_THRESH = 8`（Top1 出现次数要求）
   - `TOPN_CONFIRM_THRESH = 5`（Top-N 辅助确认）
   - `COOLDOWN_FRAMES = 20`（冷却期）
   - `TOP1_RATIO_THRESH = 0.45`（Top1 占比要求）

6. **OSD 调试信息增强**：
   - 显示状态机状态（IDLE/CONFIRM/COOLDOWN）
   - 显示当前窗口帧数和最佳牌的 Top1/TopN 计数
   - 显示 Top 候选列表（含 T1/TN 计数）

7. **record_list 简化**：只存确认的牌标签字符串，不再存次选牌、索引等复杂结构

## 2026-05-13（中午）

### OLED调参页恢复（B题/F题独立参数组 + K1/K2/K3/K4操作）

**参数步长调整**：PID参数调整步长从 `±0.1` 改为 `±1.0`（`LINE_PID_STEP` 1→10），方便快速大幅调整 PID 参数。

**需求**：恢复之前删除的调参页，两页参数组，K1切换B题/F题参数页，K2选参数，K3/K4调数值。

**`0_began/Core/Src/user_main.c`**
- `User_App_Loop()` 中重构按键分发逻辑，新增调参页按键处理分支：
  - **K1**：待机页→B题参数页→F题参数页→待机页（循环切换）
  - **K2**：在调参页时切换下一个参数（BASE→KP→KI→KD→BASE），在待机页时原有K2启动逻辑不变
  - **K3**：在调参页时对当前选中题目的当前参数+1（步进50），待机页时原有K3切题逻辑不变
  - **K4**：在调参页时对当前选中题目的当前参数-1（步进50）

**`0_began/Core/Src/bsp_oled.c`**
- `Page_Param()` 已存在，调用 `App_Line_GetTuneParam()` 获取当前选中参数并用 `<` 标记
- 底部显示操作提示 `K2 SEL K3+ K4-`

**`0_began/Core/Inc/bsp_oled.h`**
- `OLED_Page_t` 已有 `OLED_PAGE_STANDBY`、`OLED_PAGE_RUN`、`OLED_PAGE_PARAM_B`、`OLED_PAGE_PARAM_F`

**`0_began/Core/Src/app_line.c`**
- `App_Line_NextTuneParam()`、`App_Line_AdjustQuestionTuneParam()`、`App_Line_GetTuneParam()` 已存在
- `s_tune_param` 状态变量已存在

**操作流程**：
1. 待机页按 **K1** → 进入 B题参数页（显示 BASE/KP/KI/KD，`<` 标记当前选中）
2. 再按 **K1** → 切换到 F题参数页
3. 再按 **K1** → 返回待机页
4. 在参数页按 **K2** → 切换到下一个参数（BASE→KP→KI→KD）
5. 在参数页按 **K3** → 当前参数+1（BASE步进50，PID步进1）
6. 在参数页按 **K4** → 当前参数-1
7. 每次调参后参数自动存入对应题目的参数组，下次运行自动加载

## 2026-05-12（晚）

### 基础题与挑战题速度/PID参数分离

**`0_began/Core/Src/app_line.c`**
- 文件顶部集中放置 B题/F题 参数宏（改参数只需改这里）：
  - `B_BASE_PWM=900 / B_KP=18 / B_KI=0 / B_KD=6`（基础题，1.5倍速度）
  - `F_BASE_PWM=600 / F_KP=12 / F_KI=0 / F_KD=4`（挑战题，低速保守）
- `s_base_pwm/s_Kp/s_Ki/s_Kd` 状态变量初始化值改为 `F_*` 宏（默认安全值）
- 运行时 `App_Line_Task()` 内根据题目类型选择对应参数组

## 2026-05-12（下午）

### OLED待机页显示行驶距离和用时

**需求**：待机页第二行改为行驶的距离，第三行改为行驶用时，每次按K2开始运动就更新。

**`0_began/Core/Src/app_line.c`**
- 快照变量 `s_snapshot_start_tick` 记录K2按下时刻，`s_finish_tick` 记录停车时刻
- **`s_finish_enc_total`**：停车时刻的编码器累计值快照（新增），用于解决停车后距离显示为0的问题
- `App_Line_RecordSnapshot()`：仅记录起始时刻（删除快照编码值）
- `App_Line_RecordFinish()`：记录停车时刻**和编码器快照**（K4手动停车/自动停车均调用）
- `App_Line_GetRunningTimeS()`：停车后 `s_finish_tick` 锁住，返回最终用时；运行时返回实时用时
- `App_Line_GetSnapshotDist()`：**改用 `s_finish_enc_total` 快照值**计算距离（而非清零后的 `s_enc_total`），解决停车后距离显示为"----"的问题。**公式修复**：原公式 `enc_total/2/ONE_LAP_COUNT*π*D` 多除了2，导致一圈只有1.57米（实际应该是π*D≈314cm）。新公式为 `enc_total / ONE_LAP_COUNT * π * D`，即 `s_finish_enc_total / 43000 * 3.14159 * 100` cm
- **`App_Line_GetRunningTime100ms()`**：新增函数，返回精确到0.01秒的运行时间（float，秒），用于OLED待机页显示。旧函数 `App_Line_GetRunningTimeS()` 保留返回整数秒

**`0_began/Core/Inc/app_line.h`**
- 新增 `App_Line_RecordSnapshot()`、`App_Line_RecordFinish()`、`App_Line_GetRunningTimeS()`、`App_Line_GetSnapshotDist()` 声明

**`0_began/Core/Src/user_main.c`**
- K2 按下启动时调用 `App_Line_RecordSnapshot()`
- K4 手动停车时调用 `App_Line_RecordFinish()`

**`0_began/Core/Src/bsp_oled.c`**
- 待机页重写：
  - 第一行：题号（不变）
  - 第二行：`Dis:xxxcm` 行驶距离，enc_total转化，暂无数据显示 `Dis:---- cm`
  - 第三行：`Tim:xxs` 行驶用时，暂无数据显示 `Tim:----`
  - 第四行：电量 `BAT: OK`（K2/K3 提示已去掉）

### 1. 数据收集脚本 `K230/09_1数据收集.py`
- 已添加 BLANK（无牌/背景）类别作为第 0 类（兜底）
- 实际总类别 55 类 = BLANK(0) + 54种扑克牌
- 6种类别（C_7, D_3, D_K, D_Q, JOKER_B, JOKER_R）只是分组采集用的分组标签
- 显示文字优化：标注类别名称和采集提示

### 2. 识别器 `K230/main.py` 已更新（适配 55 类新模型 + BLANK 防误识别）

**v6 — BLANK 分差门槛（核心防误识别）**：
- 问题：空白场景下模型仍把某牌推到 Top1 且置信度不低，导致大量误识别
- 解决：在 `Recognizer.run()` 中直接取 softmax_res[BLANK_IDX]，要求 Top1 分值必须比 BLANK 高至少 `BLANK_GAP=0.15` 才入滑动窗口
- 新增参数 `BLANK_GAP = 0.15`，启动打印中显示

**v7 — 开机预热 + 关闭次选括号**：
- 开机时模型/摄像头不稳定，模型输出乱跳导致误识别。新增 `WARMUP_FRAMES=15`，前15帧识别结果不入滑动窗口
- `CONFLICT_SECOND_DISPLAY=False`，不再用括号显示次选候选，只显示可信度最高的牌
- 修复大小王文字反了：`JOKER_B`（黑）→ 小王，`JOKER_R`（红）→ 大王

**v5 改动**：
- `card_label_to_display()`：BLANK 返回 `(None, None)`，白屏展示时自动跳过
- `get_top_predictions()`：排除 BLANK（class 0），Top-N 始终为真实扑克牌
- `Recognizer.run()`：Top1 是 BLANK 时直接跳过，不入滑动窗口
- 55 类 = BLANK(0) + 54种扑克牌，数据收集时的6类是分组采集用的

### 3. 可选改进方向
- 串口协议优化：发送 `$CARD,<花色>,<x>,<y>,<置信度>` 给 STM32
- OSD 显示美化：显示当前帧的置信度曲线或历史柱状图
- FPS 监控：低于某阈值时报警

## 2026-05-12（下午）

#### 算法优化 v3：动态 ROI + 滑动窗口冲突消解

**`K230/main.py` 完整重写**：

**1. 动态加权 ROI**（替代固定 ROI）：
- 15帧统计窗口内，每张牌的记录格式：`(frame_idx, adjusted_score)`
- `get_ranked_cards()` 按 `(帧数降序, 累计置信度降序)` 排序
- 帧数最多的作为主结果记录；其余达到阈值的作为次选牌
- `CONFLICT_SECOND_DISPLAY=True` 时，次选牌用小括号在最后展示时标注
- `short_window_check()` 保留短窗口快速检查，不影响最终确认

**4. 删除连续性追踪**：
- 完全改为纯滑动窗口处理，适应小车运动抖动
- 不再要求连续出现多帧

**5. SlidingWindowManager 类**：
- 替代原来的 `detect_window` 列表 + `StabilityTracker`
- 每帧记录所有 Top-N 的 `(frame_idx, adjusted_score)`
- `update()` 时自动清理超过 LONG_WINDOW 的旧记录
- `get_ranked_cards()` 返回达到阈值的已排序结果
- `short_window_check()` 快速检查短窗口状态

**6. Top-N 二次确认保持**：差距 < 0.10 且 Top1 < 0.6 时降权 (×0.7)

**7. CLAHE + 自动白平衡**：保留增强预处理

**v5（2026-05-12 下午）**：
- 修复 `argsort` 在 ulab.numpy 不支持，改用线性遍历找 Top-N
- **三层置信度过滤**：MIN_CONF=0.40 过滤背景噪声，TOP1_NOISE=0.50 标弱识别
- **Top-N 降权更严格**：gap<0.12 且 top1<0.65 才降权
- **短窗口加严**：5帧内≥4次（原来4帧≥3次）
- **次选牌显示改为紧随主牌后面**：`1. 黑桃A (红桃5, 梅花K)` 而非单独一行
- `record_list` 格式改为含 `secondary_labels`，`get_secondaries_for()` 提取短窗口内同帧候选
- 新增 `is_weak` 标记，OSD 显示 `[WEAK]` 黄色提示

## 2026-05-12（下午）

### K230 数据收集脚本类别缩减

**需求**：只收集 C_7、D_3、D_K、D_Q、大小王，每种 200 张（1 批次）。

**`K230/09_1数据收集.py`**
- `class_lst` 从 54 种缩减为 6 种：`C_7`、`D_3`、`D_K`、`D_Q`、`JOKER_B`、`JOKER_R`
- `BATCH_COUNT` 从 6 改为 1（每种只 1 个批次）
- `BATCH_SIZE` 从 80 改为 200（每批次 200 张）
- `done` 显示文字改为动态 `len(class_lst)`，不再硬编码 54

**总收集量**：6 类别 × 1 批次 × 200 张 = 1200 张

### F题 K230 串口调试 + 行尾兼容

**`K230/main.py`**
- 修复 `pl.show_image(show_img)` 报错：`show_image()` 在 PipeLine 框架中不接受参数，应先把内容画到 `pl.osd_img` 再调用 `pl.show_image()`（无参）
- 白屏展示改用 `pl.osd_img.clear()` + 直接绘制 + `pl.show_image()`（无参）
- 串口初始化后立即发送 `uart.send("UCRT OK\n")`，用于验证 K230 串口 TX 是否正常
- 串口接收简化：只匹配 `\n`（STM32 `bsp_uart_k230.c` 发送的是 `$START\n`，不含 `\r`）

### 串口协议改为纯字节（解决 YbUart 参数/编码兼容问题）

**问题**：`YbUart` 不接受关键字参数，且文本协议处理复杂导致各种异常。

**修复**：STM32 发 0x01(START)/0x02(STOP)，K230 回 0xA0(握手)/0xA1(ACK START)/0xA2(ACK STOP)，彻底放弃文本协议。

**`K230/main.py`**
- `YbUart()` 无参构造
- 启动发送 `b'\xA0'` 握手
- 收到 0x01 回 `b'\xA1'`，收到 0x02 回 `b'\xA2'`
- 按字节逐个解析，不再需要文本 decode/strip

**`0_began/Core/Src/bsp_uart_k230.c`**
- `BSP_UART_K230_SendStart()` 发送 `0x01`
- `BSP_UART_K230_SendStop()` 发送 `0x02`

### F题 $START 发送时机修复

**问题**：F题开始时串口没有发送 `$START`，K230 收不到消息无法开始识别。

**根因**：原逻辑在 `app_line.c` 中通过 `s_enc_total >= 1` 条件发送，但 `s_enc_total` 是 5ms 循迹任务中累加的。小车从启动到编码器产生第一个有效计数之间存在时间差，且 `s_k230_start_sent` 标志一旦置 1 后，即使多次重启也只发一次，导致漏发。

**修复方案**：将 `$START` 发送从循迹任务中移到 K2 按键处理中，切换到 `APP_STATE_RUNNING` 时立即发送，不依赖编码器计数。

**`0_began/Core/Src/user_main.c`**
- K2 按键处理中，F题启动时立即调用 `BSP_UART_K230_SendStart()`
- `$STOP` 发送逻辑保持不变（在 `app_line.c` 中，一圈完成时发送）

**`0_began/Core/Src/app_line.c`**
- 删除 `s_k230_start_sent` 变量及其相关逻辑
- 删除 `$START` 发送代码（已移至 `user_main.c` 中 K2 按键处理）
- `App_Line_Init()` 中删除 `s_k230_start_sent` 初始化

### K230 main.py 全面迁移至 PipeLine 框架（适配 CanMV K230 v1.6）

**问题**：`sensor(0) snapshot chn(2) failed(3)` 报错，AI推理通道获取图像失败。

**根因分析**：
- CanMV K230 v1.6 引入了 MPP 私有池支持，架构重大变化
- `MediaManager.init()` 已在 v1.6 中被废弃（deprecated）
- 手动 `Sensor + Display + MediaManager.init()` 的方式无法正确配置 channel 2 的 media buffer
- 所有官方 AI 示例已全面迁移至 `PipeLine` 框架

**关键变化**：
- 通道0（`CAM_CHN_ID_0`）：YUV420SP → Display 显示
- 通道2（`CAM_CHN_ID_2`）：RGBP888 → AI 推理（`PipeLine.get_frame()` 返回 numpy array）
- 必须使用 `PipeLine.create()` → `PipeLine.get_frame()` → `PipeLine.show_image()` → `PipeLine.destroy()` 流程
- 不再需要手动 `MediaManager.init()` / `MediaManager.deinit()`
- `display_mode="lcd"` + `display_size=[640,480]` 适配 ST7701 LCD
- `to_ide=False` 禁用 IDE 预览（避免干扰 LCD 显示）

**`K230/main.py` 完全重写**：
- 导入 `from libs.PipeLine import PipeLine` 和 `from libs.Utils import ScopedTiming`
- 用 `PipeLine` 替代手动 Sensor/Display/MediaManager 初始化
- `recognizer.run()` 接收 `PipeLine.get_frame()` 返回的 numpy array（RGBP888 planar）
- 显示改用 `pl.osd_img.draw_string_advanced()` + `pl.show_image()`（ARGB8888 OSD层）
- 白屏结果用 `pl.show_image(show_img)` 直接替换 osd 层
- 清理改用 `pl.destroy()` 替代手动 stop/deinit

## 2026-05-11

### F题串口通信 + K230扑克牌识别展示（第二轮修订）

**需求变更**：
- 防误识别改为7帧滑动窗口，4~7帧识别到同一张就记录
- 记录后 `last_recorded` 去重，不连续重复记录同一张牌
- 收到 `$START\n` 清空上次结果，退出白屏，恢复显示摄像头画面
- 白屏展示：白色背景，黑桃/梅花/大王用黑色文字，红桃/方块/小王用红色文字，中文花色名称（如"黑桃A"、"红桃K"）
- 新增 `card_label_to_display()` 函数做标签到中文名称和颜色的转换

**STM32 → K230 串口协议**：
- `$START\n`：F题按下K2启动后，STM32通过USART1发送
- `$STOP\n`：F题跑完一圈完成时发送

**`0_began/Core/Inc/bsp_uart_k230.h` / `0_began/Core/Src/bsp_uart_k230.c`**（新建）
- 串口通信驱动，基于 `YbUart` 模拟的 STM32 端
- `BSP_UART_K230_Init()` / `BSP_UART_K230_SendStart()` / `BSP_UART_K230_SendStop()`

**`0_began/Core/Src/app_line.c`**
- F题启动后首次累加编码器时发送 `$START`（`s_k230_start_sent` 标志）
- F题达到一圈目标时发送 `$STOP`（在停车逻辑之前）
- 新增 `s_k230_start_sent` 变量和初始化

**`0_began/Core/Src/user_main.c`**
- `User_App_Init()` 中添加 `BSP_UART_K230_Init()` 调用

**`0_began/Makefile`**
- 新增 `Core/Src/bsp_uart_k230.c` 编译项

**`K230/main.py`**（完全重写）
- **串口**：`from ybUtils.YbUart import YbUart`，`uart = YbUart(baudrate=115200)`，轮询 `uart.read()` 接收 `$START` / `$STOP`
- **状态机**：
  - `state=0`：待机，显示摄像头画面 + "Waiting..."
  - `state=1`：收到 `$START` 后开始识别记录，每帧执行 YOLO 推理
  - `state=2`：收到 `$STOP` 后白屏展示，逐行显示识别到的扑克牌文本
- **防误识别**：7帧滑动窗口，窗口填满后统计每种牌出现次数，达到 `DETECT_THRESH=4` 帧即记录；记录后 `last_recorded` 去重，不连续重复记录同一张牌
- **收到 `$START` 行为**：清空 record_list / last_recorded / detect_window，state=1 恢复显示摄像头画面（退出白屏）
- **白屏展示**：白色背景，中文花色名称逐行打印，黑桃/梅花/大王黑色，红桃/方块/小王红色
- **LCD显示**：参照 `09_1数据收集.py`，使用 `Display.show_image(img, x=0, y=0, layer=Display.LAYER_OSD0)`，分辨率 640x480
- **摄像头**：参照 `09_1数据收集.py`，`Sensor(width=640, height=480)` + `Display.ST7701`
- **图像**：摄像头通道2给AI推理，显示用通道0画面

### 题目逻辑修正 + 代码清理

**题目逻辑修正**：
- **基础题(B)**：跑完一圈声光提示（蜂鸣器+BEEP + LED亮，1秒后自动关闭），再跑完一圈停车（共两圈）
- **发挥题(F)**：跑完一圈直接停车

**`0_began/Core/Src/app_line.c`**
- B/F 题目逻辑互换：B题走两圈+F题走一圈 → B题走两圈+F题走一圈（保持）
- B题：一圈后发声光提示，再跑完一圈停车
- F题：一圈后直接停车
- 删除 `s_lap_done` 变量
- 删除未使用的调参函数 `App_Line_GetTuneParam()`、`App_Line_NextTuneParam()`、`App_Line_AdjustTuneParam()`
- 删除未使用的 `s_tune_param` 变量

**`0_began/Core/Inc/app_line.h`**
- 删除 `LineTuneParam_t` 枚举
- 删除 `App_Line_IsLapComplete()`、`App_Line_GetTuneParam()`、`App_Line_NextTuneParam()`、`App_Line_AdjustTuneParam()` 声明

**`0_began/Core/Inc/app_state.h`**
- 删除 `APP_STATE_MOTOR_TEST`、`APP_STATE_ENCODER_TEST` 状态枚举

**`0_began/Core/Src/app_state.c`**
- 修复残留 `QUESTION_B1` → `QUESTION_B`

**`0_began/Core/Src/app_control.c`**
- 完全重写，删除 MotorTest 和 EncoderTest 相关代码
- 只保留 `App_Control_Init()`、`App_Control_Task()`、`App_Control_SetLineParams()`

**`0_began/Core/Inc/app_control.h`**
- 删除 `App_Control_StartMotorTest()`、`App_Control_StartEncoderTest()`、`App_Control_EncoderTestTask()` 声明

**`0_began/Core/Src/user_main.c`**
- 删除 `App_Debug_Init()`、`App_Debug_Task()`、`App_EncoderTest_Init()`、`App_EncoderTest_Task()` 调用
- 删除 `App_Control_StartMotorTest()` 调用
- 删除相关头文件引用

**删除文件**
- `Core/Src/app_debug.c`、`Core/Inc/app_debug.h`（空壳代码）
- `Core/Src/app_encoder_test.c`、`Core/Inc/app_encoder_test.h`（已完成历史使命）

**`0_began/Makefile`**
- 移除 `app_debug.c`、`app_encoder_test.c` 编译项

**`0_began/Core/Src/bsp_oled.c`**
- 进度条逻辑修正：B题目标两圈，F题目标一圈

## 2026-05-11（续）

### OLED页面简化：删除调试页，自动跳转待机页

**需求**：删除OLED调试页；按下K2进入运行页；完成目标圈数自动跳转待机页。

**`0_began/Core/Inc/bsp_oled.h`**
- 删除 `OLED_PAGE_DEBUG` 枚举，只保留 `OLED_PAGE_STANDBY` 和 `OLED_PAGE_RUN`

**`0_began/Core/Src/bsp_oled.c`**
- 删除 `Page_Debug()` 函数和 `bsp_key.h` 引用
- 简化 `BSP_OLED_Task()` 中的 switch，只处理待机页和运行页

**`0_began/Core/Src/user_main.c`**
- 删除 `Handle_DebugPageKey()` 函数
- 删除 K1 切换 OLED 页面的逻辑（K1 不再用于切页）
- K3/K2 按键处理逻辑不变

**`0_began/Core/Src/app_line.c`**
- 新增 `bsp_oled.h` 引用
- 达到目标圈数停车后（`APP_STATE_FINISHED`），自动调用 `BSP_OLED_SwitchPage(OLED_PAGE_STANDBY)` 跳转到待机页

### 题目枚举重构：B1→B（基础题），B2→F（发挥题）

**需求变更**：基础题B2 改为基础题B，基础题B1 改为发挥题F；根据说明书9.1处理扑克牌遮线，但因路径是圆形应保持遮线前的偏转角度继续运动而非直线。

**`0_began/Core/Inc/app_state.h`**
- `QuestionType_t` 枚举重构：`QUESTION_B1`(0) → `QUESTION_B`，`QUESTION_B2` → `QUESTION_F`
- 删除 `QUESTION_B3`、`QUESTION_F1`、`QUESTION_F2` 预留项，简化枚举

**`0_began/Core/Inc/app_line.h`**
- `LineStatus_t` 新增 `LINE_CARD_COVER`（扑克牌遮线状态）

**`0_began/Core/Src/app_line.c`**
- 扑克牌遮线策略（说明书9.1）：检测到持续白场（`_IsLineLost`）超过 `CARD_COVER_THRESH`（=30×5ms=150ms）后进入 `LINE_CARD_COVER` 状态
- 遮线时保持遮线前的 `s_last_error` 偏转角度，不降速沿圆弧行进
- 关键修复：`s_last_error` 仅在 `LINE_OK`（正常循迹）时更新为原始 `calc_error`，遮线期间保持不变，确保持续遮线时转向角度不衰减
- 题目枚举引用更新：`QUESTION_B2` → `QUESTION_F`（一圈后声光提示，两圈停车），`QUESTION_B1` → `QUESTION_B`（一圈停车）
- `App_Line_Init()` 逻辑不变，`s_last_error` 每次启动时正确重置

**`0_began/Core/Src/bsp_oled.c`**
- 待机页/运行页显示：B1/B2 → B/F
- K3 提示：`→B2/→B1` → `→F/→B`
- F题进度条目标改为 `ONE_LAP_COUNT * 2`

**`0_began/Core/Src/user_main.c`**
- K3 按键切换：B↔F

**`0_began/Core/Src/app_state.c`**
- 初始化默认题目改为 `QUESTION_B`

## 2026-05-10

### 代码全面检查与修复

**`0_began/Core/Src/gpio.c`**
- 修正 `PB4/PB5` 引脚注释，标注为"预留，软件I2C灰度实际用PA4/PA5"

**`K230/09_1数据收集.py`**
- **Bug修复（按键去抖逻辑错误）**：原代码 `else` 分支中 `last_key_state = key.value()` 导致按键抬起时被覆盖为 0，使得下一次检测到按下时 `last_key_state == 0` 满足，持续触发收集逻辑
- **修复**：将 `else: last_key_state = key.value()` 改为 `elif key.value() == 0: last_key_state = 0`，仅在按键释放时更新状态，避免边沿逻辑被破坏

**`0_began/Core/Src/app_line.c`**
- **安全加固**：为 `s_enc_total` 添加上限保护 `ENC_TOTAL_MAX`（ONE_LAP_COUNT * 4），防止 `int32_t` 溢出
- `App_Line_Init()` 中已正确重置 `s_enc_total`，每次启动独立计数

### B2题声光提示修正 + LED亮灭逻辑修正

**`0_began/Core/Src/app_line.c`**
- **问题1（声光提示一直响/亮到第二圈结束）**：原代码在触发声光提示后，只在最终停车时才关闭蜂鸣器和LED，导致B2题第一圈触发后会一直响/亮到第二圈结束
- **修复**：新增 `s_alert_off_done` 标志和 `s_alert_tick` 记录触发时刻，触发后延迟 `ALERT_DURATION_MS`(1000ms) 自动关闭蜂鸣器和LED
- **新增宏**：`ALERT_DURATION_MS (1000U)` 声光提示持续时间

**`0_began/Core/Src/bsp_led.c`**
- **问题2（LED亮灭反了）**：`BSP_LED_On()` 写 `GPIO_PIN_RESET`（低电平），`BSP_LED_Off()` 写 `GPIO_PIN_SET`（高电平），与实际硬件逻辑相反
- **修复**：交换两函数中的GPIO电平赋值

### 基础题B1/B2双题支持及一圈/两圈计数

**`0_began/Core/Inc/app_state.h`**
- 新增 `QuestionType_t` 枚举：`QUESTION_B1`（一圈停车）、`QUESTION_B2`（两圈后声光提示再跑完两圈停车）、`QUESTION_F1/F2`（发挥题预留）
- 新增 `App_State_SetQuestion()` / `App_State_GetQuestion()` 接口

**`0_began/Core/Inc/app_line.h`**
- 新增 `App_Line_GetEncTotal()` 接口声明，供 OLED 页面读取累计进度

**`0_began/Core/Src/app_state.c`**
- 实现题目切换逻辑，支持在 READY/FINISHED 状态切换 B1↔B2

**`0_began/Core/Src/app_line.c`**
- `App_Line_Init()` 重置 `s_alert_done`
- `App_Line_Task()` 核心计数逻辑重构：
  - **B1**：跑完一圈（`ONE_LAP_COUNT`）自动停车 + `APP_STATE_FINISHED`
  - **B2**：一圈后发声光提示（蜂鸣器+BEEP + LED亮），继续跑到两圈（`ONE_LAP_COUNT*2`）再停车
  - 停车时自动关闭蜂鸣器和LED
- 新增 `bsp_beep.h` 和 `bsp_led.h` 头文件引用

**`0_began/Core/Src/bsp_oled.c`**
- **待机页**：第一行显示当前题目类型（B1/B2），K3 功能提示根据当前题目动态显示（→B2/→B1）
- **运行页**：第一行显示 B1/B2 题号，进度行显示百分比进度（P:xxx%）
- 新增 `app_state.h`、`bsp_key.h` 引用

**`0_began/Core/Src/user_main.c`**
- `User_App_Init()` 中添加 `BSP_LED_Init()` 调用
- K3 按键：待机页/完成页下切换当前题目（B1↔B2）
- K2 启动时重置编码器 + `App_Line_Init()`
- K4 注释更新为"基础题运行时手动停车"

### 巡线一圈自动停车功能

**`0_began/Core/Inc/user_config.h`**
- 新增 `ONE_LAP_COUNT` 宏（一圈目标编码器累计值），默认值 1500，需实测后标定

**`0_began/Core/Inc/app_line.h`**
- 新增 `App_Line_IsLapComplete()` 接口声明

**`0_began/Core/Src/app_line.c`**
- 新增变量 `s_enc_total`（左右轮编码器绝对值累计）和 `s_lap_done`（一圈完成标志）
- `App_Line_Init()` 中重置这两个变量
- `App_Line_Task()` 中：在正常循迹时，每周期累加左右编码器 delta 的绝对值
  - 达到 `ONE_LAP_COUNT` 后：停止电机 + 切换 `APP_STATE_FINISHED`
- 新增 `App_Line_GetEncTotal()` 和 `App_Line_IsLapComplete()` 查询函数
- `app_line.c` 新增引入 `bsp_encoder.h`

### 巡线方向修正（左右PWM分配反了）

**`0_began/Core/Src/app_line.c`**

- **问题**：小车偏左时实际执行了右转，偏右时执行了左转，方向完全相反
- **根因**：权重计算得到的 error 符号与 PWM 分配逻辑不匹配
  - 小车偏左 → 最左传感器(bit0)检测黑线 → weight=-40 → error<0
  - 但原代码 `left_pwm = base_pwm - pwm_out`，负error让右轮加速，本应让左轮加速
- **修复**：交换左右轮 PWM 公式
  - 原：`left_pwm = base_pwm - pwm_out; right_pwm = base_pwm + pwm_out;`
  - 改：`left_pwm = base_pwm + pwm_out; right_pwm = base_pwm - pwm_out;`
- **新逻辑**：
  - 偏左 → error<0 → left_pwm 增加 → 左转修正
  - 偏右 → error>0 → right_pwm 增加 → 右转修正

### K230 数据收集脚本运行时错误修复

**`K230/09_1数据收集.py`**

- **错误**：`extra keyword arguments given` + `deprecated function` + `MPY: soft reboot`
- **根因**：CanMV K230 v1.6 固件中 `draw_string_advanced()` 不再支持 `color=` 关键字参数，官方示例中 color 直接作为第 5 个位置参数传入
- **修复**：将所有 16 处 `img.draw_string_advanced(..., color=(R, G, B))` 改为 `img.draw_string_advanced(..., (R, G, B))`（去掉 `color=` 关键字，保留位置参数）
- **附加修复**：将 2 处 `img.draw_rectangle(..., color=(R, G, B))` 同样改为位置参数（去掉 `color=` 关键字）

### K230 数据收集脚本重写（扑克牌识别训练数据）

**`K230/09_1数据收集.py`**

- **类别扩展**：从10个数字类改为 **54种扑克牌**
  - 黑桃/红桃/梅花/方块各13张（A, 2-10, J, Q, K）
  - 大小王2张（JOKER_B 大王, JOKER_R 小王）
  - 类别命名：`S_A` ~ `S_K`, `H_A` ~ `H_K`, `C_A` ~ `C_K`, `D_A` ~ `D_K`, `JOKER_B`, `JOKER_R`
- **收集策略**：每种150张，分3次按键完成，每次50张
- **按键逻辑**：短按启动新一轮收集（50张）；批次完成自动切换下一批次；3批次完成自动切换下一个类别
- **LCD显示**：参考示例代码修复显示，使用 `Display.show_image(img, x=0, y=0, mode=1)` 驱动LCD主帧缓冲
- **分辨率适配**：改为 640x480 采集和显示，添加按键去抖逻辑

### K230 LCD 屏幕不显示问题修复

- **问题原因1**：`Display.show_image(img, x=0, y=0, mode=1)` 调用缺失
  - 原代码只有 `compressed_for_ide()` 发送到 IDE 预览，没有真正驱动 LCD 主帧缓冲
  - 修复：添加 `Display.show_image(img, x=0, y=0, mode=1)`，`mode=1` 表示写入 LCD 主帧缓冲
- **问题原因2**：K230 ST7701 屏幕背光默认关闭（固件层面，上电后背光才会亮）
- **问题原因3**：v1.2.2 固件有屏幕不稳定已知 bug，建议升级到 PreRelease 固件
  - 现象：黑屏/闪屏/花屏/程序运行几个循环后停止
  - 下载地址：https://github.com/kendryte/canmv_k230/releases/tag/PreRelease
- **分辨率适配**：原代码用 `Sensor(width=1024, height=768)` + `Display.show_image(x=(800-320)//2...)` 缩放显示，现改为直接用 `800x480` 采集和显示，省去缩放步骤

## 2026-05-09

### 第五阶段：开环低速循迹

#### 新增文件

**`Core/Inc/app_line.h` / `Core/Src/app_line.c`**
- 灰度传感器黑线循迹模块，基于 PD 控制算法
- 8路灰度权重设计：`-40,-20,-10,-5,+5,+10,+20,+40`，对应 bit0(最左)~bit7(最右)
- 黑线检测：`digital` 中 bit=0 表示检测到黑场
- **PD控制公式**：`pwm_out = Kp*error + Kd*(error - last_error)`
  - `error > 0`（偏左）→ 减少左PWM、增加右PWM → 右转修正
  - `error < 0`（偏右）→ 减少右PWM、增加左PWM → 左转修正
- **丢线保护策略**（按 `LINE_TASK_PERIOD_MS=5ms` 周期）：
  - `<150ms`：保持上一周期 error 值，直行碾过
  - `150~500ms`：降速 70%~50% 找线
  - `>600ms`：停车，进入 `APP_STATE_ERROR`
- **默认参数**：`base_pwm=600, Kp=20, Kd=0`（说明书建议起步值）
- **I2C失败保护**：读取失败时立即停车

#### 修改文件

**`app_control.c/h`**
- `App_Control_Init()` 中调用 `App_Line_Init()`
- `App_Control_Task()` 中 `APP_STATE_RUNNING` 时执行 `App_Line_Task()`
- 新增 `App_Control_SetLineParams()` 接口

**`app_debug.c`**
- 新增串口命令：
  - `line <base> <kp> <kd>`：设置循迹参数（例：`line 600 20 0`）
  - `lineq`：查询当前循迹参数
  - `linedbg`：打印灰度digital值、error、状态

**`user_main.c`**
- K2 启动时同时调用 `App_Line_Init()` 重置循迹状态
- K2 支持从 `APP_STATE_ERROR` 恢复重新启动

**`Makefile`**
- 源文件列表加入 `Core/Src/app_line.c`

#### 调参指南（按说明书8.5节）

1. 先只调 `Kp`，每次 `+10`，直到直线轻微摆动
2. 再加 `Kd`，每次 `+10`，直到摆动减小或消除
3. 最后逐步提高 `base_pwm`，不要一开始就追求速度

#### 操作流程

1. 小车架空，按 K2 启动（进入 RUNNING 状态）
2. 灰度传感器读取 digital 值，`bit=0` 为黑场
3. 用 `linedbg` 查看 error 值是否与偏移方向一致
4. 放上赛道低速测试，依次调参

### OLED显示系统开发

- 新增三个 OLED 页面，周期 100ms（`OLED_TASK_PERIOD_MS`），K1 按键循环切换。
- `bsp_oled.h` 新增 `OLED_Page_t` 枚举（`STANDBY/RUN/DEBUG`）、`BSP_OLED_SwitchPage()`、`BSP_OLED_GetPage()`。
- **待机页**（`OLED_PAGE_STANDBY`）：
  - 显示标题、分隔线、当前模式（`IDLE/BASIC/MOTOR/ENC/RUN/ERROR/DONE`）、K2:START/K1:PAGE、BAT:OK。
  - K2 按键从 READY/FINISHED 状态启动系统，进入 RUNNING 并切换到运行页；K4 按键停止运行并返回待机页。
- **运行页**（`OLED_PAGE_RUN`）：
  - L/R 编码器增量、ERR 灰度错误码、左右 PWM 占空比、LOST 丢线状态（二进制+十六进制）。
  - 灰度 8 路模拟量（6x8 小字体，前 4 路第 5 行，后 4 路第 6 行）。
- **调试页**（`OLED_PAGE_DEBUG`）：
  - 左/右编码器累计值、K1-K4 按键实时状态、灰度 I2C ping 状态、固件版本、系统状态。
- `bsp_motor.c` 新增 `BSP_Motor_GetLeftPWM()` / `BSP_Motor_GetRightPWM()`，保存每次 Set 的 pwm 值供 OLED 运行页读取。
- `user_main.c` 主循环接管页面切换逻辑（K1 切页，K2 启动，K4 停止）。
- 编译通过：RAM 10.7%（2188B/20KB），Flash 33.8%（22120B/64KB）。

## 2026-05-08

- 检查并整理 `0_began` STM32 工程结构。
- 修正 `PB4/PB5` 为模拟高阻，避免干扰 I2C 共享网络。
- 将 TIM3/TIM4 编码器滤波统一为 `IC1Filter=8`、`IC2Filter=8`。
- 补齐 STM32 分层软件骨架：`user_main`、`user_config`、BSP 层、APP 层基础文件。
- 让 `main.c` 统一调用 `User_App_Init()` / `User_App_Loop()`。
- 将新源文件加入 `Makefile`，方便后续直接编译。
- 为 PlatformIO 的 ST-LINK 上传加入 `CPUTAPID 0x2ba01477` 兼容参数，处理 OpenOCD 识别码不匹配问题。
- 根据实测 OpenOCD 输出，修正 PlatformIO 上传参数为 `CPUTAPID 0x1ba01477`，避免 `UNEXPECTED idcode`。

- 修正右轮电机接线顺序：交换 `user_config.h` 中 `R_IN1_PIN`（PB15）和 `R_IN2_PIN`（PB14）。原接线导致 K3（PB10）应使右轮正转但实际反转，K4（PB11）应使右轮反转但实际正转。交换后：IN1=PB15，IN2=PB14，与代码中 `pwm>0 -> IN1=HIGH/IN2=LOW`（正转）逻辑一致。

- 新增编码器测试功能：创建 `app_encoder_test.c/.h`，实现 `ENCODER_TEST` 状态。测试电机前进 1.5 秒后，通过串口输出左右轮 delta 值及方向判定（OK/REVERSE/ZERO）。`user_config.h` 中 `LEFT_ENCODER_DIR=-1`、`RIGHT_ENCODER_DIR=1`。实测左轮编码器计数方向与电机方向相反（K1 正转得 -96），改为 `-1` 后左轮 delta 方向与电机方向一致；右轮编码器方向正确，保持 `1`。

- 扩展 `app_debug.c` 串口命令支持 `enc L|R|B`（编码器测试）、`encq`（实时查询）、`motor L|R <pwm>`、`stop`、`state`。`app_state.h` 新增 `APP_STATE_MOTOR_TEST` 和 `APP_STATE_ENCODER_TEST` 两个状态。
- 补齐 `app_debug.c` 中 `App_Debug_Printf` 实现，使用 UART 发送格式化调试输出，解决链接报错。

## 2026-05-09

### 灰度传感器驱动开发

- 完善灰度传感器驱动 `bsp_gray.c`：实现 `BSP_Gray_Ping()`（ping 诊断 0xAA→0x66）、`BSP_Gray_GetDigital()`（数字量 0xDD，返回 8bit，每 bit 对应一路）、`BSP_Gray_GetAllAnalog()`（连续模拟量 0xB0，一次读 8 路）。`user_config.h` 新增 `GRAY_I2C_ADDR=0x4E`（跳线帽 AD1=1 AD0=0，对应写地址 0x9C）。

### 引脚分配修正（重要）

**第一阶段修正（引脚从 PB8/PB9 改到 PA4/PA5）**：
`gscan` 扫到的 0x3C 是 OLED（通过硬件 I2C1 在 PB8/PB9），不是灰度传感器。硬件 I2C1 已占用 PB8/PB9 用于 OLED，软件模拟 I2C 灰度必须使用独立的 PA4/PA5 引脚。修正 `user_config.h` 中灰度引脚为 PA4/PA5。

**第二阶段修正（SCL/SDA 线序）**：
参考工程 `huidu.c` 定义 `SCL_PIN=GPIO_Pin_4, SDA_PIN=GPIO_Pin_5`，但实测正确接法为 **PA5=SCL, PA4=SDA**（当时反接时有 ACK 响应，正接无响应）。可能参考工程用了杜邦线交叉连接。已改正。

### 软件模拟 I2C 架构

- 完全重构 `bsp_gray.c` 为纯软件模拟 I2C，参考 `32循迹3.5` 工程 `huidu.c` 的成熟实现。关键决策：
  - 删除了所有 `HAL_I2C_*` 调用
  - 手动控制 GPIO（开漏输出+上拉输入切换）作为软件 I2C 总线
  - 参考旧项目经验，I2C 延时使用 `Delay_us(150)`（SCL 周期约 600µs，~1.6kHz），与灰度传感器低速内部 MCU 匹配
  - ACK 采样在 SCL 高电平期间，SDA 读取前先释放总线（上拉输入）
  - 标准 I2C 流程：START→addr+write→ACK→cmd→ACK→Repeated START→addr+read→ACK→data→NACK→STOP

### 灰度驱动最终状态（验证通过）

- `gscan` 在地址 0x4E 找到设备，ping 返回 0x66 OK
- `gray` 命令输出完整：`version=0x36`、`error=0x00`、`digital=0xC0`（bit7=1 对应第8路白场，其余黑场）、`analog[1-8]` 正常读取
- **注意**：更换灰度传感器后需要重新验证地址（不同传感器跳线帽配置可能不同）

### 代码整理

- 删除 `app_debug.c` 中冗余调试命令：`gbbscan`、`gready`、`pingraw`、`gaddr`、`i2creset`、`i2c <addr>`
- 删除 `bsp_gray.c` 中不再需要的 `s_last_ack` 调试变量及 `BSP_Gray_GetLastAck()` 函数
- 清理 `_I2C_TryAddr` 保留在 `BSP_Gray_I2CScan()` 中仍被使用

### 最终接线对应

| 总线 | SCL | SDA | 用途 |
|------|------|------|------|
| **灰度传感器**（软件模拟 I2C） | **PA5** | **PA4** | 灰度 8 路传感器 |
| OLED（硬件 I2C1） | PB8 | PB9 | OLED 显示屏 |
