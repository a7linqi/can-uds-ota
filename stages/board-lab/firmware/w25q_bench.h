#ifndef W25Q_BENCH_H
#define W25Q_BENCH_H
#include <stdint.h>

/* Minimal W25Q128 write-capable driver for the bench restore image.
 *
 * This is NOT the product flash path: it is a bring-up tool that lives in the
 * stage tree, sends its own opcodes, and has no OTA semantics. Every SPI/CS/
 * delay primitive is injected, so the whole driver runs on a host test with a
 * behavioural chip model and on the board with the HAL glue.
 *
 * Safety rules enforced here, not by the caller:
 *   - erase requires 4 KiB alignment, program requires 256 B page alignment and
 *     must not cross a page boundary;
 *   - every write is preceded by WREN and the WEL bit is read back, so a bad
 *     CS line fails closed instead of silently dropping the write;
 *   - every operation waits for WIP to clear with a bounded timeout.
 */

#define W25Q_SECTOR_SIZE 4096u
#define W25Q_PAGE_SIZE   256u

typedef struct {
    /* Full-duplex transfer of n bytes: tx may be NULL (send 0xFF), rx may be
     * NULL (discard). Returns 0 on success. */
    int (*xfer)(void *ctx, const uint8_t *tx, uint8_t *rx, uint32_t n);
    int (*cs)(void *ctx, int active);
    void (*delay_ms)(void *ctx, uint32_t ms);
    void *ctx;
} w25q_io_t;

typedef struct {
    const w25q_io_t *io;
    uint32_t capacity;
    uint32_t timeout_ms;
    uint32_t jedec_id;
} w25q_t;

int w25q_open(w25q_t *d, const w25q_io_t *io, uint32_t capacity, uint32_t timeout_ms);
int w25q_read_jedec(w25q_t *d, uint32_t *id);
int w25q_read_status(w25q_t *d, uint8_t *sr1);
int w25q_wait_ready(w25q_t *d);
int w25q_read(w25q_t *d, uint32_t addr, uint8_t *out, uint32_t n);
int w25q_erase_4k(w25q_t *d, uint32_t addr);
int w25q_page_program(w25q_t *d, uint32_t addr, const uint8_t *src, uint32_t n);
/* Streaming CRC32 over [addr, addr+n). Returns the CRC and sets *err on
 * failure; a partial sweep must never be reported as a match. */
uint32_t w25q_crc32_range(w25q_t *d, uint32_t addr, uint32_t n,
                          uint8_t *scratch, uint32_t scratch_len, int *err);

#endif
