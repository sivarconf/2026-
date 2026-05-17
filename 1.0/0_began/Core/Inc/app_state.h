#ifndef __APP_STATE_H
#define __APP_STATE_H

#include "stdint.h"

typedef enum
{
    APP_STATE_IDLE = 0,
    APP_STATE_READY,
    APP_STATE_RUNNING,
    APP_STATE_ERROR,
    APP_STATE_FINISHED
} AppState_t;

typedef enum
{
    QUESTION_B = 0,      /* 基础题：循迹一圈后停车 */
    QUESTION_F,          /* 挑战题：循迹一圈后声光提示，再跑完一圈停车 */
    QUESTION_COUNT
} QuestionType_t;

void App_State_Init(void);
void App_State_Set(AppState_t state);
AppState_t App_State_Get(void);

void App_State_SetQuestion(QuestionType_t q);
QuestionType_t App_State_GetQuestion(void);

#endif
