#ifndef OTA_BOARD_HAL_USER_H
#define OTA_BOARD_HAL_USER_H
#include "stm32f4xx_hal.h"
#include "ota_board.h"
/* User glue generated/filled for the actual board. Not implemented by the
 * skeleton. Keep exactly one main, startup, SystemInit and HAL callback. */
int ota_user_generated_init(void);
void ota_user_config(ota_board_config_t *config);
CAN_HandleTypeDef *ota_user_can(void);
I2C_HandleTypeDef *ota_user_i2c(void);
/* Only needed when opting into w25q128_hal_user.c. The board owns these
 * CubeMX settings and /CS GPIO; the example never guesses pins or clocks. */
SPI_HandleTypeDef *ota_user_spi(void);
int ota_user_nor_select(int active);
int ota_hal_nor_probe(void);
uint32_t ota_hal_nor_capacity(void);
uint8_t ota_user_eeprom_address7(void);
uint32_t ota_user_flash_voltage_range(void);
void ota_user_log(const char *s);
void ota_user_heartbeat(void);
int ota_user_force_loader(void);
int ota_user_app_healthy(void);
int ota_user_quiesce_peripherals(void);
void ota_user_can_rx(const can_frame_t *frame);
int ota_user_nor_read(uint32_t off,uint8_t *out,uint32_t len);
int ota_user_nor_erase_4k(uint32_t off);
int ota_user_nor_page_program(uint32_t off,const uint8_t *data,uint32_t len);
/* Call from the existing HAL_CAN_RxFifo0MsgPendingCallback. */
void ota_hal_can_fifo0_callback(CAN_HandleTypeDef *hcan);
#endif
