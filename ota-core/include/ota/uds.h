#ifndef OTA_UDS_H
#define OTA_UDS_H

#include <stdint.h>
#include "ota/ota_port.h"
#include "ota/boot.h"

#define UDS_SESSION_DEFAULT       0x01u
#define UDS_SESSION_PROGRAMMING   0x02u
#define UDS_SESSION_EXTENDED      0x03u

/* maxNumberOfBlockLength advertised in the 0x34 response */
#define UDS_MAX_DOWNLOAD_BLOCK    512u
#define UDS_MAX_DOWNLOAD_DATA     (UDS_MAX_DOWNLOAD_BLOCK - 2u)
#define UDS_S3_TIMEOUT_MS         5000u
#define UDS_DID_UPDATE_STATUS     0x0200u /* project-specific, read-only */
#define UDS_UPDATE_STATUS_SIZE    40u

typedef struct {
    boot_context_t   *boot;
    const ota_port_t *port;

    /* session / security */
    uint8_t  session;
    uint8_t  unlocked;
    uint32_t seed;
    uint8_t  seed_valid;
    uint32_t key_failures;
    uint32_t lockout_started_ms;
    uint8_t  lockout_active;

    /* 0x34 / 0x36 / 0x37 download state */
    uint8_t  dl_active;
    uint8_t  dl_has_previous;
    uint8_t  dl_previous_counter;
    uint32_t dl_last_offset;
    uint16_t dl_last_size;
    uint32_t dl_offset;
    uint32_t dl_size;
    uint32_t dl_written;
    uint8_t  dl_next_counter;

    uint8_t  reset_requested;
    uint32_t request_count;
    uint32_t last_request_ms;
} uds_server_t;

void uds_init(uds_server_t *s, boot_context_t *boot, const ota_port_t *port);
void uds_poll(uds_server_t *s);

/* Handles one complete request. Returns the response length, 0 means
   "no response" (suppressed positive response). */
uint16_t uds_handle(uds_server_t *s, const uint8_t *req, uint16_t req_len,
                    uint8_t *resp, uint16_t resp_max);

/* Demo security algorithm, exposed so the PC tester tool can reuse it. */
uint32_t uds_calc_key(uint32_t seed);

#endif /* OTA_UDS_H */
