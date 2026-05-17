/**
 * @file    bsp_beep.c
 * @brief   蜂鸣器驱动模块
 *
 * 功能概述：
 * - 蜂鸣器开关控制
 * - 阻塞式蜂鸣（延时鸣叫）
 *
 * 硬件接口：PB3
 *
 * 版本：1.1
 * 日期：2026-05-15
 */

#include "bsp_beep.h"

void BSP_Beep_Init(void)
{
    BSP_Beep_Off();
}

void BSP_Beep_On(void)
{
    HAL_GPIO_WritePin(BEEP_PORT, BEEP_PIN, GPIO_PIN_SET);
}

void BSP_Beep_Off(void)
{
    HAL_GPIO_WritePin(BEEP_PORT, BEEP_PIN, GPIO_PIN_RESET);
}

void BSP_Beep_Toggle(void)
{
    HAL_GPIO_TogglePin(BEEP_PORT, BEEP_PIN);
}

void BSP_Beep_Blocking(uint32_t duration_ms)
{
    BSP_Beep_On();
    HAL_Delay(duration_ms);
    BSP_Beep_Off();
}
