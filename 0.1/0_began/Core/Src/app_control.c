#include "app_control.h"
#include "app_debug.h"
#include "app_encoder_test.h"
#include "app_line.h"
#include "app_state.h"
#include "bsp_motor.h"
#include "user_config.h"

static uint32_t s_motor_test_stop_tick = 0;

void App_Control_Init(void)
{
    BSP_Motor_Stop();
    s_motor_test_stop_tick = 0;
    App_Line_Init();
}

void App_Control_Task(void)
{
    AppState_t state = App_State_Get();

    if (state == APP_STATE_MOTOR_TEST)
    {
        if (s_motor_test_stop_tick != 0 && HAL_GetTick() >= s_motor_test_stop_tick)
        {
            BSP_Motor_Stop();
            s_motor_test_stop_tick = 0;
            App_State_Set(APP_STATE_READY);
            App_Debug_Printf("motor test stop\r\n");
        }
        return;
    }

    if (state == APP_STATE_RUNNING)
    {
        App_Line_Task();
        return;
    }

    /* 非 RUNNING / MOTOR_TEST / ENCODER_TEST 状态：停止电机 */
    if (state != APP_STATE_ENCODER_TEST)
    {
        BSP_Motor_Stop();
    }
}

void App_Control_StartMotorTest(KeyId_t key)
{
    if (App_State_Get() == APP_STATE_MOTOR_TEST)
    {
        App_Debug_Printf("motor test busy\r\n");
        return;
    }

    BSP_Motor_Stop();

    switch (key)
    {
    case KEY_ID_1:
        BSP_Motor_SetLeft(MOTOR_TEST_PWM);
        App_Debug_Printf("motor test: left forward pwm=%d\r\n", MOTOR_TEST_PWM);
        break;
    case KEY_ID_2:
        BSP_Motor_SetLeft(-MOTOR_TEST_PWM);
        App_Debug_Printf("motor test: left backward pwm=%d\r\n", MOTOR_TEST_PWM);
        break;
    case KEY_ID_3:
        BSP_Motor_SetRight(MOTOR_TEST_PWM);
        App_Debug_Printf("motor test: right forward pwm=%d\r\n", MOTOR_TEST_PWM);
        break;
    case KEY_ID_4:
        BSP_Motor_SetRight(-MOTOR_TEST_PWM);
        App_Debug_Printf("motor test: right backward pwm=%d\r\n", MOTOR_TEST_PWM);
        break;
    default:
        return;
    }

    App_State_Set(APP_STATE_MOTOR_TEST);
    s_motor_test_stop_tick = HAL_GetTick() + MOTOR_TEST_DURATION_MS;
}

void App_Control_StartEncoderTest(uint8_t which)
{
    if (App_State_Get() == APP_STATE_ENCODER_TEST)
    {
        App_Debug_Printf("encoder test busy\r\n");
        return;
    }

    App_State_Set(APP_STATE_ENCODER_TEST);
    switch (which)
    {
    case KEY_ID_1:
        App_EncoderTest_Start(1);
        break;
    case KEY_ID_2:
        App_EncoderTest_Start(2);
        break;
    case KEY_ID_3:
        App_EncoderTest_Start(3);
        break;
    default:
        App_State_Set(APP_STATE_READY);
        return;
    }
}

void App_Control_EncoderTestTask(void)
{
    if (App_State_Get() == APP_STATE_ENCODER_TEST)
    {
        App_EncoderTest_Task();
    }
}

void App_Control_SetLineParams(int16_t base_pwm, int16_t Kp, int16_t Ki, int16_t Kd)
{
    (void)base_pwm;
    (void)Kp;
    (void)Ki;
    (void)Kd;
    /* 参数直接透传给 App_Line_SetParams */
    App_Line_SetParams(base_pwm, Kp, Ki, Kd);
}
