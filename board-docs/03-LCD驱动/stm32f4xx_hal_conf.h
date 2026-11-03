/*
 * Minimal HAL configuration for the standalone LCD probe.
 *
 * Must be a SUPERSET of the frozen reference BSP configuration
 * (ota-core/platform/stm32f407/board/explorer_reference/stm32f4xx_hal_conf.h)
 * because the probe links explorer_reference.c as well; it only ADDS the
 * FSMC/SRAM module that the LCD parallel bus needs. The frozen file is left
 * untouched: this copy wins because build.py puts this directory first on the
 * include path.
 */
#ifndef OTA_LCD_TEST_STM32F4XX_HAL_CONF_H
#define OTA_LCD_TEST_STM32F4XX_HAL_CONF_H
#define HAL_MODULE_ENABLED
#define HAL_CAN_MODULE_ENABLED
#define HAL_CORTEX_MODULE_ENABLED
#define HAL_DMA_MODULE_ENABLED
#define HAL_FLASH_MODULE_ENABLED
#define HAL_GPIO_MODULE_ENABLED
#define HAL_I2C_MODULE_ENABLED
#define HAL_IWDG_MODULE_ENABLED
#define HAL_PWR_MODULE_ENABLED
#define HAL_RCC_MODULE_ENABLED
#define HAL_SRAM_MODULE_ENABLED          /* FSMC Bank1 -> 8080 LCD bus (probe only) */
#define HAL_SPI_MODULE_ENABLED
#define HAL_UART_MODULE_ENABLED
#ifndef HSE_VALUE
#define HSE_VALUE 8000000U
#endif
#define HSE_STARTUP_TIMEOUT 1000U
#define HSI_VALUE 16000000U
#define LSI_VALUE 32000U
#define LSE_VALUE 32768U
#define LSE_STARTUP_TIMEOUT 5000U
#define EXTERNAL_CLOCK_VALUE 12288000U
#define VDD_VALUE 3300U
#define TICK_INT_PRIORITY 15U
#define USE_RTOS 0U
#define PREFETCH_ENABLE 1U
#define INSTRUCTION_CACHE_ENABLE 1U
#define DATA_CACHE_ENABLE 1U
#include "stm32f4xx_hal_rcc.h"
#include "stm32f4xx_hal_gpio.h"
#include "stm32f4xx_hal_dma.h"
#include "stm32f4xx_hal_cortex.h"
#include "stm32f4xx_hal_can.h"
#include "stm32f4xx_hal_flash.h"
#include "stm32f4xx_hal_i2c.h"
#include "stm32f4xx_hal_iwdg.h"
#include "stm32f4xx_hal_pwr.h"
#include "stm32f4xx_hal_sram.h"
#include "stm32f4xx_hal_spi.h"
#include "stm32f4xx_hal_uart.h"
#define assert_param(expr) ((void)0U)
#endif
