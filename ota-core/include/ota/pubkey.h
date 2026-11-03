#ifndef OTA_PUBKEY_H
#define OTA_PUBKEY_H

#include <stdint.h>
#include "ota/signature.h"

/* Ed25519 public key hardcoded into the bootloader for image verification.
 *
 * DEMONSTRATION KEY — NOT a production secret.
 * Derived from the deterministic demo seed 00 01 02 ... 1f. The matching
 * private seed is used by the PC signing tool (tools/ed25519.py +
 * tools/make_image.py). Anyone can reproduce this key, so it provides
 * tamper *detection* for the demo but no real authority.
 *
 * To rotate the key for real use: generate a fresh seed, sign images with
 * it, and replace the 32 bytes below (then rebuild the firmware). Keep the
 * seed/private key off the device. */
static const uint8_t OTA_PUBLIC_KEY[OTA_PUBLIC_KEY_SIZE] = {
    0x03, 0xa1, 0x07, 0xbf, 0xf3, 0xce, 0x10, 0xbe,
    0x1d, 0x70, 0xdd, 0x18, 0xe7, 0x4b, 0xc0, 0x99,
    0x67, 0xe4, 0xd6, 0x30, 0x9b, 0xa5, 0x0d, 0x5f,
    0x1d, 0xdc, 0x86, 0x64, 0x12, 0x55, 0x31, 0xb8
};

#endif
