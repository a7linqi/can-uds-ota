#include "ota/crc32.h"

uint32_t crc32_update(uint32_t crc, const uint8_t *data, uint32_t len)
{
    uint32_t i;
    uint8_t bit;

    crc ^= 0xFFFFFFFFu;
    for (i = 0u; i < len; i++) {
        crc ^= (uint32_t)data[i];
        for (bit = 0u; bit < 8u; bit++) {
            if (crc & 1u) {
                crc = (crc >> 1) ^ 0xEDB88320u;
            } else {
                crc = crc >> 1;
            }
        }
    }
    return crc ^ 0xFFFFFFFFu;
}

uint32_t crc32_calc(const uint8_t *data, uint32_t len)
{
    return crc32_update(0u, data, len);
}
