/* Host-side test of the real NOR streaming state machine (nor_stream.c),
 * compiled with a SMALL model capacity (NOR_TOTAL_SIZE overridden on the
 * command line) to exercise first/middle/last blocks quickly. This is a model
 * configuration, not the 16 MiB physical capacity. */
#include "nor_stream.h"
#include <stdio.h>
#include <string.h>

static volatile nor_mailbox_t mb;
static uint32_t now_ms;
static int read_calls;
static int publish_order_ok;
static int fail_read_at; /* -1 = never fail */

static int mock_read(uint32_t addr, uint8_t *out, uint32_t n)
{
    (void)addr;
    /* The firmware must have set BUSY before touching the buffer. */
    if (mb.status != MB_STATUS_BUSY) publish_order_ok = 0;
    read_calls++;
    if (fail_read_at >= 0 && (read_calls - 1) == fail_read_at) return -1;
    memset(out, 0xA5, n);
    return 0;
}

static uint32_t mock_now(void) { return now_ms; }

#define CHECK(cond) do { if (!(cond)) { printf("FAIL %s:%d\n", __FILE__, __LINE__); return 1; } } while (0)

int main(void)
{
    nor_stream_t s;
    int r;

    /* 1. init publishes magic/version and IDLE. */
    now_ms = 0; read_calls = 0; publish_order_ok = 1; fail_read_at = -1;
    nor_stream_init(&s, &mb, mock_read, mock_now);
    CHECK(mb.magic == NOR_MAILBOX_MAGIC && mb.version == NOR_MAILBOX_VERSION);
    CHECK(mb.status == MB_STATUS_IDLE && mb.seq == 0);

    /* 2. session handshake then first block. */
    mb.session = 0x12345678u;
    r = nor_stream_service(&s, 1000u);   /* WAIT_SESSION -> PUBLISH */
    CHECK(r == 0);
    r = nor_stream_service(&s, 1000u);   /* publish block 1 */
    CHECK(r == 0 && mb.status == MB_STATUS_READY);
    CHECK(mb.seq == 1 && mb.offset == 0 && mb.length == NOR_BLOCK_SIZE);
    CHECK(mb.session == 0x12345678u);
    CHECK(publish_order_ok); /* BUSY was set before read */

    /* 3. ack advances to block 2, then block 3 (last), then DONE. */
    mb.ack = 1;
    r = nor_stream_service(&s, 1000u);   /* consume ack -> PUBLISH */
    CHECK(r == 0);
    r = nor_stream_service(&s, 1000u);   /* publish block 2 */
    CHECK(r == 0 && mb.seq == 2 && mb.offset == NOR_BLOCK_SIZE);
    mb.ack = 2;
    r = nor_stream_service(&s, 1000u);
    r = nor_stream_service(&s, 1000u);
    CHECK(r == 0 && mb.seq == 3 && mb.offset == 2 * NOR_BLOCK_SIZE);
    mb.ack = 3;
    r = nor_stream_service(&s, 1000u);
    r = nor_stream_service(&s, 1000u);
    CHECK(r == 1 && mb.status == MB_STATUS_DONE);

    /* 4. SPI failure publishes ERROR with SPI code. */
    nor_stream_init(&s, &mb, mock_read, mock_now);
    read_calls = 0; fail_read_at = 0;
    mb.session = 1u;
    r = nor_stream_service(&s, 1000u);   /* WAIT_SESSION -> PUBLISH */
    r = nor_stream_service(&s, 1000u);   /* read fails -> ERROR */
    CHECK(r == -1 && mb.status == MB_STATUS_ERROR && mb.error == MB_ERR_SPI);

    /* 5. ACK timeout uses the monotonic clock. */
    nor_stream_init(&s, &mb, mock_read, mock_now);
    read_calls = 0; fail_read_at = -1; now_ms = 0;
    mb.session = 1u;
    r = nor_stream_service(&s, 100u);    /* WAIT_SESSION -> PUBLISH */
    r = nor_stream_service(&s, 100u);    /* publish block 1, deadline = 100 */
    CHECK(r == 0 && mb.status == MB_STATUS_READY);
    now_ms = 200;
    r = nor_stream_service(&s, 100u);    /* WAIT_ACK times out */
    CHECK(r == -1 && mb.error == MB_ERR_ACK_TIMEOUT);

    /* 6. reset: a fresh init clears seq/session/status. */
    nor_stream_init(&s, &mb, mock_read, mock_now);
    CHECK(mb.status == MB_STATUS_IDLE && mb.seq == 0 && mb.session == 0);

    printf("nor_stream C tests passed (read_calls=%d)\n", read_calls);
    return 0;
}
