#ifndef __BSP_OLED_H
#define __BSP_OLED_H

#include "OLED.h"

typedef enum
{
    OLED_PAGE_STANDBY = 0,
    OLED_PAGE_PARAM_B,
    OLED_PAGE_PARAM_F,
} OLED_Page_t;

void BSP_OLED_Init(void);
void BSP_OLED_Task(void);
void BSP_OLED_SwitchPage(OLED_Page_t page);
OLED_Page_t BSP_OLED_GetPage(void);

#endif
