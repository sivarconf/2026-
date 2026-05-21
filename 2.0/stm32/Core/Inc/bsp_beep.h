#ifndef __BSP_BEEP_H
#define __BSP_BEEP_H

#include "user_config.h"

void BSP_Beep_Init(void);
void BSP_Beep_On(void);
void BSP_Beep_Off(void);
void BSP_Beep_Toggle(void);
void BSP_Beep_Blocking(uint32_t duration_ms);

#endif
