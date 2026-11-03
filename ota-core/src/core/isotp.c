#include "ota/isotp.h"

#include <string.h>

#define ISOTP_N_BS_MS  1000u
#define ISOTP_N_CR_MS  1000u

static uint32_t link_now(isotp_link_t *l)
{
    if (l->port.now_ms != 0) {
        return l->port.now_ms(l->port.user);
    }
    return 0u;
}

static int link_send(isotp_link_t *l, const uint8_t *data, uint8_t dlc)
{
    can_frame_t f;

    if (l->port.can_send == 0) {
        return ISOTP_ERR_SEND;
    }
    memset(&f, 0, sizeof f);
    f.id = l->tx_id;
    f.dlc = dlc;
    f.extended = (l->tx_id > 0x7FFu);
    memcpy(f.data, data, dlc);
    return l->port.can_send(l->port.user, &f);
}

static int link_send_fc(isotp_link_t *l, uint8_t status)
{
    uint8_t f[3];

    f[0] = (uint8_t)(0x30u | (uint32_t)(status & 0x0Fu));
    f[1] = l->flow_bs;
    f[2] = l->flow_stmin;
    return link_send(l, f, 3u);
}

static int abort_tx(isotp_link_t *l,int error)
{
    l->tx_state=ISOTP_IDLE;
    l->tx_len=0u;
    l->tx_error=error;
    return error;
}

void isotp_init(isotp_link_t *l, const ota_port_t *port, uint32_t tx_id, uint32_t rx_id)
{
    memset(l, 0, sizeof *l);
    if (port != 0) {
        l->port = *port;
    }
    l->tx_id = tx_id;
    l->rx_id = rx_id;
    l->rx_state = ISOTP_IDLE;
    l->tx_state = ISOTP_IDLE;
}

void isotp_set_flow(isotp_link_t *l, uint8_t bs, uint8_t stmin_encoded)
{
    l->flow_bs = bs;
    l->flow_stmin = stmin_encoded;
}

int isotp_send(isotp_link_t *l, const uint8_t *data, uint16_t len)
{
    uint8_t f[8];

    if (l == 0 || data == 0) {
        return ISOTP_ERR_LENGTH;
    }
    if (len == 0u || len > ISOTP_MAX_PAYLOAD || len > 4095u) {
        return ISOTP_ERR_LENGTH;
    }
    if (l->tx_state != ISOTP_IDLE) {
        return ISOTP_ERR_BUSY;
    }

    memcpy(l->tx_buf, data, len);
    l->tx_len = len;
    l->tx_sent = 0u;
    l->tx_error = 0;
    l->tx_wait_count = 0u;

    if (len <= 7u) {
        memset(f, 0, sizeof f);
        f[0] = (uint8_t)len;
        memcpy(&f[1], data, len);
        if(link_send(l,f,(uint8_t)(len+1u))) return abort_tx(l,ISOTP_ERR_SEND);
        return ISOTP_OK;
    }

    memset(f, 0, sizeof f);
    f[0] = (uint8_t)(0x10u | ((len >> 8) & 0x0Fu));
    f[1] = (uint8_t)(len & 0xFFu);
    memcpy(&f[2], data, 6);
    l->tx_sent = 6u;
    /* FF has no SN field. The first consecutive frame starts at SN=1. */
    l->tx_sn = 1u;
    l->tx_block_cnt = 0u;
    /* state must change before the frame hits the bus: the peer may
       answer synchronously (virtual bus in host tests). */
    l->tx_state = ISOTP_WAIT_FC;
    l->tx_last_ms = link_now(l);
    if(link_send(l,f,8u)) return abort_tx(l,ISOTP_ERR_SEND);
    return ISOTP_OK;
}

int isotp_poll(isotp_link_t *l)
{
    uint32_t now = link_now(l);

    if (l->rx_done) {
        return 1;
    }

    if (l->tx_state == ISOTP_SENDING_CF) {
        uint8_t f[8];
        uint8_t n = 0u;
        uint8_t dlc;

        if ((uint32_t)(now - l->tx_last_ms) < l->fc_stmin) {
            return 0;
        }
        memset(f, 0, sizeof f);
        f[0] = (uint8_t)(0x20u | (uint32_t)(l->tx_sn & 0x0Fu));
        while (n < 7u && l->tx_sent < l->tx_len) {
            f[1u + n] = l->tx_buf[l->tx_sent];
            l->tx_sent++;
            n++;
        }
        dlc = (uint8_t)(n + 1u);
        l->tx_sn = (uint8_t)((l->tx_sn + 1u) & 0x0Fu);
        l->tx_last_ms = now;
        if (l->tx_sent >= l->tx_len) {
            l->tx_state = ISOTP_IDLE;
        } else {
            l->tx_block_cnt++;
            if (l->fc_bs != 0u && l->tx_block_cnt >= l->fc_bs) {
                l->tx_block_cnt = 0u;
                l->tx_state = ISOTP_WAIT_FC;
                l->tx_last_ms = now;
            }
        }
        if (link_send(l, f, dlc) != 0) {
            return abort_tx(l,ISOTP_ERR_SEND);
        }
        return l->rx_done ? 1 : 0;
    }

    if (l->tx_state == ISOTP_WAIT_FC) {
        if ((uint32_t)(now - l->tx_last_ms) > ISOTP_N_BS_MS) {
            l->tx_state = ISOTP_IDLE;
            l->tx_len = 0u;
            l->tx_error = ISOTP_ERR_TIMEOUT;
            return ISOTP_ERR_TIMEOUT;
        }
    }

    if (l->rx_state == ISOTP_WAIT_CF) {
        if ((uint32_t)(now - l->rx_last_ms) > ISOTP_N_CR_MS) {
            l->rx_state = ISOTP_IDLE;
            l->rx_got = 0u;
            l->rx_error = ISOTP_ERR_TIMEOUT;
            return ISOTP_ERR_TIMEOUT;
        }
    }

    return 0;
}

int isotp_read(isotp_link_t *l, uint8_t *out, uint16_t out_max, uint16_t *out_len)
{
    if (l == 0 || out == 0 || out_len == 0) {
        return ISOTP_ERR_LENGTH;
    }
    if (!l->rx_done) {
        return 0;
    }
    if (l->rx_len > out_max) {
        l->rx_done = 0u;
        l->rx_len = 0u;
        return ISOTP_ERR_OVERFLOW;
    }
    memcpy(out, l->rx_buf, l->rx_len);
    *out_len = l->rx_len;
    l->rx_done = 0u;
    l->rx_len = 0u;
    return 1;
}

void isotp_on_frame(isotp_link_t *l, const can_frame_t *f)
{
    uint8_t pci;
    uint16_t len;

    if (l == 0 || f == 0) {
        return;
    }
    if (f->id != l->rx_id || (f->extended!=0u)!=(l->rx_id>0x7FFu)) {
        return;
    }
    if (f->dlc == 0u || f->dlc > 8u) {
        return;
    }

    pci = (uint8_t)(f->data[0] >> 4);
    /* One completed message may wait for the owner to read it. Incoming
     * traffic must not silently replace it; FC for our TX is still allowed. */
    if(l->rx_done && pci!=3u) return;

    switch (pci) {
    case 0x0u: /* single frame */
        len = (uint16_t)(f->data[0] & 0x0Fu);
        if (len == 0u || len > (uint16_t)(f->dlc - 1u)) {
            return;
        }
        l->rx_state = ISOTP_IDLE;
        l->rx_error = 0;
        memcpy(l->rx_buf, &f->data[1], len);
        l->rx_len = len;
        l->rx_got = len;
        l->rx_done = 1u;
        break;

    case 0x1u: /* first frame */
        if (f->dlc < 3u) return; /* prevent dlc-2 underflow / over-read */
        len = (uint16_t)((((uint16_t)f->data[0] & 0x0Fu) << 8) | (uint16_t)f->data[1]);
        if (len < 8u) return;
        if (len > ISOTP_MAX_PAYLOAD) {
            l->rx_state = ISOTP_IDLE;
            l->rx_error = ISOTP_ERR_OVERFLOW;
            if(link_send_fc(l,2u)) l->rx_error=ISOTP_ERR_SEND;
            return;
        }
        l->rx_error = 0;
        l->rx_len = len;
        l->rx_got = (uint16_t)(f->dlc - 2u);
        if (l->rx_got > len) {
            l->rx_got = len;
        }
        memcpy(l->rx_buf, &f->data[2], l->rx_got);
        l->rx_sn = 1u;
        l->rx_block_cnt = 0u;
        l->rx_last_ms = link_now(l);
        l->rx_state = ISOTP_WAIT_CF;
        if(link_send_fc(l,0u)) {
            l->rx_state=ISOTP_IDLE; l->rx_error=ISOTP_ERR_SEND;
        }
        break;

    case 0x2u: { /* consecutive frame */
        uint8_t sn = (uint8_t)(f->data[0] & 0x0Fu);
        uint16_t room;
        uint16_t n;

        if (l->rx_state != ISOTP_WAIT_CF || f->dlc < 2u) {
            return;
        }
        if (sn != l->rx_sn) {
            l->rx_state = ISOTP_IDLE;
            l->rx_done = 0u;
            l->rx_error = ISOTP_ERR_SEQUENCE;
            return;
        }
        room = (uint16_t)(l->rx_len - l->rx_got);
        n = (uint16_t)(f->dlc - 1u);
        if (n > room) {
            n = room;
        }
        memcpy(&l->rx_buf[l->rx_got], &f->data[1], n);
        l->rx_got = (uint16_t)(l->rx_got + n);
        l->rx_sn = (uint8_t)((l->rx_sn + 1u) & 0x0Fu);
        l->rx_last_ms = link_now(l);
        if (l->rx_got >= l->rx_len) {
            l->rx_state = ISOTP_IDLE;
            l->rx_done = 1u;
        } else {
            l->rx_block_cnt++;
            if (l->flow_bs != 0u && l->rx_block_cnt >= l->flow_bs) {
                l->rx_block_cnt = 0u;
                if(link_send_fc(l,0u)) {
                    l->rx_state=ISOTP_IDLE; l->rx_error=ISOTP_ERR_SEND;
                }
            }
        }
        break;
    }

    case 0x3u: { /* flow control */
        uint8_t status = (uint8_t)(f->data[0] & 0x0Fu);

        if (l->tx_state != ISOTP_WAIT_FC) {
            return;
        }
        if (f->dlc < 3u) {
            return;
        }
        if (status == 0u) { /* continue to send */
            l->fc_bs = f->data[1];
            uint8_t encoded=f->data[2];
            if(encoded<=0x7Fu) l->fc_stmin=encoded;
            else if(encoded>=0xF1u && encoded<=0xF9u) l->fc_stmin=1u;
            else { (void)abort_tx(l,ISOTP_ERR_FLOW); return; }
            l->tx_block_cnt = 0u;
            l->tx_wait_count = 0u;
            l->tx_last_ms = link_now(l);
            l->tx_state = ISOTP_SENDING_CF;
        } else if (status == 1u) { /* wait: restart N_BS timer */
            l->tx_wait_count++;
            if(l->tx_wait_count>ISOTP_MAX_WAIT_FRAMES) {
                (void)abort_tx(l,ISOTP_ERR_FLOW); return;
            }
            l->tx_last_ms = link_now(l);
        } else { /* overflow */
            (void)abort_tx(l,ISOTP_ERR_FLOW);
        }
        break;
    }

    default:
        break;
    }
}

int isotp_busy(const isotp_link_t *l)
{
    if (l == 0) {
        return 0;
    }
    return (l->tx_state != ISOTP_IDLE) || (l->rx_state != ISOTP_IDLE);
}
