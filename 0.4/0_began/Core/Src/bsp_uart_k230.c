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
