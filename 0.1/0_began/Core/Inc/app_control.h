#ifndef __APP_CONTROL_H
#define __APP_CONTROL_H

#include "bsp_key.h"
#include "stdint.h"

void App_Control_Init(void);
void App_Control_Task(void);
void App_Control_StartMotorTest(KeyId_t key);
void App_Control_StartEncoderTest(uint8_t which);
void App_Control_EncoderTestTask(void);

/* 循迹参数控制 */
void App_Control_SetLineParams(int16_t base_pwm, int16_t Kp, int16_t Ki, int16_t Kd);

#endif
