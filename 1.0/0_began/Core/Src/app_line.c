/**
 * @file    app_line.c
 * @brief   黑线循迹控制模块
 *
 * 功能概述：
 * - 8路灰度传感器黑线循迹，基于PID控制算法
 * - 支持基础题(B)和挑战题(F)两套独立参数组
 * - 支持扑克牌遮线检测（保持遮线前角度沿圆弧行进）
 * - 支持一圈计数（编码器累加判定）
 * - 支持B题声光提示、F题K230串口通信
 *
 * 硬件接口：
 * - 灰度传感器：PA4(SDA)/PA5(SCL)，软件模拟I2C
 * - 电机驱动：PB12/PB13(左轮IN1/IN2)，PB14/PB15(右轮IN1/IN2)
 * - PWM输出：TIM2 CH1(左轮)，TIM2 CH2(右轮)
 * - 编码器：TIM3(左轮)，TIM4(右轮)
 *
 * PID参数说明：
 * - 参数内部扩大10倍存储（int16_t），OLED显示时除以10
 * - K3/K4每次±1，对应实际PID值变化±0.1，方便微调
 *
 * 版本：1.0
 * 日期：2026-05-13
 */

/**
 * @file    app_line.c
 * @brief   黑线循迹控制模块
 *
 * 功能概述：
 * - 8路灰度传感器黑线循迹，基于PID控制算法
 * - 支持基础题(B)和挑战题(F)两套独立参数组
 * - 支持扑克牌遮线检测（保持遮线前角度沿圆弧行进）
 * - 支持一圈计数（编码器累加判定）
 * - 支持B题声光提示、F题K230串口通信
 *
 * 硬件接口：
 * - 灰度传感器：PA4(SDA)/PA5(SCL)，软件模拟I2C
 * - 电机驱动：PB12/PB13(左轮IN1/IN2)，PB14/PB15(右轮IN1/IN2)
 * - PWM输出：TIM2 CH1(左轮)，TIM2 CH2(右轮)
 * - 编码器：TIM3(左轮)，TIM4(右轮)
 *
 * PID参数说明：
 * - 参数内部扩大10倍存储（int16_t），OLED显示时除以10
 * - K3/K4每次±1，对应实际PID值变化±0.1，方便微调
 *
 * 版本：1.0
 * 日期：2026-05-13
 */

#include "app_line.h"
#include "app_state.h"
#include "bsp_beep.h"
#include "bsp_encoder.h"
#include "bsp_gray.h"
#include "bsp_led.h"
#include "bsp_motor.h"
#include "bsp_oled.h"
#include "bsp_uart_k230.h"
#include "user_config.h"
#include <stdlib.h>

/* ============================ 速度/PID 参数配置 ============================ */
/* =====================================================
 *   B题（基础题）参数组  —  高速（BASE=1300，循迹一圈后停车）
 *   F题（挑战题）参数组  —  低速保守（BASE=700，循迹一圈后声光提示，再跑完一圈停车）
 * 参数内部扩大10倍存储（方便OLED调参±0.1微调）
 * 显示值 = 存储值 / 10.0
 * 修改参数只需改这里
 * ===================================================== */
#define B_BASE_PWM   1300
#define B_KP         112   
#define B_KI         20
#define B_KD         60   

#define F_BASE_PWM   700
#define F_KP         60   
#define F_KI         3
#define F_KD         40   
/* ===================================================== */

#define LINE_BASE_PWM_MIN   0
#define LINE_BASE_PWM_MAX   MOTOR_PWM_MAX
#define LINE_PID_MIN        0
#define LINE_PID_MAX        999
#define LINE_BASE_PWM_STEP  50
#define LINE_PID_STEP       1

/* ============================ 寻线黑线权重 ============================ */
/* 8路灰度传感器布置在车头，小车前进方向从左到右
 * 黑线位置对应权重（与前进方向相关）：
 *   偏左 → error < 0 → 左转修正（增加左PWM，减少右PWM）
 *   偏右 → error > 0 → 右转修正（增加右PWM，减少左PWM）
 * 权重根据传感器分布设计，使得:
 *   小车偏左时 error 为负，左轮加速
 *   小车偏右时 error 为正，右轮加速 */
static const int8_t s_weights[8] = {
    -40,  /* 第1路（最左）：大幅左转 */
    -28,  /* 第2路 */
    -20,  /* 第3路 */
    -15,   /* 第4路 */
     15,   /* 第5路 */
    20,  /* 第6路 */
    28,  /* 第7路 */
    40   /* 第8路（最右）：大幅右转 */
};

/* ============================ 状态变量 ============================ */
static int16_t s_last_error = 0;   /* 上次error，用于微分项 */
static int32_t s_integral   = 0;   /* 积分累积，用于积分项 */
static uint16_t s_lost_cnt  = 0;   /* 丢线计数（单位：5ms周期） */
static LineStatus_t s_status = LINE_OK;
static int16_t s_cur_error  = 0;    /* 当前error缓存 */
static int16_t s_base_pwm  = F_BASE_PWM;  /* 速度，运行期按题目类型赋值 */
static int16_t s_Kp        = F_KP;
static int16_t s_Ki        = F_KI;
static int16_t s_Kd        = F_KD;
static LineParams_t s_question_params[QUESTION_COUNT] = {
    {B_BASE_PWM, B_KP, B_KI, B_KD},
    {F_BASE_PWM, F_KP, F_KI, F_KD}
};
static LineTuneParam_t s_tune_param = LINE_TUNE_BASE_PWM;

/* 一圈计数 ============================ */
static int32_t s_enc_total  = 0;    /* 左右轮编码器累计（绝对值累加） */
static uint8_t s_alert_done = 0;    /* 已发出声光提示标志（B题用） */
static uint8_t s_alert_off_done = 0; /* 声光提示已自动关闭标志 */
static uint32_t s_alert_tick = 0;   /* 声光提示触发时刻（ms） */

/* K2按下时的快照数据（用于OLED待机页显示上次行驶结果） */
static uint32_t s_snapshot_start_tick = 0;  /* K2按下时的时间戳（ms），0表示无有效记录 */
static uint32_t s_finish_tick         = 0;  /* 停车时的时间戳（ms），0表示未停车 */
static int32_t  s_finish_enc_total    = 0;  /* 停车时的编码器累计值快照 */

/* 编码器累计上限（防止int32_t溢出：最大目标=ONE_LAP_COUNT*2，加2倍余量） */
#define ENC_TOTAL_MAX    (ONE_LAP_COUNT * 4)

/* 圈直径（cm），用于编码器值转换为距离 */
#define WHEEL_DIAMETER_CM  100.0f

/* ============================ 灰度布局说明 ============================ */
/*
 * 小车俯视图（前进方向 →）：
 *
 *   左轮                                     右轮
 *    ◯ ─────────────────────────────────── ◯
 *         |  [1][2][3][4][5][6][7][8]  |
 *         |__________ 灰度传感器 __________|
 *
 * digital bit含义：bit0=第1路(最左), bit7=第8路(最右)
 * 黑线检测：1=白场，0=黑线
 *
 * 典型场景：
 *   digital = 0b11111111 (0xFF)  → 全白场，纯地面
 *   digital = 0b00111100 (0x3C)  → 中间4路黑线（弯道）
 *   digital = 0b00000111 (0x07)  → 最右3路黑线（急左弯）
 *   digital = 0b11100000 (0xE0)  → 最左3路黑线（急右弯）
 */

/* 声光提示持续时间（ms），1秒后自动关闭 */
#define ALERT_DURATION_MS    (1000U)

/* ============================ 丢线阈值 ============================ */
/* 丢线判定：8路全白（digital=0xFF）时认为是完全丢线
 * 但如果地面反光或阈值过低，可能误判，需要确认传感器实际表现
 * 备用策略：黑线区域全白时也视为接近丢线 */
#define LOST_THRESH_SHORT   (30U)   /* 30×5ms = 150ms，短丢线保持上一转向 */
#define LOST_THRESH_MED     (100U)  /* 100×5ms = 500ms，中丢线降速找线 */
#define LOST_THRESH_LONG    (120U)  /* 120×5ms = 600ms，长丢线停止 */

/* ============================ 积分限幅（抗积分饱和） ============================ */
#define INTEGRAL_MAX        (500)
#define INTEGRAL_MIN        (-500)

/* 扑克牌遮线检测阈值
 * 扑克牌放在黑线上，灰度传感器检测到大部分/全部白场（digital高位为1）
 * 连续多帧持续大部分白场则认为是扑克牌遮线，而非短暂丢线
 * 这里用丢线计数阈值来判断：超过 SHORT 阈值且持续白场视为遮线 */
#define CARD_COVER_THRESH   LOST_THRESH_SHORT   /* 30×5ms = 150ms，超过此时间且全白视为扑克牌遮线 */

/* ============================ 初始化 ============================ */

void App_Line_Init(void)
{
    s_last_error = 0;
    s_integral   = 0;
    s_lost_cnt   = 0;
    s_status     = LINE_OK;
    s_cur_error  = 0;
    s_enc_total  = 0;
    s_alert_done = 0;
    s_alert_off_done = 0;
    s_alert_tick = 0;
    s_snapshot_start_tick = 0;
    s_finish_tick = 0;
    s_finish_enc_total = 0;

    LineParams_t params;
    App_Line_GetQuestionParams(App_State_GetQuestion(), &params);
    s_base_pwm = params.base_pwm;
    s_Kp = params.Kp;
    s_Ki = params.Ki;
    s_Kd = params.Kd;
}

/* ============================ 灰度误差计算 ============================ */

/* 根据灰度digital值计算黑线位置误差
 * 返回：error值，含义如下：
 *   error > 0：小车偏左，需要右转（向右修正）
 *   error < 0：小车偏右，需要左转（向左修正）
 *   error = 0：居中或全白
 *
 * 寻线策略：找到所有检测到黑线的传感器（digital中bit=0），
 *          用加权平均计算黑线中心位置偏移 */
static int16_t _CalcError(uint8_t digital)
{
    int16_t sum_weighted = 0;
    int8_t  sum_count    = 0;
    int8_t  i;

    for (i = 0; i < 8; i++) {
        /* digital中bit=0表示检测到黑场 */
        if ((digital & (1U << i)) == 0) {
            sum_weighted += s_weights[i];
            sum_count++;
        }
    }

    if (sum_count == 0) {
        /* 全白场：8路全部返回1（白） → digital=0xFF */
        return 0;  /* 无有效信息，返回0保持直行 */
    }

    /* 加权平均得到误差值 */
    return (int16_t)(sum_weighted / sum_count);
}

/* ============================ 丢线判定 ============================ */

/* 判断是否丢线（8路全白 = digital == 0xFF）
 * 也可根据传感器实测数据调整判断逻辑 */
static uint8_t _IsLineLost(uint8_t digital)
{
    /* 8路全部检测为白场（bit全为1） */
    return (digital == 0xFF) ? 1 : 0;
}

/* ============================ 核心循迹任务 ============================ */

void App_Line_Task(void)
{
    uint8_t digital;
    int16_t error;
    int16_t pwm_out;
    int16_t left_pwm;
    int16_t right_pwm;

    /* 仅在RUNNING状态执行 */
    if (App_State_Get() != APP_STATE_RUNNING) {
        return;
    }

    /* 读取灰度数字量 */
    if (BSP_Gray_GetDigital(&digital) != 0) {
        /* I2C读取失败：停止电机，安全优先 */
        BSP_Motor_Stop();
        s_status = LINE_LOST_LONG;
        return;
    }

    /* 计算黑线位置误差 */
    error = _CalcError(digital);

    /* 记录本次计算的原始error，用于扑克牌遮线时保持正确的转向角度 */
    int16_t calc_error = error;

    /* 丢线检测与处理
     * 扑克牌遮线处理：路径是圆形的，不是直线。
     * 遮线时保持遮线前的偏转角度继续运动，而不是直行。 */
    if (_IsLineLost(digital)) {
        s_lost_cnt++;

        if (s_lost_cnt < LOST_THRESH_SHORT) {
            /* 短暂丢线（<150ms）：保持上一周期的error值，让车直行 */
            error = s_last_error;
            s_status = LINE_LOST_SHORT;
        } else if (s_lost_cnt < CARD_COVER_THRESH) {
            /* 扑克牌遮线（150ms~150ms，持续白场）：
             * 保持遮线前的error角度，保持原有转向运动，
             * 因为圆环路径不是直线，保持偏转角度才能沿圆弧继续行进 */
            error = s_last_error;
            s_status = LINE_CARD_COVER;
        } else if (s_lost_cnt < LOST_THRESH_MED) {
            /* 中等丢线（150~500ms）：降速并保持方向找线 */
            s_status = LINE_LOST_MED;
        } else if (s_lost_cnt < LOST_THRESH_LONG) {
            /* 接近超时：再降速 */
            s_status = LINE_LOST_MED;
        } else {
            /* 长时间丢线（>600ms）：停车，进入错误状态 */
            BSP_Motor_Stop();
            s_status = LINE_LOST_LONG;
            App_State_Set(APP_STATE_ERROR);
            return;
        }
    } else {
        /* 检测到黑线：重置丢线计数 */
        s_lost_cnt = 0;
        s_status = LINE_OK;
    }

    /* 一圈计数：根据当前题目决定行为
     * 基础题(B)：跑完一圈声光提示，再跑完一圈停车（共两圈）
     * 发挥题(F)：跑完一圈停车 */
    if (s_status != LINE_LOST_LONG) {
        QuestionType_t q = App_State_GetQuestion();
        int32_t target = (q == QUESTION_B) ? (ONE_LAP_COUNT * 2) : ONE_LAP_COUNT;

        /* 取绝对值累加：不管 LEFT_ENCODER_DIR 方向如何，编码器值只表示距离 */
        int16_t delta_l = BSP_Encoder_GetLeftDelta();
        int16_t delta_r = BSP_Encoder_GetRightDelta();
        s_enc_total += (int32_t)abs(delta_l);
        s_enc_total += (int32_t)abs(delta_r);
        if (s_enc_total > ENC_TOTAL_MAX) {
            s_enc_total = ENC_TOTAL_MAX;
        }

        /* F题：一圈完成停车时通知K230停止记录 */
        if (q == QUESTION_B && !s_alert_done && s_enc_total >= ONE_LAP_COUNT) {
            s_alert_done = 1;
            BSP_Beep_On();
            BSP_LED_On();
            s_alert_tick = HAL_GetTick();
        }

        /* B题：声光提示1秒后自动关闭 */
        if (q == QUESTION_B && s_alert_done && !s_alert_off_done) {
            if ((HAL_GetTick() - s_alert_tick) >= ALERT_DURATION_MS) {
                BSP_Beep_Off();
                BSP_LED_Off();
                s_alert_off_done = 1;
            }
        }

        /* F题：一圈完成时通知K230停止记录 */
        if (q == QUESTION_F && s_enc_total >= target) {
            BSP_UART_K230_SendStop();
        }

        /* 达到目标圈数停车 */
        if (s_enc_total >= target) {
            BSP_Beep_Off();
            BSP_LED_Off();
            BSP_Motor_Stop();
            App_Line_RecordFinish();  /* 记录停车时刻和编码器快照 */
            App_State_Set(APP_STATE_FINISHED);
            BSP_OLED_SwitchPage(OLED_PAGE_STANDBY);
            return;
        }
    }

    /* 积分项：仅在正常循迹时累加，限幅防止积分饱和 */
    if (s_status == LINE_OK) {
        s_integral += (int32_t)error;
        if (s_integral > INTEGRAL_MAX) s_integral = INTEGRAL_MAX;
        if (s_integral < INTEGRAL_MIN) s_integral = INTEGRAL_MIN;
    }

    /* PID控制计算
     * P项：s_Kp * error
     * I项：s_Ki * 积分累计（s_integral），按周期缩放
     * D项：s_Kd * (error - s_last_error)
     * 周期为5ms，Ki需配合周期调整，通常 Ki << Kp */
    {
        LineParams_t params;
        App_Line_GetQuestionParams(App_State_GetQuestion(), &params);
        s_base_pwm = params.base_pwm;
        s_Kp       = params.Kp;
        s_Ki       = params.Ki;
        s_Kd       = params.Kd;
    }

    pwm_out = (int16_t)(s_Kp * error / 10
                        + s_Ki * s_integral / 1000 / 10
                        + s_Kd * (error - s_last_error) / 10);

    /* 保存error供下次微分计算
     * 扑克牌遮线时：不更新s_last_error，保持遮线前的转向角度，
     * 这样在持续遮线期间角度不会衰减，确保沿圆弧路径行进 */
    if (s_status == LINE_OK) {
        s_last_error = calc_error;
    }
    /* LINE_LOST_SHORT / LINE_CARD_COVER / LINE_LOST_MED / LINE_LOST_LONG：
     * 都不更新s_last_error，保持遮线前的有效转向角度 */
    s_cur_error  = error;

    /* 计算左右轮PWM
     * 偏左 → error<0 → left_pwm增加 → 小车左转修正
     * 偏右 → error>0 → right_pwm增加 → 小车右转修正 */
    left_pwm  = s_base_pwm + pwm_out;
    right_pwm = s_base_pwm - pwm_out;

    /* 扑克牌遮线时：保持遮线前的error角度沿圆弧行进，不降速 */
    /* 中等丢线时降速（避免高速撞线） */
    if (s_status == LINE_LOST_MED) {
        int16_t factor = (s_lost_cnt < (LOST_THRESH_SHORT + LOST_THRESH_MED) / 2)
                         ? 70 : 50;
        left_pwm  = (int16_t)(left_pwm  * factor / 100);
        right_pwm = (int16_t)(right_pwm * factor / 100);
    }

    /* PWM限幅 */
    if (left_pwm > MOTOR_PWM_MAX)  left_pwm  = MOTOR_PWM_MAX;
    if (left_pwm < -MOTOR_PWM_MAX) left_pwm  = -MOTOR_PWM_MAX;
    if (right_pwm > MOTOR_PWM_MAX) right_pwm = MOTOR_PWM_MAX;
    if (right_pwm < -MOTOR_PWM_MAX) right_pwm = -MOTOR_PWM_MAX;

    /* 最低速度保护（避免死区） */
    if (left_pwm > 0 && left_pwm < 100)  left_pwm  = 100;
    if (right_pwm > 0 && right_pwm < 100) right_pwm = 100;

    /* 输出到电机 */
    BSP_Motor_Set(left_pwm, right_pwm);
}

/* ============================ 参数读写 ============================ */

void App_Line_SetParams(int16_t base_pwm, int16_t Kp, int16_t Ki, int16_t Kd)
{
    LineParams_t params;
    params.base_pwm = base_pwm;
    params.Kp = Kp;
    params.Ki = Ki;
    params.Kd = Kd;
    App_Line_SetQuestionParams(App_State_GetQuestion(), &params);
}

void App_Line_GetParams(LineParams_t *params)
{
    App_Line_GetQuestionParams(App_State_GetQuestion(), params);
}

void App_Line_GetQuestionParams(QuestionType_t q, LineParams_t *params)
{
    if (params != NULL && q >= 0 && q < QUESTION_COUNT) {
        *params = s_question_params[q];
    }
}

void App_Line_SetQuestionParams(QuestionType_t q, const LineParams_t *params)
{
    if (params != NULL && q >= 0 && q < QUESTION_COUNT) {
        s_question_params[q] = *params;

        if (s_question_params[q].base_pwm < LINE_BASE_PWM_MIN) {
            s_question_params[q].base_pwm = LINE_BASE_PWM_MIN;
        }
        if (s_question_params[q].base_pwm > LINE_BASE_PWM_MAX) {
            s_question_params[q].base_pwm = LINE_BASE_PWM_MAX;
        }
        if (s_question_params[q].Kp < LINE_PID_MIN) {
            s_question_params[q].Kp = LINE_PID_MIN;
        }
        if (s_question_params[q].Kp > LINE_PID_MAX) {
            s_question_params[q].Kp = LINE_PID_MAX;
        }
        if (s_question_params[q].Ki < LINE_PID_MIN) {
            s_question_params[q].Ki = LINE_PID_MIN;
        }
        if (s_question_params[q].Ki > LINE_PID_MAX) {
            s_question_params[q].Ki = LINE_PID_MAX;
        }
        if (s_question_params[q].Kd < LINE_PID_MIN) {
            s_question_params[q].Kd = LINE_PID_MIN;
        }
        if (s_question_params[q].Kd > LINE_PID_MAX) {
            s_question_params[q].Kd = LINE_PID_MAX;
        }

        if (q == App_State_GetQuestion()) {
            s_base_pwm = s_question_params[q].base_pwm;
            s_Kp = s_question_params[q].Kp;
            s_Ki = s_question_params[q].Ki;
            s_Kd = s_question_params[q].Kd;
        }
    }
}

LineTuneParam_t App_Line_GetTuneParam(void)
{
    return s_tune_param;
}

void App_Line_NextTuneParam(void)
{
    if (s_tune_param == LINE_TUNE_KD) {
        s_tune_param = LINE_TUNE_BASE_PWM;
    } else {
        s_tune_param = (LineTuneParam_t)(s_tune_param + 1);
    }
}

void App_Line_AdjustQuestionTuneParam(QuestionType_t q, int16_t delta)
{
    LineParams_t params;
    int16_t *target = NULL;
    int16_t step = LINE_PID_STEP;

    App_Line_GetQuestionParams(q, &params);

    switch (s_tune_param) {
    case LINE_TUNE_BASE_PWM:
        target = &params.base_pwm;
        step = LINE_BASE_PWM_STEP;
        break;
    case LINE_TUNE_KP:
        target = &params.Kp;
        break;
    case LINE_TUNE_KI:
        target = &params.Ki;
        break;
    case LINE_TUNE_KD:
    default:
        target = &params.Kd;
        break;
    }

    *target = (int16_t)(*target + delta * step);
    App_Line_SetQuestionParams(q, &params);
}

int16_t App_Line_GetError(void)
{
    return s_cur_error;
}

int32_t App_Line_GetEncTotal(void)
{
    return s_enc_total;
}

/* 记录K2按下时刻的快照：保存运行起点时间和当时编码器累计值 */
void App_Line_RecordSnapshot(void)
{
    s_snapshot_start_tick = HAL_GetTick();
}

/* 记录停车时刻，用于锁定用时和距离 */
void App_Line_RecordFinish(void)
{
    s_finish_tick = HAL_GetTick();
    s_finish_enc_total = s_enc_total;
}

/* 获取当前已经过的运行时间（秒），0表示未在运行（无快照记录）
 * 停车后返回最终用时，不再增长 */
uint32_t App_Line_GetRunningTimeS(void)
{
    if (s_snapshot_start_tick == 0) {
        return 0;
    }
    uint32_t end_tick = (s_finish_tick != 0) ? s_finish_tick : HAL_GetTick();
    return (end_tick - s_snapshot_start_tick) / 1000;
}

/* 获取当前已经过的运行时间，精确到0.01秒，0.0表示未在运行
 * 停车后返回最终用时，不再增长 */
float App_Line_GetRunningTime100ms(void)
{
    if (s_snapshot_start_tick == 0) {
        return 0.0f;
    }
    uint32_t end_tick = (s_finish_tick != 0) ? s_finish_tick : HAL_GetTick();
    return (float)(end_tick - s_snapshot_start_tick) / 1000.0f;
}

/* 获取当前已经过的运行时间（毫秒），0表示未在运行
 * 停车后返回最终用时，不再增长 */
uint32_t App_Line_GetRunningTimeMs(void)
{
    if (s_snapshot_start_tick == 0) {
        return 0;
    }
    uint32_t end_tick = (s_finish_tick != 0) ? s_finish_tick : HAL_GetTick();
    return end_tick - s_snapshot_start_tick;
}

/* 获取本次行驶的距离（cm）
 * 换算：43000计数 = 1圈 = π*D cm，代入 D=100cm，得 43000→314cm
 * 距离 = enc_total / ONE_LAP_COUNT * π * D */
float App_Line_GetSnapshotDist(void)
{
    if (s_finish_enc_total <= 0) {
        return 0.0f;
    }
    float dist_cm = (float)s_finish_enc_total / (float)ONE_LAP_COUNT * 3.14159f * WHEEL_DIAMETER_CM;
    return dist_cm;
}

LineStatus_t App_Line_GetStatus(void)
{
    return s_status;
}

void App_Line_PrintDebug(void)
{
    (void)s_cur_error;
    (void)s_status;
}
