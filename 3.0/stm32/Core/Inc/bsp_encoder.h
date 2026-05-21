#ifndef __BSP_ENCODER_H
#define __BSP_ENCODER_H

#include "user_config.h"

void BSP_Encoder_Init(void);
void BSP_Encoder_Reset(void);
int16_t BSP_Encoder_GetLeftDelta(void);
int16_t BSP_Encoder_GetRightDelta(void);

#endif
