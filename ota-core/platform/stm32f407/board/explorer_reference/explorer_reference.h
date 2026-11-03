#ifndef OTA_EXPLORER_REFERENCE_H
#define OTA_EXPLORER_REFERENCE_H
#include "board_hal_user.h"
#include "ota_stm32.h"

/* Common reference wiring verified in ATK V2 schematic and V3 manual.
 * This does not identify the user's physical PCB or fitted NOR part. */
#ifndef OTA_EXPLORER_REFERENCE_PINS
#define OTA_EXPLORER_REFERENCE_PINS 0
#endif
#ifndef OTA_EXPLORER_CAN_LOOPBACK
#define OTA_EXPLORER_CAN_LOOPBACK 1
#endif
#if OTA_ENABLE_FLASHING || OTA_ENABLE_BOOT_HANDOFF || OTA_ENABLE_WATCHDOG
#error "Explorer reference bring-up targets are read-only; all update gates must stay off"
#endif
#if OTA_EXPLORER_CAN_LOOPBACK != 0 && OTA_EXPLORER_CAN_LOOPBACK != 1
#error "Select silent internal loopback (1) or normal physical CAN (0)"
#endif

#define OTA_EXPLORER_HSE_HZ 8000000u
#define OTA_EXPLORER_HCLK_HZ 168000000u
#define OTA_EXPLORER_PCLK1_HZ 42000000u
#define OTA_EXPLORER_PCLK2_HZ 84000000u
#define OTA_EXPLORER_CAN_PRESCALER 6u
#define OTA_EXPLORER_CAN_BS1_TQ 11u
#define OTA_EXPLORER_CAN_BS2_TQ 2u
#define OTA_EXPLORER_CAN_BITRATE 500000u

typedef struct {
    uint32_t device_id,revision_id,flash_bytes;
    uint32_t hclk_hz,pclk1_hz,pclk2_hz;
    uint32_t jedec_id,eeprom_crc32;
    int jedec_status,eeprom_status;
    uint8_t eeprom[256]; /* Only a readback; no automatic metadata initialization. */
} ota_explorer_probe_t;
extern ota_explorer_probe_t ota_explorer_probe;
/* Implemented by the selected firmware main; called from FIFO0 ISR. */
void ota_bootloader_can_rx(const can_frame_t *frame);
#endif
