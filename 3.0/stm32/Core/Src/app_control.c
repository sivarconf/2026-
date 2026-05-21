/**
 * @file    app_control.c
 * @brief   电机控制调度模块
 *
 * 功能概述：
 * - RUNNING状态下执行循迹任务
 * - 非RUNNING状态下停止电机
 *
 * 版本：3.0
 * 日期：2026-05-21
 */

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
    if (App_State_Get() == APP_STATE_RUNNING) {
        App_Line_Task();
        return;
    }
    BSP_Motor_Stop();
}
