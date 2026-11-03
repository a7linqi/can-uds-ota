#ifndef OTA_METADATA_H
#define OTA_METADATA_H
#include <stdint.h>
#include "ota/flash_layout.h"
#include "ota/ota_port.h"
#define META_MAGIC         0x3241544Du /* MAT2, format incompatible with old L1 */
#define META_COMMITTED     0xA55A5AA5u
#define MAX_BOOT_ATTEMPTS  3u

typedef enum {
    UPDATE_IDLE = 0,
    UPDATE_DOWNLOADING,
    UPDATE_READY,
    UPDATE_INSTALLING,
    UPDATE_TRIAL,
    UPDATE_RESTORING
} update_state_t;

typedef struct {
    uint32_t magic, seq, state;
    uint32_t active_version, active_size, active_crc;
    uint32_t pending_version, pending_size, pending_crc;
    uint32_t boot_attempts, min_version;
    uint32_t reserved[3];
    uint32_t crc, commit;
} metadata_t;

typedef struct { eeprom_port_t eeprom; } metadata_store_t;
void metadata_store_init(metadata_store_t *s, eeprom_port_t eeprom);
/* 1 = newest valid record; 0 = no valid record; -1 = I/O/ambiguity.
 * Any unreadable copy is an error: never overwrite an unknown newer copy. */
int metadata_load_ex(metadata_store_t *s, metadata_t *out, int *copy_index);
int metadata_load(metadata_store_t *s, metadata_t *out);
/* Invalidate OTHER copy, write body+CRC, commit last, read back.
 * Does not modify *m unless the complete record was verified. */
int metadata_save(metadata_store_t *s, metadata_t *m);
#endif
