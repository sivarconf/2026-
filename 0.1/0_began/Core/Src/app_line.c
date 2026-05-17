#include "app_line.h"
#include "app_state.h"
#include "bsp_gray.h"
#include "bsp_motor.h"
#include "user_config.h"

/* ============================ 寻线黑线权重 ============================ */
/* 8路灰度传感器布置在车头，小车前进方向从左到右
 * 黑线位置对应权重（与前进方向相关）：
 *   偏左 → error > 0 → 需要右转（增加右PWM，减少左PWM）
 *   偏右 → error < 0 → 需要左转（增加左PWM，减少右PWM）
 * 权重根据传感器分布设计，使得:
 *   小车偏左时 error 为正，右轮加速
 *   小车偏右时 error 为负，左轮加速 */
static const int8_t s_weights[8] = {
    -40,  /* 第1路（最左）：大幅左转 */
    -20,  /* 第2路 */
    -10,  /* 第3路 */
    -5,   /* 第4路 */
     5,   /* 第5路 */
    10,  /* 第6路 */
    20,  /* 第7路 */
    40   /* 第8路（最右）：大幅右转 */
};

/* ============================ 默认参数（低速开环） ============================ */
/* 下地实测转向幅度偏大，默认降低 Kp 并加入少量 Kd 阻尼 */
static int16_t s_base_pwm   = 600;
static int16_t s_Kp         = 12;
static int16_t s_Ki         = 0;
static int16_t s_Kd         = 4;

/* ============================ 状态变量 ============================ */
static int16_t s_last_error = 0;   /* 上次error，用于微分项 */
static int32_t s_integral   = 0;   /* 积分累积，用于积分项 */
static uint16_t s_lost_cnt  = 0;   /* 丢线计数（单位：5ms周期） */
static LineStatus_t s_status = LINE_OK;
static int16_t s_cur_error  = 0;    /* 当前error缓存 */
static LineTuneParam_t s_tune_param = LINE_TUNE_KP;

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

/* ============================ 初始化 ============================ */

void App_Line_Init(void)
{
    s_last_error = 0;
    s_integral   = 0;
    s_lost_cnt   = 0;
    s_status     = LINE_OK;
    s_cur_error  = 0;
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

    /* 丢线检测与处理 */
    if (_IsLineLost(digital)) {
        s_lost_cnt++;

        if (s_lost_cnt < LOST_THRESH_SHORT) {
            /* 短丢线（<150ms）：保持上一周期的error值，让车直行 */
            error = s_last_error;
            s_status = LINE_LOST_SHORT;
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
    pwm_out = (int16_t)(s_Kp * error
                        + s_Ki * s_integral / 1000
                        + s_Kd * (error - s_last_error));

    /* 保存error供下次微分计算 */
    s_last_error = error;
    s_cur_error  = error;

    /* 计算左右轮PWM */
    left_pwm  = s_base_pwm - pwm_out;
    right_pwm = s_base_pwm + pwm_out;

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
    s_base_pwm = base_pwm;
    s_Kp = Kp;
    s_Ki = Ki;
    s_Kd = Kd;
}

void App_Line_GetParams(LineParams_t *params)
{
    if (params != NULL) {
        params->base_pwm = s_base_pwm;
        params->Kp      = s_Kp;
        params->Ki      = s_Ki;
        params->Kd      = s_Kd;
    }
}

LineTuneParam_t App_Line_GetTuneParam(void)
{
    return s_tune_param;
}

void App_Line_NextTuneParam(void)
{
    if (s_tune_param == LINE_TUNE_KD) {
        s_tune_param = LINE_TUNE_KP;
    } else {
        s_tune_param = (LineTuneParam_t)(s_tune_param + 1);
    }
}

void App_Line_AdjustTuneParam(int16_t delta)
{
    int16_t *target;

    switch (s_tune_param) {
    case LINE_TUNE_KP:
        target = &s_Kp;
        break;
    case LINE_TUNE_KI:
        target = &s_Ki;
        break;
    case LINE_TUNE_KD:
    default:
        target = &s_Kd;
        break;
    }

    *target = (int16_t)(*target + delta);
    if (*target < 0) {
        *target = 0;
    }
    if (*target > 999) {
        *target = 999;
    }
}

int16_t App_Line_GetError(void)
{
    return s_cur_error;
}

LineStatus_t App_Line_GetStatus(void)
{
    return s_status;
}

/* ============================ 调试输出（保留给后续串口恢复用） ============================ */

void App_Line_PrintDebug(void)
{
    (void)s_cur_error;
    (void)s_status;
}
