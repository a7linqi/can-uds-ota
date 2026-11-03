#ifndef OTA_CAN_FRAME_H
#define OTA_CAN_FRAME_H

#include <stdint.h>
#include <stdbool.h>

/* One classic CAN frame. Extended ids are used by 29-bit identifiers;
   the OTA stack itself only cares that the id round-trips unchanged. */
typedef struct {
    uint32_t id;
    uint8_t  dlc;
    uint8_t  data[8];
    bool     extended;
} can_frame_t;

#endif /* OTA_CAN_FRAME_H */
