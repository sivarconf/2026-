#ifndef __BSP_KEY_H
#define __BSP_KEY_H

#include "user_config.h"

typedef enum
{
    KEY_ID_NONE = 0,
    KEY_ID_1,
    KEY_ID_2,
    KEY_ID_3,
    KEY_ID_4
} KeyId_t;

void BSP_Key_Init(void);
uint8_t BSP_Key_IsPressed(KeyId_t key);
KeyId_t BSP_Key_ScanPressed(void);

#endif
