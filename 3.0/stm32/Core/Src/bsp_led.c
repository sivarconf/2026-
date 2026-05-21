/**
 * @file    bsp_led.c
 * @brief   LED驱动模块
 *
 * 功能概述：
 * - 竞赛指示灯开关控制
 *
 * 硬件接口：PC13
 *
 * 版本：3.0
 * 日期：2026-05-21
 */

#include "bsp_led.h"

void BSP_LED_Init(void)
{
    BSP_LED_Off();
}

void BSP_LED_On(void)
{
    HAL_GPIO_WritePin(LED_PORT, LED_PIN, GPIO_PIN_SET);
}

void BSP_LED_Off(void)
{
    HAL_GPIO_WritePin(LED_PORT, LED_PIN, GPIO_PIN_RESET);
}

void BSP_LED_Toggle(void)
{
    HAL_GPIO_TogglePin(LED_PORT, LED_PIN);
}
