#ifndef OTA_SIGNATURE_H
#define OTA_SIGNATURE_H

#include <stdint.h>
#include "ota/flash_layout.h"
#include "ota/ota_port.h"

#define OTA_PUBLIC_KEY_SIZE  32u

/* Verify an Ed25519 signature over a signed image in external flash.
 * The signed message is: 32-byte header + image_size payload bytes.
 * Message bytes are streamed through the flash port in chunks, so a
 * full image never has to fit in RAM. Only the defined external container
 * offsets and bounded App payload sizes are accepted; S must be canonical.
 * Returns 0 on success, -1 otherwise.
 * The public key is hardcoded (see ota/pubkey.h). */
int image_signature_verify(flash_port_t external, uint32_t offset,
                           uint32_t image_size,
                           const uint8_t signature[OTA_SIGNATURE_SIZE]);

#endif
