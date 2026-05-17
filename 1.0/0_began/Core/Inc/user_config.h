#ifndef __USER_CONFIG_H
#define __USER_CONFIG_H

#include "main.h"
#include "tim.h"
#include "usart.h"
#include "i2c.h"

/* PWM */
#define MOTOR_PWM_MAX               3599
#define MOTOR_PWM_MIN               0
#define MOTOR_TEST_PWM              300
#define MOTOR_TEST_DURATION_MS      1000

#define L_PWM_TIM                   htim2
#define L_PWM_CH                    TIM_CHANNEL_1
#define R_PWM_TIM                   htim2
#define R_PWM_CH                    TIM_CHANNEL_2

/* Motor direction */
#define L_IN1_PORT                  GPIOB
#define L_IN1_PIN                   GPIO_PIN_12
#define L_IN2_PORT                  GPIOB
#define L_IN2_PIN                   GPIO_PIN_13

#define R_IN1_PORT                  GPIOB
#define R_IN1_PIN                   GPIO_PIN_15
#define R_IN2_PORT                  GPIOB
#define R_IN2_PIN                   GPIO_PIN_14

/* Keys */
#define KEY1_PORT                   GPIOB
#define KEY1_PIN                    GPIO_PIN_1
#define KEY2_PORT                   GPIOB
#define KEY2_PIN                    GPIO_PIN_0
#define KEY3_PORT                   GPIOB
#define KEY3_PIN                    GPIO_PIN_10
#define KEY4_PORT                   GPIOB
#define KEY4_PIN                    GPIO_PIN_11

#define KEY_PRESSED_LEVEL           GPIO_PIN_RESET

/* Beep */
#define BEEP_PORT                   GPIOB
#define BEEP_PIN                    GPIO_PIN_3

/* LED (PC13) - 竞赛指示灯 */
#define LED_PORT                    GPIOC
#define LED_PIN                     GPIO_PIN_13

/* Encoders */
#define LEFT_ENCODER_TIM            htim3
#define RIGHT_ENCODER_TIM           htim4
#define LEFT_ENCODER_DIR            -1
#define RIGHT_ENCODER_DIR           1

/* I2C */
#define CAR_I2C                     hi2c1

/* Gray sensor (Ganwei 8ch I2C)
   I2C地址（AD1跳线帽安装=1, AD0跳线帽不装=0 => 7-bit=0x4E, 8-bit写=0x9C）
   传感器固件版本：0x36 */
#define GRAY_I2C_ADDR              0x4E

/* 巡线一圈目标编码器计数（左右轮各跑约1圈时累计）
   需要实测标定：让小车跑一圈，记录 OLED 显示的 enc_total 值
   初始设为 43000（实测一圈约需此值），实测后改为 enc_total * 1.0 即可 */
#define ONE_LAP_COUNT              43000

/* Gray sensor 软件模拟I2C引脚（PA5=SCL, PA4=SDA）
   经实测：PA5=SCL, PA4=SDA 时 gscan 找到 0x4E，gp ping OK
   传感器接线：PA5→SCL，PA4→SDA，SCL/SDA跳线帽装上
   独立于PB8/PB9的硬件I2C1（OLED总线） */
#define GRAY_SCL_PORT_DEF          GPIOA
#define GRAY_SCL_PIN_DEF           GPIO_PIN_5
#define GRAY_SDA_PORT_DEF          GPIOA
#define GRAY_SDA_PIN_DEF           GPIO_PIN_4

/* Debug UART */
#define DEBUG_UART                  huart1

/* Task periods, ms */
#define KEY_TASK_PERIOD_MS          10
#define SPEED_TASK_PERIOD_MS        10
#define LINE_TASK_PERIOD_MS         5
#define OLED_TASK_PERIOD_MS         100
#define BEEP_TASK_PERIOD_MS         10

#endif
