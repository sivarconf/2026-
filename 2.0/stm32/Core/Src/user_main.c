/**
 * @file    user_main.c
 * @brief   应用层主程序入口
 *
 * 功能概述：
 * - 系统初始化（所有BSP外设初始化）
 * - 按键处理（K1切换稳定/竞赛模式、K2切题B/F、K3启动、K4手动停车）
 * - 主循环调度（App_Control_Task + BSP_OLED_Task）
 *
 * 版本：2.0
 * 日期：2026-05-17
 */

#include "user_main.h"

#include "app_control.h"
#include "app_line.h"
#include "app_state.h"
#include "bsp_beep.h"
#include "bsp_delay.h"
#include "bsp_encoder.h"
#include "bsp_gray.h"
#include "bsp_key.h"
#include "bsp_motor.h"
#include "bsp_oled.h"
#include "bsp_uart_k230.h"

void User_App_Init(void)
{
    Delay_Init();
    BSP_Beep_Init();
    BSP_Key_Init();
    BSP_LED_Init();
    BSP_Motor_Init();
    BSP_Encoder_Init();
    BSP_Gray_Init();
    BSP_OLED_Init();
    BSP_UART_K230_Init();

    App_State_Init();
    App_Control_Init();

    BSP_Beep_Blocking(80);
}

void User_App_Loop(void)
{
    KeyId_t key = BSP_Key_ScanPressed();

    if (key != KEY_ID_NONE)
    {
        BSP_Beep_Blocking(30);

        AppState_t st = App_State_Get();

        /* K1: 待机页/完成页/错误页切换稳定模式/竞赛模式 */
        if (key == KEY_ID_1)
        {
            if (st == APP_STATE_READY || st == APP_STATE_FINISHED)
            {
                App_Line_ToggleStableMode();
            }
        }
        /* K2: 待机页/完成页/错误页切换题目B/F */
        else if (key == KEY_ID_2)
        {
            if (st == APP_STATE_READY || st == APP_STATE_FINISHED)
            {
                QuestionType_t cur = App_State_GetQuestion();
                QuestionType_t next = (cur == QUESTION_B) ? QUESTION_F : QUESTION_B;
                App_State_SetQuestion(next);
            }
        }
        /* K3: 待机页/完成页/错误页启动 */
        else if (key == KEY_ID_3)
        {
            if (st == APP_STATE_READY || st == APP_STATE_FINISHED || st == APP_STATE_ERROR)
            {
                BSP_Encoder_Reset();
                App_Line_Init();
                App_Line_RecordSnapshot();
                App_State_Set(APP_STATE_RUNNING);
                if (App_State_GetQuestion() == QUESTION_F)
                {
                    BSP_UART_K230_SendStart();
                }
            }
        }
    }

    /* K4: 运行时手动停车（RUNNING状态，按键持续检测） */
    if (App_State_Get() == APP_STATE_RUNNING)
    {
        if (HAL_GPIO_ReadPin(KEY4_PORT, KEY4_PIN) == KEY_PRESSED_LEVEL)
        {
            BSP_Motor_Stop();
            BSP_Beep_Off();
            BSP_LED_Off();
            App_Line_RecordFinish();
            App_State_Set(APP_STATE_READY);
            BSP_OLED_SwitchPage(OLED_PAGE_STANDBY);
        }
    }

    App_Line_CheckAlertOff();

    App_Control_Task();
    BSP_OLED_Task();
}
