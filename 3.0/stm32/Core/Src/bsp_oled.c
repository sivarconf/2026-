/**
 * @file    bsp_oled.c
 * @brief   OLED显示屏驱动模块
 *
 * 功能概述：
 * - OLED显示页面管理（待机页）
 * - 周期刷新（100ms）
 *
 * 硬件接口：硬件I2C1（PB8=SCL, PB9=SDA）
 *
 * 版本：3.0
 * 日期：2026-05-21
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

    QuestionType_t q = App_State_GetQuestion();
    uint8_t digital = 0;
    BSP_Gray_GetDigital(&digital);

    /* 第一行：ti:B(C) 00000000（16字符填满128px，bit0最左bit7最右）
     *         ti:F(S) 00000000  C=竞赛模式 S=稳定模式 */
    char q_ch = (q == QUESTION_B) ? 'B' : 'F';
    char m_ch = App_Line_IsStableMode() ? 'S' : 'C';
    char line0[17];
    snprintf(line0, sizeof(line0), "ti:%c(%c) %u%u%u%u%u%u%u%u",
             q_ch, m_ch,
             (unsigned int)((digital >> 0) & 1),
             (unsigned int)((digital >> 1) & 1),
             (unsigned int)((digital >> 2) & 1),
             (unsigned int)((digital >> 3) & 1),
             (unsigned int)((digital >> 4) & 1),
             (unsigned int)((digital >> 5) & 1),
             (unsigned int)((digital >> 6) & 1),
             (unsigned int)((digital >> 7) & 1));
    OLED_ShowString(0, 0, line0, OLED_8X16);

    /* 第二行：行驶距离（cm）
     * 公式：dist_cm = enc_total / ONE_LAP_COUNT * pi * WHEEL_DIAMETER_CM
     * 车轮直径由 user_config.h 的 WHEEL_DIAMETER_CM 定义 */
    int32_t enc_total = App_Line_GetEncTotal();
    char line1[32];
    if (enc_total > 0) {
        int32_t dist_cm = (int32_t)((float)enc_total / (float)ONE_LAP_COUNT * 3.14159f * (float)WHEEL_DIAMETER_CM);
        snprintf(line1, sizeof(line1), "Dis:%ldcm    ", (long)dist_cm);
    } else {
        snprintf(line1, sizeof(line1), "Dis:----cm   ");
    }
    OLED_ShowString(0, 16, line1, OLED_8X16);

    /* 第三行：行驶用时（精确到0.01秒） */
    uint32_t time_ms = App_Line_GetRunningTimeMs();
    char line2[32];
    if (time_ms > 0) {
        snprintf(line2, sizeof(line2), "Tim:%lu.%02lus  ",
                 (unsigned long)(time_ms / 1000), (unsigned long)((time_ms % 1000) / 10));
    } else {
        snprintf(line2, sizeof(line2), "Tim:----s     ");
    }
    OLED_ShowString(0, 32, line2, OLED_8X16);

    /* 第四行：左右电机PWM */
    char line3[32];
    snprintf(line3, sizeof(line3), "L:%4d  R:%4d",
             (int)BSP_Motor_GetLeftPWM(), (int)BSP_Motor_GetRightPWM());
    OLED_ShowString(0, 48, line3, OLED_8X16);
}
