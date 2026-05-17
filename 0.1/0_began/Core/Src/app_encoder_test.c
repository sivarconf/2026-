#include "app_encoder_test.h"
#include "app_debug.h"
#include "app_state.h"
#include "bsp_motor.h"
#include "bsp_encoder.h"
#include "user_config.h"

#define ENC_TEST_PWM         300
#define ENC_TEST_DURATION_MS 1500

typedef enum {
    ENC_TEST_IDLE,
    ENC_TEST_RUNNING,
} EncTestState_t;

static EncTestState_t s_state = ENC_TEST_IDLE;
static uint32_t s_stop_tick = 0;
static int16_t s_left_total = 0;
static int16_t s_right_total = 0;

void App_EncoderTest_Init(void)
{
    s_state = ENC_TEST_IDLE;
    s_stop_tick = 0;
    s_left_total = 0;
    s_right_total = 0;
    BSP_Encoder_Reset();
}

void App_EncoderTest_Start(uint8_t which)
{
    if (s_state == ENC_TEST_RUNNING)
        return;

    BSP_Encoder_Reset();
    s_left_total = 0;
    s_right_total = 0;

    switch (which)
    {
    case 1:
        BSP_Motor_SetLeft(ENC_TEST_PWM);
        BSP_Motor_SetRight(0);
        App_Debug_Printf("enc test: left forward\r\n");
        break;
    case 2:
        BSP_Motor_SetLeft(0);
        BSP_Motor_SetRight(ENC_TEST_PWM);
        App_Debug_Printf("enc test: right forward\r\n");
        break;
    case 3:
        BSP_Motor_SetLeft(ENC_TEST_PWM);
        BSP_Motor_SetRight(ENC_TEST_PWM);
        App_Debug_Printf("enc test: both forward\r\n");
        break;
    default:
        return;
    }

    s_stop_tick = HAL_GetTick() + ENC_TEST_DURATION_MS;
    s_state = ENC_TEST_RUNNING;
}

void App_EncoderTest_Task(void)
{
    if (s_state != ENC_TEST_RUNNING)
        return;

    s_left_total += BSP_Encoder_GetLeftDelta();
    s_right_total += BSP_Encoder_GetRightDelta();

    if (HAL_GetTick() >= s_stop_tick)
    {
        BSP_Motor_Stop();
        s_state = ENC_TEST_IDLE;
        App_State_Set(APP_STATE_READY);

        App_Debug_Printf("enc result: left=%d, right=%d\r\n",
                         (int)s_left_total, (int)s_right_total);
        App_Debug_Printf("enc dir: left=%s, right=%s\r\n",
                         s_left_total > 0  ? "OK" : s_left_total < 0 ? "REVERSE" : "ZERO",
                         s_right_total > 0 ? "OK" : s_right_total < 0 ? "REVERSE" : "ZERO");
    }
}
