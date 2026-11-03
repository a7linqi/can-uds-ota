#ifndef OTA_ISOTP_H
#define OTA_ISOTP_H

#include <stdint.h>
#include "ota/ota_port.h"

/* ISO 15765-2 transport on classic CAN.
 * Implemented subset: SF / FF / CF / FC, block size, STmin,
 * N_CR / N_BS timeouts, 12-bit length (max 4095 bytes). */
#ifndef ISOTP_MAX_PAYLOAD
#define ISOTP_MAX_PAYLOAD 2048u
#endif
#define ISOTP_MAX_WAIT_FRAMES 3u

#define ISOTP_OK            0
#define ISOTP_ERR_BUSY     -1
#define ISOTP_ERR_LENGTH   -2
#define ISOTP_ERR_TIMEOUT  -3
#define ISOTP_ERR_SEQUENCE -4
#define ISOTP_ERR_OVERFLOW -5
#define ISOTP_ERR_SEND     -6
#define ISOTP_ERR_FLOW     -7

typedef enum {
    ISOTP_IDLE = 0,
    ISOTP_WAIT_CF,      /* receiver: FF seen, waiting consecutive frames */
    ISOTP_WAIT_FC,      /* sender: waiting for flow control              */
    ISOTP_SENDING_CF    /* sender: flow control received, sending CFs    */
} isotp_state_t;

typedef struct {
    ota_port_t port;
    uint32_t   tx_id;   /* id this link transmits on  */
    uint32_t   rx_id;   /* id this link receives on   */

    /* flow control parameters this link advertises as a receiver */
    uint8_t flow_bs;
    uint8_t flow_stmin;

    /* receive side */
    isotp_state_t rx_state;
    uint8_t  rx_buf[ISOTP_MAX_PAYLOAD];
    uint16_t rx_len;
    uint16_t rx_got;
    uint8_t  rx_sn;
    uint8_t  rx_block_cnt;
    uint8_t  rx_done;
    uint32_t rx_last_ms;
    int      rx_error;

    /* transmit side */
    isotp_state_t tx_state;
    uint8_t  tx_buf[ISOTP_MAX_PAYLOAD];
    uint16_t tx_len;
    uint16_t tx_sent;
    uint8_t  tx_sn;
    uint8_t  tx_block_cnt;
    uint8_t  tx_wait_count;
    uint8_t  fc_bs;
    uint8_t  fc_stmin; /* decoded milliseconds; 100..900 us round up to 1 ms */
    uint32_t tx_last_ms;
    int      tx_error;
} isotp_link_t;

void isotp_init(isotp_link_t *l, const ota_port_t *port, uint32_t tx_id, uint32_t rx_id);
void isotp_set_flow(isotp_link_t *l, uint8_t bs, uint8_t stmin_encoded);

/* Start a transfer. Copies the payload into tx_buf, so the caller's
   buffer can go out of scope immediately. */
int isotp_send(isotp_link_t *l, const uint8_t *data, uint16_t len);

/* Drive the state machine. Call from the main loop (or 1 ms task).
   returns  1 : a complete message is available (read with isotp_read)
   returns  0 : nothing to report
   returns <0 : timeout / protocol error                             */
int isotp_poll(isotp_link_t *l);

/* 1 = message copied out, 0 = nothing pending, <0 = buffer too small. */
int isotp_read(isotp_link_t *l, uint8_t *out, uint16_t out_max, uint16_t *out_len);

/* Feed a received CAN frame; must be called from the CAN RX path. */
void isotp_on_frame(isotp_link_t *l, const can_frame_t *f);

int isotp_busy(const isotp_link_t *l);

#endif /* OTA_ISOTP_H */
