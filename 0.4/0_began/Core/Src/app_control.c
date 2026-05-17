#include "app_control.h"
#include "app_line.h"
#include "app_state.h"
#include "bsp_motor.h"

void App_Control_Init(void)
{
    BSP_Motor_Stop();
    App_Line_Init();
}

void App_Control_Task(void)
{
    AppState_t state = App_State_Get();

    if (state == APP_STATE_RUNNING)
    {
        App_Line_Task();
        return;
    }

    /* 非 RUNNING 状态：停止电机 */
    BSP_Motor_Stop();
}

void App_Control_SetLineParams(int16_t base_pwm, int16_t Kp, int16_t Ki, int16_t Kd)
{
    (void)base_pwm;
    (void)Kp;
    (void)Ki;
    (void)Kd;
    App_Line_SetParams(base_pwm, Kp, Ki, Kd);
}
