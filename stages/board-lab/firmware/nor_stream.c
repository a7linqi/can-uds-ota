#include "nor_stream.h"
#include "ota/crc32.h"
#include <string.h>

#if defined(STM32F407xx) && defined(__GNUC__)
/* Data memory barrier so the SWD/AHB observer sees the header stores before
 * the READY/ERROR status store. Does not need the full CMSIS device header. */
#define NOR_PUBLISH_BARRIER() __asm__ volatile("dmb" ::: "memory")
#else
/* Host-side test build: volatile stores already forbid compiler reordering. */
#define NOR_PUBLISH_BARRIER() ((void)0)
#endif

void nor_stream_init(nor_stream_t *s, volatile nor_mailbox_t *mb,
                     nor_stream_read_fn read, nor_stream_now_ms_fn now_ms)
{
    s->mb = mb;
    s->read = read;
    s->now_ms = now_ms;
    s->session = 0u;
    s->seq = 0u;
    s->offset = 0u;
    s->ack_deadline = 0u;
    s->state = NOR_STREAM_WAIT_SESSION;
    memset((void *)mb, 0, sizeof(*mb));
    mb->magic = NOR_MAILBOX_MAGIC;
    mb->version = NOR_MAILBOX_VERSION;
    mb->status = MB_STATUS_IDLE;
    mb->error = MB_ERR_NONE;
}

/* Publish an error: write the error code first, then status=ERROR last. */
static void publish_error(nor_stream_t *s, uint32_t err)
{
    s->mb->error = err;
    NOR_PUBLISH_BARRIER();
    s->mb->status = MB_STATUS_ERROR;
    s->state = NOR_STREAM_ERROR;
}

int nor_stream_service(nor_stream_t *s, uint32_t ack_timeout_ms)
{
    volatile nor_mailbox_t *mb = s->mb;
    uint32_t n, crc;

    switch (s->state) {
    case NOR_STREAM_WAIT_SESSION:
        if (mb->session != 0u) {
            s->session = mb->session;
            s->seq = 0u;
            s->offset = 0u;
            s->state = NOR_STREAM_PUBLISH;
        }
        return 0;

    case NOR_STREAM_PUBLISH:
        if (s->offset >= NOR_TOTAL_SIZE) {
            mb->status = MB_STATUS_DONE;
            s->state = NOR_STREAM_DONE;
            return 1;
        }
        n = NOR_TOTAL_SIZE - s->offset;
        if (n > NOR_BLOCK_SIZE) n = NOR_BLOCK_SIZE;
        /* 1. Leave READY before touching the buffer, so the host never sees a
         *    half-updated block. */
        mb->status = MB_STATUS_BUSY;
        /* 2. Fill the data buffer (still BUSY). */
        if (s->read(s->offset, (uint8_t *)mb->data, n)) {
            publish_error(s, MB_ERR_SPI);
            return -1;
        }
        /* 3. Prepare every header field, then publish READY as the LAST store. */
        crc = crc32_calc((const uint8_t *)mb->data, n);
        mb->session = s->session;
        mb->seq = s->seq + 1u;
        mb->offset = s->offset;
        mb->length = n;
        mb->crc32 = crc;
        NOR_PUBLISH_BARRIER();
        mb->status = MB_STATUS_READY;
        s->seq += 1u;
        s->offset += n;
        s->state = NOR_STREAM_WAIT_ACK;
        s->ack_deadline = s->now_ms() + ack_timeout_ms;
        return 0;

    case NOR_STREAM_WAIT_ACK:
        if (mb->ack == s->seq) {
            s->state = NOR_STREAM_PUBLISH;
            return 0;
        }
        /* Wrap-safe monotonic difference. */
        if ((int32_t)(s->now_ms() - s->ack_deadline) >= 0) {
            publish_error(s, MB_ERR_ACK_TIMEOUT);
            return -1;
        }
        return 0;

    case NOR_STREAM_DONE:
        return 1;
    case NOR_STREAM_ERROR:
    default:
        return -1;
    }
}
