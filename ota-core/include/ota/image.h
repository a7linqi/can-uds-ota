#ifndef OTA_IMAGE_H
#define OTA_IMAGE_H
#include <stdint.h>
#include "ota/flash_layout.h"
#include "ota/ota_port.h"
#define IMAGE_MAGIC 0x32474D49u /* IMG2, explicitly little-endian on wire */
/* flags bit 0: an Ed25519 signature (OTA_SIGNATURE_SIZE bytes) follows the
 * payload. Signed message = 32-byte header + payload. See ota/signature.h. */
#define IMAGE_FLAG_SIGNED 0x00000001u
/* When 1, every image MUST carry a valid Ed25519 signature; unsigned images
 * are rejected. This is the default and closes the "CRC only detects
 * corruption, not forgery" gap. */
#ifndef OTA_REQUIRE_SIGNATURE
#define OTA_REQUIRE_SIGNATURE 1
#endif
typedef struct {
    uint32_t magic, version, image_size, payload_crc;
    uint32_t load_address, flags, reserved, header_crc;
} image_header_t;
void image_header_finalize(image_header_t *h);
int image_header_ok(const image_header_t *h);
void image_header_encode(const image_header_t *h, uint8_t out[IMAGE_HEADER_SIZE]);
void image_header_decode(image_header_t *h, const uint8_t in[IMAGE_HEADER_SIZE]);
/* Only DOWNLOAD_OFFSET/BACKUP_OFFSET in external flash are accepted. */
int image_read_header(flash_port_t external, uint32_t offset, image_header_t *h);
int image_validate(flash_port_t external, uint32_t offset,
                   uint32_t *version, uint32_t *payload_size);
/* Internal APP_OFFSET contains payload/vectors, NOT a container header. */
int image_validate_app(flash_port_t internal, uint32_t size, uint32_t crc);
/* Only BACKUP_OFFSET with flags == 0 is accepted by this CRC-only path.
 * The backup holds a
 * copy of the already-verified internal App, and the device has no private
 * key to re-sign at restore time. */
int image_validate_backup(flash_port_t external, uint32_t offset,
                          uint32_t *version, uint32_t *size);
#endif
