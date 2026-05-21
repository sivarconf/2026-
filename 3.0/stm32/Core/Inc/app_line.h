#ifndef __APP_LINE_H
#define __APP_LINE_H

#include <stdint.h>
#include "app_state.h"

/* ============================ 循迹参数 ============================ */

typedef struct {
    int16_t base_pwm;
    int16_t Kp;
    int16_t Ki;
    int16_t Kd;
} LineParams_t;

/* ============================ 丢线策略 ============================ */

typedef enum {
    LINE_OK = 0,
    LINE_LOST_SHORT,
    LINE_LOST_MED,
    LINE_LOST_LONG,
    LINE_CARD_COVER
} LineStatus_t;

/* ============================ 初始化 ============================ */

void App_Line_Init(void);

/* ============================ 核心任务 ============================ */

void App_Line_Task(void);

/* ============================ 稳定模式 ============================ */

uint8_t App_Line_IsStableMode(void);
void App_Line_ToggleStableMode(void);

/* ============================ 参数读写 ============================ */

void App_Line_GetParams(LineParams_t *params);
void App_Line_GetQuestionParams(QuestionType_t q, LineParams_t *params);
void App_Line_SetQuestionParams(QuestionType_t q, const LineParams_t *params);
int16_t App_Line_GetError(void);
int32_t App_Line_GetEncTotal(void);
LineStatus_t App_Line_GetStatus(void);

/* ============================ 快照记录 ============================ */

void App_Line_RecordSnapshot(void);
void App_Line_RecordFinish(void);
uint32_t App_Line_GetRunningTimeS(void);
float App_Line_GetRunningTime100ms(void);
uint32_t App_Line_GetRunningTimeMs(void);
float App_Line_GetSnapshotDist(void);

/* ============================ 声光提示 ============================ */

void App_Line_CheckAlertOff(void);

#endif
