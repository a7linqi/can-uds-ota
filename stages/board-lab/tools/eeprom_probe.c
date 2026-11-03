/* eeprom_probe.c - read-only acceptance of a real 256-byte EEPROM snapshot
 * against the FROZEN core metadata loader.
 *
 * Links the real ota-core src/core/metadata.c + crc32.c and calls
 * metadata_load_ex(). The read callback maps the snapshot; the write callback
 * counts calls and always fails, so nothing is ever written and the input file
 * is never modified. No metadata_save() is ever invoked.
 */
#include "ota/metadata.h"
#include "ota/crc32.h"
#include <stdio.h>
#include <string.h>
#include <stdint.h>

static uint8_t snap[256];
static uint32_t snap_len = 0u;
static uint32_t write_calls = 0u;
static uint32_t read_calls = 0u;

static int read_cb(void *user, uint32_t off, uint8_t *out, uint32_t n)
{
    (void)user;
    read_calls++;
    /* 64-bit arithmetic so off+n cannot wrap past the buffer check. */
    if ((uint64_t)off + (uint64_t)n > (uint64_t)snap_len) return -1;
    memcpy(out, snap + off, n);
    return 0;
}
static int write_cb(void *user, uint32_t off, const uint8_t *data, uint32_t n)
{
    (void)user; (void)off; (void)data; (void)n;
    write_calls++;
    return -1; /* read-only: refuse and count */
}

static uint32_t get_le(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8)
         | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void dump_record(const char *label, uint32_t off)
{
    uint8_t raw[META_RECORD_SIZE];
    uint32_t w[16], crc_stored, crc_calc;
    int i;
    if ((uint64_t)off + META_RECORD_SIZE > (uint64_t)snap_len) {
        printf("%s: READ_OUT_OF_RANGE (off=%u + %u > snap_len=%u)\n",
               label, off, (uint32_t)META_RECORD_SIZE, snap_len);
        return;
    }
    memcpy(raw, snap + off, META_RECORD_SIZE);
    for (i = 0; i < 16; i++) w[i] = get_le(raw + 4u * i);
    crc_stored = w[14];
    crc_calc = crc32_calc(raw, 56u);
    printf("%s: magic=%08X%s commit=%08X%s seq=%u state=%u\n",
           label, w[0], w[0] == META_MAGIC ? "(MAT2)" : "(BAD)",
           w[15], w[15] == META_COMMITTED ? "(COMMITTED)" : "(BAD)", w[1], w[2]);
    printf("  active=%u/%u/%08X pending=%u/%u/%08X attempts=%u min=%u\n",
           w[3], w[4], w[5], w[6], w[7], w[8], w[9], w[10]);
    printf("  crc_stored=%08X crc_calc=%08X %s\n",
           crc_stored, crc_calc, crc_stored == crc_calc ? "(MATCH)" : "(MISMATCH)");
    printf("  reserved=%08X %08X %08X\n", w[11], w[12], w[13]);
}

int main(int argc, char **argv)
{
    FILE *f;
    long len;
    size_t got;
    eeprom_port_t port;
    metadata_store_t store;
    metadata_t out;
    int copy_index = -1;
    int r;

    if (argc != 2) { fprintf(stderr, "usage: eeprom_probe <snapshot.bin>\n"); return 2; }
    f = fopen(argv[1], "rb");
    if (!f) { printf("OPEN_FAILED path=%s\n", argv[1]); return 1; }
    if (fseek(f, 0, SEEK_END) != 0) { printf("SEEK_FAILED\n"); fclose(f); return 1; }
    len = ftell(f);
    if (fseek(f, 0, SEEK_SET) != 0) { printf("SEEK_FAILED\n"); fclose(f); return 1; }
    if (len < 0) { printf("LEN_FAILED\n"); fclose(f); return 1; }
    /* Strict length: exactly 256 bytes. Oversized input must be rejected, never
     * silently truncated (R4). */
    if (len != 256) {
        printf("LEN_ERROR actual=%ld expected=256\n", len);
        fclose(f);
        return 1;
    }
    got = fread(snap, 1, 256u, f);
    fclose(f);
    if (got != 256u) {
        printf("READ_ERROR got=%zu expected=256\n", got);
        return 1;
    }
    snap_len = 256u;

    printf("snapshot_len=%u\n", snap_len);

    port.read = read_cb;
    port.write = write_cb;
    port.user = 0;
    metadata_store_init(&store, port);

    dump_record("copy@0", 0u);
    dump_record("copy@128", 128u);

    write_calls = 0u; read_calls = 0u;
    r = metadata_load_ex(&store, &out, &copy_index);
    printf("metadata_load_ex return=%d copy_index=%d write_calls=%u read_calls=%u\n",
           r, copy_index, write_calls, read_calls);
    if (r == 1) {
        printf("loaded: seq=%u state=%u active=%u/%u/%08X pending=%u/%u/%08X attempts=%u min=%u commit=%08X\n",
               out.seq, out.state, out.active_version, out.active_size, out.active_crc,
               out.pending_version, out.pending_size, out.pending_crc,
               out.boot_attempts, out.min_version, out.commit);
    } else if (r == 0) {
        printf("verdict=NO_VALID_RECORD\n");
    } else {
        printf("verdict=ERROR_OR_AMBIGUOUS\n");
    }

    if (snap_len == 256u && r == 0 && write_calls == 0u) {
        printf("RESULT=EXPECTED_NO_RECORD_NO_WRITES\n");
        return 0;
    }
    printf("RESULT=UNEXPECTED\n");
    return 1;
}
