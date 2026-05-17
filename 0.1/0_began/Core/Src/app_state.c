#include "app_state.h"

static AppState_t s_app_state = APP_STATE_IDLE;

void App_State_Init(void)
{
    s_app_state = APP_STATE_READY;
}

void App_State_Set(AppState_t state)
{
    s_app_state = state;
}

AppState_t App_State_Get(void)
{
    return s_app_state;
}
