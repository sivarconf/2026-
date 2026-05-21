/**
 * @file    bsp_key.c
 * @brief   按键驱动模块
 *
 * 功能概述：
 * - 4个按键扫描（K1/K2/K3/K4）
 * - 按键去抖处理
 * - 返回按下键的ID
 *
 * 硬件接口：PB0/K2, PB1/K1, PB10/K3, PB11/K4
 *
 * 版本：2.0
 * 日期：2026-05-17
 */

#include "bsp_key.h"

typedef struct
{
    GPIO_TypeDef *port;
    uint16_t pin;
} KeyPin_t;

static const KeyPin_t s_key_pins[] = {
    {0, 0},
    {KEY1_PORT, KEY1_PIN},
    {KEY2_PORT, KEY2_PIN},
    {KEY3_PORT, KEY3_PIN},
    {KEY4_PORT, KEY4_PIN},
};

void BSP_Key_Init(void)
{
}

uint8_t BSP_Key_IsPressed(KeyId_t key)
{
    if (key <= KEY_ID_NONE || key > KEY_ID_4)
    {
        return 0;
    }

    return HAL_GPIO_ReadPin(s_key_pins[key].port, s_key_pins[key].pin) == KEY_PRESSED_LEVEL;
}

KeyId_t BSP_Key_ScanPressed(void)
{
    static uint32_t last_scan_tick = 0;
    static uint8_t last_state[5] = {0};
    uint32_t now = HAL_GetTick();

    if (now - last_scan_tick < KEY_TASK_PERIOD_MS)
    {
        return KEY_ID_NONE;
    }
    last_scan_tick = now;

    for (uint8_t i = KEY_ID_1; i <= KEY_ID_4; i++)
    {
        uint8_t pressed = BSP_Key_IsPressed((KeyId_t)i);
        if (pressed && !last_state[i])
        {
            last_state[i] = pressed;
            return (KeyId_t)i;
        }
        last_state[i] = pressed;
    }

    return KEY_ID_NONE;
}
