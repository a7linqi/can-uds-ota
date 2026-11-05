#include "nor_restore.h"
#include "ota/crc32.h"
#include <string.h>

#if defined(STM32F407xx) && defined(__GNUC__)
/* The SWD/AHB observer must see every header store before the status store
 * that publishes it. */
#define NR_BARRIER() __asm__ volatile("dmb" ::: "memory")
#else
#define NR_BARRIER() ((void)0)
#endif

enum {
    NR_S_WAIT = 0,   /* waiting for a session + command */
    NR_S_READY,      /* waiting for the next block */
    NR_S_ERASE,
    NR_S_PROGRAM,
    NR_S_VERIFY_PAGE,
    NR_S_COMMIT,
    NR_S_FINAL_CRC,
    NR_S_DONE_ST,
    NR_S_ERROR_ST
};

static void publish_status(nr_ctx_t *c, uint32_t status)
{
    NR_BARRIER();
    c->mb->h.status = status;
}

static void publish_error(nr_ctx_t *c, uint32_t err)
{
    c->mb->h.error = err;
    NR_BARRIER();
    c->mb->h.status = NR_ST_ERROR;
    c->state = NR_S_ERROR_ST;
}

void nr_init(nr_ctx_t *c, volatile nr_mailbox_t *mb, const nr_io_t *io, uint32_t total)
{
    memset(c, 0, sizeof *c);
    c->mb = mb;
    c->io = *io;
    c->total = total;
    c->state = NR_S_WAIT;
    memset((void *)mb, 0, sizeof *mb);
    mb->h.magic = NR_MAGIC;
    mb->h.version = NR_VERSION;
    mb->h.status = NR_ST_IDLE;
    mb->h.error = NR_ERR_NONE;
}

/* Latch session/command and reset every counter. Returns 0 on success. */
static int begin(nr_ctx_t *c)
{
    volatile nr_mailbox_t *mb = c->mb;
    uint32_t start = 0u;
    uint32_t end = c->total;

    if (c->total == 0u || c->total > NR_TOTAL_SIZE ||
        (c->total % NR_SECTOR_SIZE) != 0u) {
        publish_error(c, NR_ERR_INIT);
        return -1;
    }
    if (mb->h.ack == NR_CMD_RESTORE) c->mode = 1u;
    else if (mb->h.ack == NR_CMD_VERIFY) c->mode = 2u;
    else c->mode = 3u;
    if (c->mode == 3u) {
        /* Range restore reads its bounds from the same header the block
         * submits use. Validate them BEFORE the chip is touched, and before
         * the request fields are cleared below. */
        start = mb->h.offset;
        if (start >= c->total || (start % NR_SECTOR_SIZE) != 0u ||
            mb->h.length == 0u || (mb->h.length % NR_SECTOR_SIZE) != 0u ||
            mb->h.length > c->total - start) {
            publish_error(c, NR_ERR_BAD_REQUEST);
            return -1;
        }
        end = start + mb->h.length;
    }
    c->session = mb->h.session;
    mb->h.session = c->session;
    mb->h.seq = 0u;
    mb->h.offset = 0u;
    mb->h.length = 0u;
    mb->h.crc32 = 0u;
    mb->h.error = NR_ERR_NONE;
    c->seq = 0u;
    c->start = start;
    c->end = end;
    c->offset = start;
    c->blk_len = 0u;
    c->cursor = start;
    c->crc = 0u;
    c->blocks_done = 0u;
    c->bytes_done = 0u;
    mb->h.ack = 0u;   /* consume the command so READY never looks like a restart */
    publish_status(c, NR_ST_BUSY);
    if (c->mode == 2u) {
        c->state = NR_S_FINAL_CRC;
    } else {
        c->state = NR_S_READY;
        publish_status(c, NR_ST_READY);
    }
    return 0;
}

int nr_service(nr_ctx_t *c)
{
    volatile nr_mailbox_t *mb = c->mb;
    uint32_t n;

    switch (c->state) {
    case NR_S_WAIT:
        if (mb->h.session == 0u) return 0;
        if (mb->h.ack != NR_CMD_RESTORE && mb->h.ack != NR_CMD_VERIFY &&
            mb->h.ack != NR_CMD_RESTORE_RANGE) return 0;
        if (begin(c) < 0) return -1;
        return 0;

    case NR_S_READY:
        /* A new command word means "start over" (host restart without reset). */
        if (mb->h.ack >= NR_CMD_BASE) { c->state = NR_S_WAIT; return 0; }
        if (mb->h.ack == 0u) return 0;                /* command already consumed */
        if (mb->h.ack != c->seq + 1u) return 0;        /* stale or duplicate */
        if (mb->h.seq != mb->h.ack) return 0;        /* header still in flight */
        /* Validate everything BEFORE touching the chip. */
        if (mb->h.offset != c->offset) { publish_error(c, NR_ERR_BAD_REQUEST); return -1; }
        if (mb->h.length == 0u || mb->h.length > NR_BLOCK_SIZE) {
            publish_error(c, NR_ERR_BAD_REQUEST); return -1;
        }
        if (mb->h.offset + mb->h.length > c->end) { publish_error(c, NR_ERR_BAD_REQUEST); return -1; }
        if (crc32_calc((const uint8_t *)mb->data, mb->h.length) != mb->h.crc32) {
            publish_error(c, NR_ERR_BLOCK_CRC); return -1;
        }
        c->blk_len = mb->h.length;
        c->blk_crc = mb->h.crc32;
        /* 1. leave READY before anything can change under the host's feet */
        publish_status(c, NR_ST_BUSY);
        c->cursor = c->offset & ~(NR_SECTOR_SIZE - 1u);
        c->state = NR_S_ERASE;
        return 0;

    case NR_S_ERASE:
        if (c->cursor >= c->offset + c->blk_len) {
            c->cursor = c->offset;
            c->state = NR_S_PROGRAM;
            return 0;
        }
        if (c->io.erase(c->io.ctx, c->cursor)) { publish_error(c, NR_ERR_ERASE); return -1; }
        c->cursor += NR_SECTOR_SIZE;
        return 0;

    case NR_S_PROGRAM:
        if (c->cursor >= c->offset + c->blk_len) { c->state = NR_S_COMMIT; return 0; }
        if ((c->cursor % NR_PAGE_SIZE) != 0u) { publish_error(c, NR_ERR_BAD_REQUEST); return -1; }
        n = c->offset + c->blk_len - c->cursor;
        if (n > NR_PAGE_SIZE) n = NR_PAGE_SIZE;
        if (c->io.program(c->io.ctx, c->cursor,
                          (const uint8_t *)mb->data + (c->cursor - c->offset), n)) {
            publish_error(c, NR_ERR_PROGRAM); return -1;
        }
        c->page_len = (uint16_t)n;
        c->state = NR_S_VERIFY_PAGE;
        return 0;

    case NR_S_VERIFY_PAGE:
        if (c->io.read(c->io.ctx, c->cursor, c->scratch, c->page_len)) {
            publish_error(c, NR_ERR_VERIFY); return -1;
        }
        if (memcmp(c->scratch, (const uint8_t *)mb->data + (c->cursor - c->offset),
                   c->page_len) != 0) {
            publish_error(c, NR_ERR_VERIFY); return -1;
        }
        c->cursor += c->page_len;
        c->state = NR_S_PROGRAM;
        return 0;

    case NR_S_COMMIT:
        /* Echo exactly what was committed, then raise BLOCK_OK last. */
        mb->h.seq = c->seq + 1u;
        mb->h.offset = c->offset;
        mb->h.length = c->blk_len;
        mb->h.crc32 = c->blk_crc;
        mb->h.ack = 0u;              /* consume the doorbell before publishing */
        c->seq += 1u;
        c->offset += c->blk_len;
        c->blocks_done += 1u;
        c->bytes_done += c->blk_len;
        if (c->offset >= c->end) {
            c->cursor = c->start;
            c->crc = 0u;
            c->state = NR_S_FINAL_CRC;
            publish_status(c, NR_ST_BUSY);
            return 0;
        }
        publish_status(c, NR_ST_BLOCK_OK);
        c->state = NR_S_READY;
        return 0;

    case NR_S_FINAL_CRC:
        /* status stays BUSY for the whole sweep: a DONE here would be a lie. */
        if (c->cursor >= c->end) {
            mb->h.seq = c->seq;
            mb->h.offset = c->end;
            mb->h.length = 0u;
            mb->h.crc32 = c->crc;
            mb->h.ack = 0u;
            publish_status(c, NR_ST_DONE);
            c->state = NR_S_DONE_ST;
            return 1;
        }
        n = c->end - c->cursor;
        if (n > NR_PAGE_SIZE) n = NR_PAGE_SIZE;
        if (c->io.read(c->io.ctx, c->cursor, c->scratch, n)) {
            publish_error(c, NR_ERR_VERIFY); return -1;
        }
        c->crc = crc32_update(c->crc, c->scratch, n);
        c->cursor += n;
        return 0;

    case NR_S_DONE_ST:
        return 1;
    case NR_S_ERROR_ST:
    default:
        return -1;
    }
}
