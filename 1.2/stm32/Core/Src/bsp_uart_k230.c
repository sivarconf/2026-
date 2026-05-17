/**
 * @file    bsp_uart_k230.c
 * @brief   STM32与K230串口通信驱动
 *
 * 功能概述：
 * - STM32发送0x01(START)/0x02(STOP)给K230
 * - K230回0xA1(ACK START)/0xA2(ACK STOP)
 *
 * 硬件：USART1，波特率115200
 *
 * 版本：1.1
 * 日期：2026-05-15
 */

#include "bsp_uart_k230.h"
#include "user_config.h"

static uint8_t s_tx_buf[16];
static uint8_t s_rx_buf[64];

void BSP_UART_K230_Init(void)
{
    s_tx_buf[0] = '\0';
    s_rx_buf[0] = '\0';
}

void BSP_UART_K230_SendStart(void)
{
    uint8_t cmd = 0x01;
    HAL_UART_Transmit(&K230_UART_HANDLE, &cmd, 1, 100);
}

void BSP_UART_K230_SendStop(void)
{
    uint8_t cmd = 0x02;
    HAL_UART_Transmit(&K230_UART_HANDLE, &cmd, 1, 100);
}
