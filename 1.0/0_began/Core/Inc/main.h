/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32f1xx_hal.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define PWMA_Pin GPIO_PIN_0
#define PWMA_GPIO_Port GPIOA
#define PWMB_Pin GPIO_PIN_1
#define PWMB_GPIO_Port GPIOA
#define SDA1_Pin GPIO_PIN_4
#define SDA1_GPIO_Port GPIOA
#define SCL1_Pin GPIO_PIN_5
#define SCL1_GPIO_Port GPIOA
#define E1A_Pin GPIO_PIN_6
#define E1A_GPIO_Port GPIOA
#define E1B_Pin GPIO_PIN_7
#define E1B_GPIO_Port GPIOA
#define K2_Pin GPIO_PIN_0
#define K2_GPIO_Port GPIOB
#define K1_Pin GPIO_PIN_1
#define K1_GPIO_Port GPIOB
#define K3_Pin GPIO_PIN_10
#define K3_GPIO_Port GPIOB
#define K4_Pin GPIO_PIN_11
#define K4_GPIO_Port GPIOB
#define AIN1_Pin GPIO_PIN_12
#define AIN1_GPIO_Port GPIOB
#define AIN2_Pin GPIO_PIN_13
#define AIN2_GPIO_Port GPIOB
#define BIN1_Pin GPIO_PIN_14
#define BIN1_GPIO_Port GPIOB
#define BIN2_Pin GPIO_PIN_15
#define BIN2_GPIO_Port GPIOB
#define K230RX_Pin GPIO_PIN_9
#define K230RX_GPIO_Port GPIOA
#define K230TX_Pin GPIO_PIN_10
#define K230TX_GPIO_Port GPIOA
#define BEEP_Pin GPIO_PIN_3
#define BEEP_GPIO_Port GPIOB
#define SDA_Pin GPIO_PIN_4
#define SDA_GPIO_Port GPIOB
#define SCL_Pin GPIO_PIN_5
#define SCL_GPIO_Port GPIOB
#define E2A_Pin GPIO_PIN_6
#define E2A_GPIO_Port GPIOB
#define E2B_Pin GPIO_PIN_7
#define E2B_GPIO_Port GPIOB
#define SCL1B8_Pin GPIO_PIN_8
#define SCL1B8_GPIO_Port GPIOB
#define SDA1B9_Pin GPIO_PIN_9
#define SDA1B9_GPIO_Port GPIOB

/* USER CODE BEGIN Private defines */

/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
