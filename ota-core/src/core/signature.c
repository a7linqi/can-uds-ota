#include "ota/signature.h"
#include "ota/pubkey.h"
#include "sha512.h"
#include "ge.h"
#include "sc.h"

static int consttime_equal32(const uint8_t *x, const uint8_t *y)
{
    uint8_t r = 0u;
    unsigned i;
    for (i = 0u; i < 32u; i++) r |= x[i] ^ y[i];
    return !r;
}

/* RFC 8032 sections 5.1.7 and 8.4 require 0 <= S < L. Checking only the
 * high three bits permits S+L and makes signature encodings malleable.
 * S is public input; an early-exit comparison does not expose a secret. */
static int scalar_is_canonical(const uint8_t s[32])
{
    static const uint8_t order[32]={
        0xED,0xD3,0xF5,0x5C,0x1A,0x63,0x12,0x58,
        0xD6,0x9C,0xF7,0xA2,0xDE,0xF9,0xDE,0x14,
        0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0x10
    };
    int i;
    for(i=31;i>=0;i--) {
        if(s[i]<order[i]) return 1;
        if(s[i]>order[i]) return 0;
    }
    return 0;
}

int image_signature_verify(flash_port_t external, uint32_t offset,
                           uint32_t image_size,
                           const uint8_t signature[OTA_SIGNATURE_SIZE])
{
    sha512_context ctx;
    uint8_t header[IMAGE_HEADER_SIZE];
    uint8_t chunk[256];
    uint8_t h[64];
    uint8_t checker[32];
    ge_p3 A;
    ge_p2 R;
    uint32_t off = 0u;

    if (!external.read || !signature ||
        (offset!=DOWNLOAD_OFFSET && offset!=BACKUP_OFFSET) ||
        image_size<8u || image_size>IMAGE_MAX_PAYLOAD ||
        !scalar_is_canonical(signature+32u)) return -1;
    if (external.read(external.user, offset, header, sizeof header)) return -1;

    /* h = SHA-512(R || A || header || payload), streamed in chunks. */
    sha512_init(&ctx);
    sha512_update(&ctx, signature, 32u);            /* R */
    sha512_update(&ctx, OTA_PUBLIC_KEY, 32u);       /* A */
    sha512_update(&ctx, header, sizeof header);     /* header */
    while (off < image_size) {
        uint32_t n = image_size - off;
        if (n > sizeof chunk) n = sizeof chunk;
        if (external.read(external.user, offset + IMAGE_HEADER_SIZE + off,
                          chunk, n))
            return -1;
        sha512_update(&ctx, chunk, n);
        off += n;
    }
    sha512_final(&ctx, h);

    /* Check R + h*A == S*B (equivalently h*(-A) + S*B == R). */
    sc_reduce(h);
    if (ge_frombytes_negate_vartime(&A, OTA_PUBLIC_KEY) != 0) return -1;
    ge_double_scalarmult_vartime(&R, h, &A, signature + 32u);
    ge_tobytes(checker, &R);
    return consttime_equal32(checker, signature) ? 0 : -1;
}
