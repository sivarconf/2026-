#ifndef __BSP_UART_K230_H
#define __BSP_UART_K230_H

#include "usart.h"
#include <stdint.h>

/**
 * BSP_UART_K230 - STM32 与 K230 串口通信驱动
 *
 * 通信格式：每条消息以 '$' 开头，以 '\n' 结尾
 *
 * STM32 -> K230:
 *   $START\n    : F题按下K2启动，开始识别记录
 *   $STOP\n     : F题跑完一圈，停止识别记录
 *
 * K230 -> STM32:
 *   $CARD,<suit>,<rank>\n  : 识别到扑克牌（suit: S/H/C/D/JB/JR, rank: A/2-10/J/Q/K）
 *   $ACK\n      : 确认收到START/STOP
 */

#define K230_UART_HANDLE   huart1

void BSP_UART_K230_Init(void);
void BSP_UART_K230_SendStart(void);
void BSP_UART_K230_SendStop(void);

#endif
