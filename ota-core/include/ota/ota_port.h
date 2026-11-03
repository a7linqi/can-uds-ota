#ifndef OTA_PORT_H
#define OTA_PORT_H
#include <stdint.h>
#include "ota/can_frame.h"

/* CAN: return 0 only when a frame is accepted by the driver/owned TX queue.
 * This does not promise physical transmission; reset must additionally wait
 * for both the software queue and hardware mailboxes to become idle. */
typedef struct {
    int (*can_send)(void *user, const can_frame_t *frame);
    uint32_t (*now_ms)(void *user);
    void *user;
} ota_port_t;

/* Storage calls are synchronous: return 0 only after durable completion.
 * Wait for FLASH/SPI WIP or EEPROM ACK polling and enforce deadlines.
 * Core calls are serialized in the bootloader/main task.
 * Offsets are relative to THIS device, never absolute MCU addresses.
 * A NOR/internal write only changes 1 -> 0. The SPI driver must split
 * writes at page boundaries; the internal driver programs 4-byte units. */
typedef struct {
    int (*erase)(void *user, uint32_t offset, uint32_t len);
    int (*write)(void *user, uint32_t offset, const uint8_t *data, uint32_t len);
    int (*read)(void *user, uint32_t offset, uint8_t *out, uint32_t len);
    void *user;
} flash_port_t;

/* EEPROM overwrites bytes without erase. Each write fits one 8-byte page;
 * metadata.c splits larger records. Return only after ACK polling. */
typedef struct {
    int (*write)(void *user, uint32_t offset, const uint8_t *data, uint32_t len);
    int (*read)(void *user, uint32_t offset, uint8_t *out, uint32_t len);
    void *user;
} eeprom_port_t;
#endif
