#ifndef NOR_STREAM_H
#define NOR_STREAM_H
#include "nor_mailbox.h"

/* Testable NOR streaming state machine with no HAL dependency. The firmware
 * main drives it; a host-side C test drives it with mock read/clock to assert
 * the publication ordering and handshake. */

typedef int (*nor_stream_read_fn)(uint32_t addr, uint8_t *out, uint32_t n);
typedef uint32_t (*nor_stream_now_ms_fn)(void);

enum {
    NOR_STREAM_WAIT_SESSION = 0,
    NOR_STREAM_PUBLISH,
    NOR_STREAM_WAIT_ACK,
    NOR_STREAM_DONE,
    NOR_STREAM_ERROR
};

typedef struct {
    volatile nor_mailbox_t *mb;
    nor_stream_read_fn read;
    nor_stream_now_ms_fn now_ms;
    uint32_t session;
    uint32_t seq;
    uint32_t offset;
    uint32_t ack_deadline;
    uint8_t state;
} nor_stream_t;

void nor_stream_init(nor_stream_t *s, volatile nor_mailbox_t *mb,
                     nor_stream_read_fn read, nor_stream_now_ms_fn now_ms);

/* Drive one step. Returns 0 = keep going, 1 = finished (DONE published),
 * -1 = error (ERROR published, code in mb->error). ack_timeout_ms bounds each
 * ACK wait using a monotonic, wrap-safe difference. */
int nor_stream_service(nor_stream_t *s, uint32_t ack_timeout_ms);

#endif
