/**
 * @file    bsp_motor.c
 * @brief   电机驱动模块
 *
 * 功能概述：
 * - 左右电机PWM输出和方向控制
 * - 电机启停及刹车
 *
 * 硬件接口：
 * - PWM：TIM2 CH1(左轮), TIM2 CH2(右轮)
 * - 方向：PB12/PB13(左轮IN1/IN2), PB14/PB15(右轮IN1/IN2)
 *
 * 版本：2.0
 * 日期：2026-05-17
 */

#include "bsp_motor.h"

static int16_t s_left_pwm = 0;
static int16_t s_right_pwm = 0;

static uint16_t Motor_AbsLimit(int16_t pwm)
{
    if (pwm < 0)
    {
        pwm = -pwm;
    }
    if (pwm > MOTOR_PWM_MAX)
    {
        pwm = MOTOR_PWM_MAX;
    }
    return (uint16_t)pwm;
}

static void Motor_SetDirection(GPIO_TypeDef *in1_port, uint16_t in1_pin,
                               GPIO_TypeDef *in2_port, uint16_t in2_pin,
                               int16_t pwm)
{
    if (pwm > 0)
    {
        HAL_GPIO_WritePin(in1_port, in1_pin, GPIO_PIN_SET);
        HAL_GPIO_WritePin(in2_port, in2_pin, GPIO_PIN_RESET);
    }
    else if (pwm < 0)
    {
        HAL_GPIO_WritePin(in1_port, in1_pin, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(in2_port, in2_pin, GPIO_PIN_SET);
    }
    else
    {
        HAL_GPIO_WritePin(in1_port, in1_pin, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(in2_port, in2_pin, GPIO_PIN_RESET);
    }
}

void BSP_Motor_Init(void)
{
    HAL_TIM_PWM_Start(&L_PWM_TIM, L_PWM_CH);
    HAL_TIM_PWM_Start(&R_PWM_TIM, R_PWM_CH);
    BSP_Motor_Stop();
}

void BSP_Motor_SetLeft(int16_t pwm)
{
    s_left_pwm = pwm;
    Motor_SetDirection(L_IN1_PORT, L_IN1_PIN, L_IN2_PORT, L_IN2_PIN, pwm);
    __HAL_TIM_SET_COMPARE(&L_PWM_TIM, L_PWM_CH, Motor_AbsLimit(pwm));
}

void BSP_Motor_SetRight(int16_t pwm)
{
    s_right_pwm = pwm;
    Motor_SetDirection(R_IN1_PORT, R_IN1_PIN, R_IN2_PORT, R_IN2_PIN, pwm);
    __HAL_TIM_SET_COMPARE(&R_PWM_TIM, R_PWM_CH, Motor_AbsLimit(pwm));
}

void BSP_Motor_Set(int16_t left_pwm, int16_t right_pwm)
{
    BSP_Motor_SetLeft(left_pwm);
    BSP_Motor_SetRight(right_pwm);
}

void BSP_Motor_Stop(void)
{
    __HAL_TIM_SET_COMPARE(&L_PWM_TIM, L_PWM_CH, 0);
    __HAL_TIM_SET_COMPARE(&R_PWM_TIM, R_PWM_CH, 0);
    HAL_GPIO_WritePin(L_IN1_PORT, L_IN1_PIN, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(L_IN2_PORT, L_IN2_PIN, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(R_IN1_PORT, R_IN1_PIN, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(R_IN2_PORT, R_IN2_PIN, GPIO_PIN_RESET);
}

void BSP_Motor_Brake(void)
{
    __HAL_TIM_SET_COMPARE(&L_PWM_TIM, L_PWM_CH, 0);
    __HAL_TIM_SET_COMPARE(&R_PWM_TIM, R_PWM_CH, 0);
    HAL_GPIO_WritePin(L_IN1_PORT, L_IN1_PIN, GPIO_PIN_SET);
    HAL_GPIO_WritePin(L_IN2_PORT, L_IN2_PIN, GPIO_PIN_SET);
    HAL_GPIO_WritePin(R_IN1_PORT, R_IN1_PIN, GPIO_PIN_SET);
    HAL_GPIO_WritePin(R_IN2_PORT, R_IN2_PIN, GPIO_PIN_SET);
}

int16_t BSP_Motor_GetLeftPWM(void)
{
    return s_left_pwm;
}

int16_t BSP_Motor_GetRightPWM(void)
{
    return s_right_pwm;
}
