/**
 * @file    bsp_encoder.c
 * @brief   编码器驱动模块
 *
 * 功能概述：
 * - 左右轮编码器计数读取
 * - 增量计算（支持方向反转）
 *
 * 硬件接口：
 * - 左轮编码器：TIM3
 * - 右轮编码器：TIM4
 *
 * 版本：1.0
 * 日期：2026-05-13
 */

/**
 * @file    bsp_encoder.c
 * @brief   编码器驱动
 *
 * 功能概述：
 * - 读取左右轮编码器增量
 * - 编码器累加值用于一圈计数判定
 *
 * 硬件：TIM3(左轮)/TIM4(右轮)，编码器模式
 *
 * 版本：1.0
 * 日期：2026-05-13
 */

#include "bsp_encoder.h"

static uint16_t s_left_last = 0;
static uint16_t s_right_last = 0;

void BSP_Encoder_Init(void)
{
    HAL_TIM_Encoder_Start(&LEFT_ENCODER_TIM, TIM_CHANNEL_ALL);
    HAL_TIM_Encoder_Start(&RIGHT_ENCODER_TIM, TIM_CHANNEL_ALL);
    BSP_Encoder_Reset();
}

void BSP_Encoder_Reset(void)
{
    __HAL_TIM_SET_COUNTER(&LEFT_ENCODER_TIM, 0);
    __HAL_TIM_SET_COUNTER(&RIGHT_ENCODER_TIM, 0);
    s_left_last = 0;
    s_right_last = 0;
}

int16_t BSP_Encoder_GetLeftDelta(void)
{
    uint16_t now = (uint16_t)__HAL_TIM_GET_COUNTER(&LEFT_ENCODER_TIM);
    int16_t delta = (int16_t)(now - s_left_last);
    s_left_last = now;
    return (int16_t)(delta * LEFT_ENCODER_DIR);
}

int16_t BSP_Encoder_GetRightDelta(void)
{
    uint16_t now = (uint16_t)__HAL_TIM_GET_COUNTER(&RIGHT_ENCODER_TIM);
    int16_t delta = (int16_t)(now - s_right_last);
    s_right_last = now;
    return (int16_t)(delta * RIGHT_ENCODER_DIR);
}
