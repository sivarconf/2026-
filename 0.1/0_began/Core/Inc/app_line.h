#ifndef __APP_LINE_H
#define __APP_LINE_H

#include <stdint.h>

/* ============================ 循迹参数 ============================ */

typedef struct {
    int16_t base_pwm;    /* 基础PWM（直行速度） */
    int16_t Kp;          /* 比例系数 */
    int16_t Ki;          /* 积分系数 */
    int16_t Kd;          /* 微分系数 */
} LineParams_t;

/* ============================ 丢线策略 ============================ */

typedef enum {
    LINE_OK = 0,         /* 正常循迹 */
    LINE_LOST_SHORT,     /* 短时丢线（<150ms） */
    LINE_LOST_MED,       /* 中等丢线（150~500ms） */
    LINE_LOST_LONG       /* 长时间丢线（>500ms），需停止 */
} LineStatus_t;

typedef enum {
    LINE_TUNE_KP = 0,
    LINE_TUNE_KI,
    LINE_TUNE_KD
} LineTuneParam_t;

/* ============================ 初始化 ============================ */

void App_Line_Init(void);

/* ============================ 核心任务 ============================ */

/* 读取灰度并执行PD控制
 * 仅在 APP_STATE_RUNNING 时调用
 * 周期：LINE_TASK_PERIOD_MS (5ms) */
void App_Line_Task(void);

/* ============================ 参数读写 ============================ */

void App_Line_SetParams(int16_t base_pwm, int16_t Kp, int16_t Ki, int16_t Kd);
void App_Line_GetParams(LineParams_t *params);
LineTuneParam_t App_Line_GetTuneParam(void);
void App_Line_NextTuneParam(void);
void App_Line_AdjustTuneParam(int16_t delta);
int16_t App_Line_GetError(void);
LineStatus_t App_Line_GetStatus(void);

/* ============================ 调试输出 ============================ */

/* 打印当前灰度digital值、error、左右PWM */
void App_Line_PrintDebug(void);

#endif
