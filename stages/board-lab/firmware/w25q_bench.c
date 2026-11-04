#include "w25q_bench.h"
#include "ota/crc32.h"
#include <string.h>

#define OP_WRITE_ENABLE  0x06u
#define OP_READ_STATUS1  0x05u
#define OP_READ_DATA     0x03u
#define OP_PAGE_PROGRAM  0x02u
#define OP_SECTOR_ERASE  0x20u
#define OP_READ_ID       0x9Fu

#define SR1_WIP 0x01u
#define SR1_WEL 0x02u

static int in_range(const w25q_t *d, uint32_t addr, uint32_t n)
{
    return addr <= d->capacity && n <= d->capacity - addr;
}

/* Assert CS, send opcode + 24-bit address, then optionally tx/rx n bytes, and
 * always release CS: every error path must leave the bus idle. */
static int cmd_addr(w25q_t *d, uint8_t op, uint32_t addr,
                    const uint8_t *tx, uint8_t *rx, uint32_t n)
{
    uint8_t head[4];
    int rc;

    head[0] = op;
    head[1] = (uint8_t)(addr >> 16);
    head[2] = (uint8_t)(addr >> 8);
    head[3] = (uint8_t)addr;
    if (d->io->cs(d->io->ctx, 1)) return -1;
    rc = d->io->xfer(d->io->ctx, head, 0, sizeof head);
    if (!rc && n) rc = d->io->xfer(d->io->ctx, tx, rx, n);
    if (d->io->cs(d->io->ctx, 0)) rc = -1;
    return rc;
}

static int write_enable(w25q_t *d)
{
    uint8_t op = OP_WRITE_ENABLE, sr1 = 0u;

    if (d->io->cs(d->io->ctx, 1)) return -1;
    if (d->io->xfer(d->io->ctx, &op, 0, 1u)) { d->io->cs(d->io->ctx, 0); return -1; }
    if (d->io->cs(d->io->ctx, 0)) return -1;
    /* Fail closed: without WEL the following erase/program is a no-op on the
     * chip and would be read back as "unchanged" much later. */
    if (w25q_read_status(d, &sr1)) return -1;
    return (sr1 & SR1_WEL) ? 0 : -1;
}

int w25q_open(w25q_t *d, const w25q_io_t *io, uint32_t capacity, uint32_t timeout_ms)
{
    if (!d || !io || !io->xfer || !io->cs || !io->delay_ms) return -1;
    if (capacity < W25Q_SECTOR_SIZE || capacity > 0x01000000u) return -1;
    if ((capacity % W25Q_SECTOR_SIZE) != 0u) return -1;
    d->io = io;
    d->capacity = capacity;
    d->timeout_ms = timeout_ms ? timeout_ms : 2000u;
    d->jedec_id = 0u;
    return 0;
}

int w25q_read_jedec(w25q_t *d, uint32_t *id)
{
    uint8_t op = OP_READ_ID, buf[3] = {0u, 0u, 0u};
    int rc;

    if (d->io->cs(d->io->ctx, 1)) return -1;
    rc = d->io->xfer(d->io->ctx, &op, 0, 1u);
    if (!rc) rc = d->io->xfer(d->io->ctx, 0, buf, sizeof buf);
    if (d->io->cs(d->io->ctx, 0)) rc = -1;
    if (rc) return -1;
    d->jedec_id = ((uint32_t)buf[0] << 16) | ((uint32_t)buf[1] << 8) | buf[2];
    if (id) *id = d->jedec_id;
    return 0;
}

int w25q_read_status(w25q_t *d, uint8_t *sr1)
{
    uint8_t op = OP_READ_STATUS1, value = 0xFFu;
    int rc;

    if (d->io->cs(d->io->ctx, 1)) return -1;
    rc = d->io->xfer(d->io->ctx, &op, 0, 1u);
    if (!rc) rc = d->io->xfer(d->io->ctx, 0, &value, 1u);
    if (d->io->cs(d->io->ctx, 0)) rc = -1;
    if (rc) return -1;
    *sr1 = value;
    return 0;
}

int w25q_wait_ready(w25q_t *d)
{
    uint32_t elapsed = 0u;
    uint8_t sr1;

    for (;;) {
        if (w25q_read_status(d, &sr1)) return -1;
        if (!(sr1 & SR1_WIP)) return 0;
        if (elapsed >= d->timeout_ms) return -1;
        d->io->delay_ms(d->io->ctx, 1u);
        elapsed++;
    }
}

int w25q_read(w25q_t *d, uint32_t addr, uint8_t *out, uint32_t n)
{
    if (!out || !n || !in_range(d, addr, n)) return -1;
    return cmd_addr(d, OP_READ_DATA, addr, 0, out, n);
}

int w25q_erase_4k(w25q_t *d, uint32_t addr)
{
    if ((addr % W25Q_SECTOR_SIZE) != 0u) return -1;
    if (!in_range(d, addr, W25Q_SECTOR_SIZE)) return -1;
    if (write_enable(d)) return -1;
    if (cmd_addr(d, OP_SECTOR_ERASE, addr, 0, 0, 0u)) return -1;
    return w25q_wait_ready(d);
}

int w25q_page_program(w25q_t *d, uint32_t addr, const uint8_t *src, uint32_t n)
{
    if (!src || n == 0u || n > W25Q_PAGE_SIZE) return -1;
    if ((addr % W25Q_PAGE_SIZE) != 0u) return -1;
    if ((addr % W25Q_PAGE_SIZE) + n > W25Q_PAGE_SIZE) return -1;
    if (!in_range(d, addr, n)) return -1;
    if (write_enable(d)) return -1;
    if (cmd_addr(d, OP_PAGE_PROGRAM, addr, src, 0, n)) return -1;
    return w25q_wait_ready(d);
}

uint32_t w25q_crc32_range(w25q_t *d, uint32_t addr, uint32_t n,
                          uint8_t *scratch, uint32_t scratch_len, int *err)
{
    uint32_t crc = 0u, done = 0u, chunk;

    if (err) *err = 0;
    if (!scratch || scratch_len == 0u || !in_range(d, addr, n)) {
        if (err) *err = -1;
        return 0u;
    }
    while (done < n) {
        chunk = n - done;
        if (chunk > scratch_len) chunk = scratch_len;
        if (w25q_read(d, addr + done, scratch, chunk)) {
            if (err) *err = -1;
            return 0u;
        }
        crc = crc32_update(crc, scratch, chunk);
        done += chunk;
    }
    return crc;
}
