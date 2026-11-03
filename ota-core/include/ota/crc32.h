#ifndef OTA_CRC32_H
#define OTA_CRC32_H

#include <stdint.h>

/* Reflected CRC-32, polynomial 0xEDB88320 (zlib compatible).
   Incremental: pass the previous return value, start with 0. */
uint32_t crc32_update(uint32_t crc, const uint8_t *data, uint32_t len);
uint32_t crc32_calc(const uint8_t *data, uint32_t len);

#endif /* OTA_CRC32_H */
