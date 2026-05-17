/**
 * @file    bsp_oled.c
 * @brief   OLED显示屏驱动模块
 *
 * 功能概述：
 * - OLED显示页面管理（待机页/运行页/调参B页/调参F页）
 * - 周期刷新（100ms）
 *
 * 硬件接口：硬件I2C1（PB8=SCL, PB9=SDA）
 *
 * 版本：1.0
 * 日期：2026-05-13
 */

/**
 * @file    bsp_oled.c
 * @brief   OLED显示驱动
 *
 * 功能概述：
 * - OLED页面管理（待机页/运行页/B题调参页/F题调参页）
 * - 实时显示题号/编码器/灰度/PWM/距离/用时等信息
 *
 * 硬件：SSD1306，I2C1(PB8/PB9)，128x64分辨率
 *
 * 版本：1.0
 * 日期：2026-05-13
 */

/**
 * @file    bsp_oled.c
 * @brief   OLED显示驱动
 *
 * 功能概述：
 * - OLED多页面显示管理
 * - 待机页(题号/距离/用时/电量)、运行页、调参页
 *
 * 硬件：I2C1(PB8=SCL, PB9=SDA)，SSD1306 128x64
 *
 * 版本：1.0
 * 日期：2026-05-13
 */

#include "bsp_oled.h"
#include "OLED.h"
#include "app_control.h"
#include "app_line.h"
#include "app_state.h"
#include "bsp_encoder.h"
#include "bsp_gray.h"
#include "bsp_motor.h"
#include "user_config.h"
#include <stdio.h>

static void Page_Standby(void);
static void Page_Run(void);
static void Page_Param(QuestionType_t q);

static OLED_Page_t s_current_page = OLED_PAGE_STANDBY;
static uint32_t s_last_refresh_tick = 0;

void BSP_OLED_Init(void)
{
    OLED_Init();
    s_current_page = OLED_PAGE_STANDBY;
    s_last_refresh_tick = 0;
}

void BSP_OLED_Task(void)
{
    uint32_t now = HAL_GetTick();
    if ((now - s_last_refresh_tick) < OLED_TASK_PERIOD_MS) {
        return;
    }
    s_last_refresh_tick = now;

    switch (s_current_page) {
    case OLED_PAGE_STANDBY:
        Page_Standby();
        break;
    case OLED_PAGE_RUN:
        Page_Run();
        break;
    case OLED_PAGE_PARAM_B:
        Page_Param(QUESTION_B);
        break;
    case OLED_PAGE_PARAM_F:
        Page_Param(QUESTION_F);
        break;
    default:
        Page_Standby();
        break;
    }

    OLED_Update();
}

void BSP_OLED_SwitchPage(OLED_Page_t page)
{
    if (s_current_page != page) {
        s_current_page = page;
        OLED_Clear();
    }
}

OLED_Page_t BSP_OLED_GetPage(void)
{
    return s_current_page;
}

static void Page_Standby(void)
{
    OLED_Clear();

    /* 第一行：ti: B / ti: F（题号） */
    QuestionType_t q = App_State_GetQuestion();
    char line0[32];
    if (q == QUESTION_B) {
        snprintf(line0, sizeof(line0), "ti: B          ");
    } else if (q == QUESTION_F) {
        snprintf(line0, sizeof(line0), "ti: F          ");
    } else {
        snprintf(line0, sizeof(line0), "ti: B%d         ", (int)q + 1);
    }
    OLED_ShowString(0, 0, line0, OLED_8X16);

    /* 第二行：行驶距离（cm） */
    int32_t enc_total = App_Line_GetEncTotal();
    char line1[32];
    if (enc_total > 0) {
        /* 43000计数=1圈=314cm，显示为"xxxcm"整数形式 */
        int32_t dist_cm = (int32_t)((float)enc_total / (float)ONE_LAP_COUNT * 314.0f);
        snprintf(line1, sizeof(line1), "Dis:%ldcm    ", (long)dist_cm);
    } else {
        snprintf(line1, sizeof(line1), "Dis:----cm   ");
    }
    OLED_ShowString(0, 16, line1, OLED_8X16);

    /* 第三行：行驶用时（精确到0.01秒） */
    uint32_t time_ms = App_Line_GetRunningTimeMs();
    char line2[32];
    if (time_ms > 0) {
        /* 显示为"xx.xxs"形式（精确到0.01秒） */
        snprintf(line2, sizeof(line2), "Tim:%lu.%02lus  ", (unsigned long)(time_ms / 1000), (unsigned long)((time_ms % 1000) / 10));
    } else {
        snprintf(line2, sizeof(line2), "Tim:----s     ");
    }
    OLED_ShowString(0, 32, line2, OLED_8X16);

    /* 第四行：电量 */
    OLED_ShowString(0, 48, "BAT: OK           ", OLED_8X16);
}

static void Page_Run(void)
{
    OLED_Clear();

    /* 第一行：题目号 + 左右轮编码增量 */
    QuestionType_t q = App_State_GetQuestion();
    int16_t enc_l = BSP_Encoder_GetLeftDelta();
    int16_t enc_r = BSP_Encoder_GetRightDelta();
    char line0[32];
    if (q == QUESTION_B) {
        snprintf(line0, sizeof(line0), "B  L:%4d R:%4d", (int)enc_l, (int)enc_r);
    } else if (q == QUESTION_F) {
        snprintf(line0, sizeof(line0), "F  L:%4d R:%4d", (int)enc_l, (int)enc_r);
    } else {
        snprintf(line0, sizeof(line0), "RUN L:%4d R:%4d", (int)enc_l, (int)enc_r);
    }
    OLED_ShowString(0, 0, line0, OLED_8X16);

    uint8_t gray_err = 0;
    BSP_Gray_GetError(&gray_err);
    char line1[32];
    snprintf(line1, sizeof(line1), "ERR: %3u        ", (unsigned int)gray_err);
    OLED_ShowString(0, 16, line1, OLED_8X16);

    char line2[32];
    snprintf(line2, sizeof(line2), "PWM:%4d %4d  ",
             (int)BSP_Motor_GetLeftPWM(), (int)BSP_Motor_GetRightPWM());
    OLED_ShowString(0, 32, line2, OLED_8X16);

    uint8_t digital = 0;
    BSP_Gray_GetDigital(&digital);
    /* 第四行：进度条 + 灰度digital
     * B题目标两圈，F题目标一圈 */
    char line3[32];
    if (q == QUESTION_B) {
        int32_t enc_total = App_Line_GetEncTotal();
        int32_t target2 = ONE_LAP_COUNT * 2;
        uint8_t pct = (uint8_t)((enc_total * 100) / target2);
        if (pct > 100) pct = 100;
        snprintf(line3, sizeof(line3), "GRAY:0x%02X P:%3d%%", digital, pct);
    } else {
        int32_t enc_total = App_Line_GetEncTotal();
        uint8_t pct = (uint8_t)((enc_total * 100) / ONE_LAP_COUNT);
        if (pct > 100) pct = 100;
        snprintf(line3, sizeof(line3), "GRAY:0x%02X P:%3d%%", digital, pct);
    }
    OLED_ShowString(0, 48, line3, OLED_8X16);
}

static void Page_Param(QuestionType_t q)
{
    OLED_Clear();

    LineParams_t params;
    LineTuneParam_t tune = App_Line_GetTuneParam();
    App_Line_GetQuestionParams(q, &params);

    char line0[32];
    snprintf(line0, sizeof(line0), "%c BASE:%4d%c  ",
             q == QUESTION_B ? 'B' : 'F',
             (int)params.base_pwm,
             tune == LINE_TUNE_BASE_PWM ? '<' : ' ');
    OLED_ShowString(0, 0, line0, OLED_8X16);

    /* PID参数内部扩大10倍存储，显示时除10还原
     * 用整数运算代替%f，避免STM32浮点格式化问题 */
    {
        char line1[32];
        int kp_int = params.Kp / 10;
        int kp_frac = params.Kp >= 0 ? (params.Kp % 10) : ((-params.Kp) % 10);
        snprintf(line1, sizeof(line1), "KP:%3d.%d%c  ",
                 kp_int, kp_frac,
                 tune == LINE_TUNE_KP ? '<' : ' ');
        OLED_ShowString(0, 16, line1, OLED_8X16);

        char line2[32];
        int ki_int = params.Ki / 10;
        int ki_frac = params.Ki >= 0 ? (params.Ki % 10) : ((-params.Ki) % 10);
        snprintf(line2, sizeof(line2), "KI:%3d.%d%c  ",
                 ki_int, ki_frac,
                 tune == LINE_TUNE_KI ? '<' : ' ');
        OLED_ShowString(0, 32, line2, OLED_8X16);

        char line3[32];
        int kd_int = params.Kd / 10;
        int kd_frac = params.Kd >= 0 ? (params.Kd % 10) : ((-params.Kd) % 10);
        snprintf(line3, sizeof(line3), "KD:%3d.%d%c  ",
                 kd_int, kd_frac,
                 tune == LINE_TUNE_KD ? '<' : ' ');
        OLED_ShowString(0, 48, line3, OLED_8X16);
    }
}
