#ifndef __BSP_UART_K230_H
#define __BSP_UART_K230_H

#include "usart.h"
#include <stdint.h>

/**
 * BSP_UART_K230 - STM32 与 K230 串口通信驱动
 *
 * 通信协议（STM32 → K230）：单字节命令
 *   0x01: F题启动，K230 开始识别记录
 *   0x02: F题一圈完成，K230 停止识别记录
 *
 * K230 → STM32（ACK响应）：
 *   0xA1: ACK START
 *   0xA2: ACK STOP
 */

#define K230_UART_HANDLE   huart1

void BSP_UART_K230_Init(void);
void BSP_UART_K230_SendStart(void);
void BSP_UART_K230_SendStop(void);

#endif
