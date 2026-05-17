#ifndef __APP_STATE_H
#define __APP_STATE_H

#include "stdint.h"

typedef enum
{
    APP_STATE_IDLE = 0,
    APP_STATE_READY,
    APP_STATE_MOTOR_TEST,
    APP_STATE_ENCODER_TEST,
    APP_STATE_RUNNING,
    APP_STATE_ERROR,
    APP_STATE_FINISHED
} AppState_t;

void App_State_Init(void);
void App_State_Set(AppState_t state);
AppState_t App_State_Get(void);

#endif
