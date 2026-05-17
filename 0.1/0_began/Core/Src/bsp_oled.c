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
static void Page_Debug(void);

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
    case OLED_PAGE_DEBUG:
        Page_Debug();
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

    AppState_t state = App_State_Get();
    const char *mode_str;
    switch (state) {
    case APP_STATE_IDLE:
    case APP_STATE_READY:
        mode_str = "MODE: BASIC";
        break;
    case APP_STATE_MOTOR_TEST:
        mode_str = "MODE: MOTOR";
        break;
    case APP_STATE_ENCODER_TEST:
        mode_str = "MODE: ENC  ";
        break;
    case APP_STATE_RUNNING:
        mode_str = "MODE: RUN  ";
        break;
    case APP_STATE_ERROR:
        mode_str = "MODE: ERROR";
        break;
    case APP_STATE_FINISHED:
        mode_str = "MODE: DONE ";
        break;
    default:
        mode_str = "MODE: UNK  ";
        break;
    }

    OLED_ShowString(0, 0,  (char *)mode_str, OLED_8X16);
    OLED_ShowString(0, 16, "K2: START     ", OLED_8X16);
    OLED_ShowString(0, 32, "K1: MODE      ", OLED_8X16);
    OLED_ShowString(0, 48, "BAT: OK       ", OLED_8X16);
}

static void Page_Run(void)
{
    OLED_Clear();

    int16_t enc_l = BSP_Encoder_GetLeftDelta();
    int16_t enc_r = BSP_Encoder_GetRightDelta();
    char line0[32];
    snprintf(line0, sizeof(line0), "L:%5d R:%5d", (int)enc_l, (int)enc_r);
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
    char line3[32];
    snprintf(line3, sizeof(line3), "GRAY:0x%02X     ", digital);
    OLED_ShowString(0, 48, line3, OLED_8X16);
}

static void Page_Debug(void)
{
    OLED_Clear();

    LineParams_t params;
    LineTuneParam_t tune = App_Line_GetTuneParam();
    App_Line_GetParams(&params);

    char line0[32];
    snprintf(line0, sizeof(line0), "KP:%03d.%02d%c    ",
             (int)(params.Kp / 100), (int)(params.Kp % 100),
             tune == LINE_TUNE_KP ? '<' : ' ');
    OLED_ShowString(0, 0, line0, OLED_8X16);

    char line1[32];
    snprintf(line1, sizeof(line1), "KI:%03d.%02d%c    ",
             (int)(params.Ki / 100), (int)(params.Ki % 100),
             tune == LINE_TUNE_KI ? '<' : ' ');
    OLED_ShowString(0, 16, line1, OLED_8X16);

    char line2[32];
    snprintf(line2, sizeof(line2), "KD:%03d.%02d%c    ",
             (int)(params.Kd / 100), (int)(params.Kd % 100),
             tune == LINE_TUNE_KD ? '<' : ' ');
    OLED_ShowString(0, 32, line2, OLED_8X16);

    char line3[32];
    snprintf(line3, sizeof(line3), "ERR:%4d B:%4d ",
             (int)App_Line_GetError(), (int)params.base_pwm);
    OLED_ShowString(0, 48, line3, OLED_8X16);
}
