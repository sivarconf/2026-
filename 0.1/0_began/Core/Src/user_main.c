#include "user_main.h"

#include "app_control.h"
#include "app_debug.h"
#include "app_encoder_test.h"
#include "app_line.h"
#include "app_state.h"
#include "bsp_beep.h"
#include "bsp_delay.h"
#include "bsp_encoder.h"
#include "bsp_gray.h"
#include "bsp_key.h"
#include "bsp_motor.h"
#include "bsp_oled.h"

static void Handle_DebugPageKey(KeyId_t key)
{
    if (key == KEY_ID_2)
    {
        App_Line_NextTuneParam();
    }
    else if (key == KEY_ID_3)
    {
        App_Line_AdjustTuneParam(1);
    }
    else if (key == KEY_ID_4)
    {
        App_Line_AdjustTuneParam(-1);
    }
}

void User_App_Init(void)
{
    Delay_Init();
    BSP_Beep_Init();
    BSP_Key_Init();
    BSP_Motor_Init();
    BSP_Encoder_Init();
    BSP_Gray_Init();
    BSP_OLED_Init();

    App_State_Init();
    App_Control_Init();
    App_Debug_Init();
    App_EncoderTest_Init();

    BSP_Beep_Blocking(80);
}

void User_App_Loop(void)
{
    KeyId_t key = BSP_Key_ScanPressed();

    if (key != KEY_ID_NONE)
    {
        BSP_Beep_Blocking(30);

        if (key == KEY_ID_1)
        {
            /*K1: 切换OLED页面*/
            OLED_Page_t cur = BSP_OLED_GetPage();
            OLED_Page_t next = (cur + 1) % 3;
            BSP_OLED_SwitchPage(next);
        }
        else if (BSP_OLED_GetPage() == OLED_PAGE_DEBUG)
        {
            Handle_DebugPageKey(key);
        }
        else if (key == KEY_ID_2)
        {
            /*K2: 待机页/错误页按K2进入RUNNING，运行页/调试页K2无效*/
            AppState_t st = App_State_Get();
            if (st == APP_STATE_READY || st == APP_STATE_FINISHED || st == APP_STATE_ERROR)
            {
                BSP_Encoder_Reset();
                App_Line_Init();
                App_State_Set(APP_STATE_RUNNING);
                BSP_OLED_SwitchPage(OLED_PAGE_RUN);
            }
        }
        else
        {
            App_Control_StartMotorTest(key);
        }
    }

    /*系统状态为RUNNING时，按K4停止*/
    if (App_State_Get() == APP_STATE_RUNNING)
    {
        if (HAL_GPIO_ReadPin(KEY4_PORT, KEY4_PIN) == KEY_PRESSED_LEVEL)
        {
            BSP_Motor_Stop();
            App_State_Set(APP_STATE_READY);
            BSP_OLED_SwitchPage(OLED_PAGE_STANDBY);
        }
    }

    App_Control_Task();
    App_Control_EncoderTestTask();
    App_Debug_Task();
    BSP_OLED_Task();
}
