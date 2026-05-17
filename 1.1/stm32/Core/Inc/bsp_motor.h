#ifndef __BSP_MOTOR_H
#define __BSP_MOTOR_H

#include "user_config.h"

void BSP_Motor_Init(void);
void BSP_Motor_SetLeft(int16_t pwm);
void BSP_Motor_SetRight(int16_t pwm);
void BSP_Motor_Set(int16_t left_pwm, int16_t right_pwm);
void BSP_Motor_Stop(void);
void BSP_Motor_Brake(void);
int16_t BSP_Motor_GetLeftPWM(void);
int16_t BSP_Motor_GetRightPWM(void);

#endif
