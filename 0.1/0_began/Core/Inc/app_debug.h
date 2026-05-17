#ifndef __APP_DEBUG_H
#define __APP_DEBUG_H

#include "stdint.h"
#include "app_state.h"

void App_Debug_Init(void);
void App_Debug_Task(void);
void App_Debug_Printf(const char *fmt, ...);

#endif
