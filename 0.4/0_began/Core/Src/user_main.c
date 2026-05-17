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

        OLED_Page_t page = BSP_OLED_GetPage();

        /* ============================================================
         * 调参页按键处理（待机页/完成页均可进入调参页）
         * K1: 循环切换 待机页 → B题参数页 → F题参数页 → 待机页
         * K2: 选择下一个参数（BASE → KP → KI → KD 循环）
         * K3: 当前选中参数 +1
         * K4: 当前选中参数 -1
         * ============================================================ */
        if (key == KEY_ID_1)
        {
            if (page == OLED_PAGE_STANDBY)
            {
                BSP_OLED_SwitchPage(OLED_PAGE_PARAM_B);
            }
            else if (page == OLED_PAGE_PARAM_B)
            {
                BSP_OLED_SwitchPage(OLED_PAGE_PARAM_F);
            }
            else if (page == OLED_PAGE_PARAM_F)
            {
                BSP_OLED_SwitchPage(OLED_PAGE_STANDBY);
            }
        }
        else if (key == KEY_ID_2)
        {
            if (page == OLED_PAGE_PARAM_B || page == OLED_PAGE_PARAM_F)
            {
                App_Line_NextTuneParam();
            }
            else
            {
                /* K2: 待机页/完成页/错误页按K2进入RUNNING */
                AppState_t st = App_State_Get();
                if (st == APP_STATE_READY || st == APP_STATE_FINISHED || st == APP_STATE_ERROR)
                {
                    BSP_OLED_SwitchPage(OLED_PAGE_RUN);
                    BSP_Encoder_Reset();
                    App_Line_Init();
                    App_Line_RecordSnapshot();
                    App_State_Set(APP_STATE_RUNNING);
                    /* F题：K2启动时立即发送$START通知K230开始识别记录 */
                    if (App_State_GetQuestion() == QUESTION_F)
                    {
                        BSP_UART_K230_SendStart();
                    }
                }
            }
        }
        else if (key == KEY_ID_3)
        {
            if (page == OLED_PAGE_PARAM_B)
            {
                App_Line_AdjustQuestionTuneParam(QUESTION_B, +1);
            }
            else if (page == OLED_PAGE_PARAM_F)
            {
                App_Line_AdjustQuestionTuneParam(QUESTION_F, +1);
            }
            else
            {
                /* K3: 待机页/完成页切换当前题目 */
                AppState_t st = App_State_Get();
                if (st == APP_STATE_READY || st == APP_STATE_FINISHED)
                {
                    QuestionType_t cur = App_State_GetQuestion();
                    QuestionType_t next = (cur == QUESTION_B) ? QUESTION_F : QUESTION_B;
                    App_State_SetQuestion(next);
                }
            }
        }
        else if (key == KEY_ID_4)
        {
            if (page == OLED_PAGE_PARAM_B)
            {
                App_Line_AdjustQuestionTuneParam(QUESTION_B, -1);
            }
            else if (page == OLED_PAGE_PARAM_F)
            {
                App_Line_AdjustQuestionTuneParam(QUESTION_F, -1);
            }
        }
    }

    /*K4: 运行时手动停车（RUNNING状态）*/
    if (App_State_Get() == APP_STATE_RUNNING)
    {
        if (HAL_GPIO_ReadPin(KEY4_PORT, KEY4_PIN) == KEY_PRESSED_LEVEL)
        {
            BSP_Motor_Stop();
            App_Line_RecordFinish();
            App_State_Set(APP_STATE_READY);
            BSP_OLED_SwitchPage(OLED_PAGE_STANDBY);
        }
    }

    App_Control_Task();
    BSP_OLED_Task();
}
