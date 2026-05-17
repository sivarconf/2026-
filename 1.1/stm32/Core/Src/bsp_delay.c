/**
 * @file    bsp_delay.c
 * @brief   微秒/毫秒延时模块
 *
 * 功能概述：
 * - 基于DWT CYCCNT的精确微秒延时
 * - 基于Delay_us的毫秒延时
 *
 * 版本：1.0
 * 日期：2026-05-13
 */

/**
 * @file    bsp_delay.c
 * @brief   微秒级延时驱动
 *
 * 功能概述：
 * - DWT微秒延时（SysTick补充）
 * - 微秒/毫秒延时函数
 *
 * 版本：1.0
 * 日期：2026-05-13
 */

#include "bsp_delay.h"
#include "core_cm3.h"

/* 使用DWT（Data Watchpoint and Trace）单元的CYCCNT寄存器实现精确微秒延时
 * DWT_CYCCNT是一个32位向上计数器，时钟频率 = CPU频率（72MHz @ STM32F103）
 * 计数器溢出周期 = 2^32 / 72000000 ≈ 59.65秒，足够覆盖一般延时需求 */

static uint32_t s_fac_us = 0;  /* us延时倍乘数 */

void Delay_Init(void)
{
    /* 使能DWT外设时钟 */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    /* 清零计数器 */
    DWT->CYCCNT = 0;
    /* 使能计数器 */
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

    /* 72MHz / 8 = 9MHz → 1us = 9个计数 */
    s_fac_us = SystemCoreClock / 8000000UL;
}

void Delay_us(uint32_t us)
{
    uint32_t ticks;
    uint32_t start;
    uint32_t elapsed;

    if (us == 0)
        return;

    if (s_fac_us == 0)
        Delay_Init();

    ticks = us * s_fac_us;
    start = DWT->CYCCNT;

    do {
        elapsed = DWT->CYCCNT - start;
    } while (elapsed < ticks);
}

void Delay_ms(uint32_t ms)
{
    for (uint32_t i = 0; i < ms; i++)
        Delay_us(1000);
}
