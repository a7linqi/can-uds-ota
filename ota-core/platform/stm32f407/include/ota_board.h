#ifndef OTA_BOARD_H
#define OTA_BOARD_H
#include <stdint.h>
#include "ota/can_frame.h"

/* Implement with CubeMX/HAL AFTER checking the actual board schematic.
 * Default board_unconfigured.c fails closed; it is not a fake HAL driver.
 * Storage calls are synchronous with bounded timeouts and durable return. */
typedef struct {
    uint32_t detected_flash_bytes;
    uint32_t nor_bytes, nor_page_bytes, nor_erase_bytes;
    uint32_t eeprom_bytes, eeprom_page_bytes;
    uint8_t board_verified, storage_verified;
} ota_board_config_t;
int ota_bsp_init(ota_board_config_t *config);
uint32_t ota_bsp_now_ms(void);
void ota_bsp_idle(void);
void ota_bsp_log(const char *message);
void ota_bsp_heartbeat(void);
int ota_bsp_force_loader(void);
int ota_bsp_app_healthy(void);
int ota_bsp_quiesce(void);
void ota_bsp_reset(void);
/* Implemented by each firmware main. Long SPI WIP/ACK loops may call this
 * from the main context at least every 1000 ms. Never call from an ISR or
 * re-enter the diagnostic dispatcher. A negative result aborts the driver. */
int ota_firmware_service(void);
int ota_firmware_watchdog_service(void); /* role-specific: never blindly feeds App */
int ota_bsp_watchdog_start(void); /* explicit option: prescaler 256, reload 4095 */
int ota_bsp_watchdog_reload(void);

/* 0 accepted, 1 temporarily busy, negative permanent error.
 * idle must query hardware mailboxes, not just the software queue. */
int ota_bsp_can_try_tx(const can_frame_t *frame);
int ota_bsp_can_start(void); /* enable RX notification only AFTER diag init */
int ota_bsp_can_tx_idle(void);
int ota_bsp_iflash_read(uint32_t absolute,uint8_t *out,uint32_t len);
int ota_bsp_iflash_unlock(void);
int ota_bsp_iflash_lock(void);
int ota_bsp_iflash_erase_sector(uint32_t sector);
int ota_bsp_iflash_program_word(uint32_t absolute,uint32_t word);

/* NOR functions handle WREN/WIP, chip-select and bounded timeouts.
 * During a diagnostic operation, return or call ota_firmware_service()
 * within 1000 ms. This bound must be measured on the actual board.
 * The port splits pages/sectors; these hooks implement the chip protocol. */
int ota_bsp_nor_read(uint32_t offset,uint8_t *out,uint32_t len);
int ota_bsp_nor_erase(uint32_t offset,uint32_t len);
int ota_bsp_nor_page_program(uint32_t offset,const uint8_t *data,uint32_t len);
int ota_bsp_eeprom_read(uint32_t offset,uint8_t *out,uint32_t len);
int ota_bsp_eeprom_page_write(uint32_t offset,const uint8_t *data,uint32_t len);
#endif
