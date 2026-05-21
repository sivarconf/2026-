/**
 * @file    app_state.c
 * @brief   系统状态和题目类型管理
 *
 * 功能概述：
 * - 管理应用状态（IDLE/READY/RUNNING/ERROR/FINISHED）
 * - 管理题目类型（基础题B/挑战题F）
 *
 * 版本：2.0
 * 日期：2026-05-17
 */

#include "app_state.h"

static AppState_t s_app_state = APP_STATE_IDLE;
static QuestionType_t s_current_question = QUESTION_B;

void App_State_Init(void)
{
    s_app_state = APP_STATE_READY;
    s_current_question = QUESTION_B;
}

void App_State_Set(AppState_t state)
{
    s_app_state = state;
}

AppState_t App_State_Get(void)
{
    return s_app_state;
}

void App_State_SetQuestion(QuestionType_t q)
{
    if (q >= 0 && q < QUESTION_COUNT) {
        s_current_question = q;
    }
}

QuestionType_t App_State_GetQuestion(void)
{
    return s_current_question;
}
