#include "bench.h"

#include <string.h>

static int node_can_send(void *user, const can_frame_t *f)
{
    bench_node_t *n = (bench_node_t *)user;
    bench_t *b = n->b;
    isotp_link_t *peer = n->self ? &b->tester_link : &b->ecu_link;
    uint8_t pci = (uint8_t)(f->data[0] >> 4);

    b->frames++;
    if (pci == 3u && b->drop_fc > 0) {
        b->drop_fc--;
        return 0;
    }
    if (pci == 2u && b->drop_cf > 0) {
        b->drop_cf--;
        return 0;
    }
    isotp_on_frame(peer, f);
    return 0;
}

static uint32_t node_now(void *user)
{
    bench_node_t *n = (bench_node_t *)user;

    return n->b->now_ms;
}

void bench_reset_links(bench_t *b)
{
    isotp_init(&b->ecu_link, &b->ecu_port, 0x7E8u, 0x7E0u);
    isotp_init(&b->tester_link, &b->tester_port, 0x7E0u, 0x7E8u);
    isotp_set_flow(&b->ecu_link, 8u, 0u);
    isotp_set_flow(&b->tester_link, 0u, 0u);
}

void bench_reboot(bench_t *b)
{
    storage_fault_reset(&b->fault);
    flash_ram_attach(&b->flash,b->internal_mem,FLASH_TOTAL_SIZE,0,&b->fault);
    flash_ram_attach(&b->external,b->external_mem,EXT_FLASH_TOTAL_SIZE,1,&b->fault);
    b->eeprom.fault=&b->fault;
    b->eeprom.read_fail_after=-1;
    b->eeprom.ignore_writes=0;
    b->eeprom.write_ops=0u;
    metadata_store_init(&b->meta,eeprom_ram_port(&b->eeprom));
    boot_context_init(&b->boot,flash_ram_port(&b->flash),flash_ram_port(&b->external),&b->meta);
    b->drop_fc=0; b->drop_cf=0; b->frames=0u;

    b->ecu_node.b = b;
    b->ecu_node.self = 1u;
    b->tester_node.b = b;
    b->tester_node.self = 0u;

    b->ecu_port.can_send = node_can_send;
    b->ecu_port.now_ms = node_now;
    b->ecu_port.user = &b->ecu_node;

    b->tester_port.can_send = node_can_send;
    b->tester_port.now_ms = node_now;
    b->tester_port.user = &b->tester_node;

    bench_reset_links(b);

    uds_init(&b->server, &b->boot, &b->ecu_port);
    b->now_ms = 1u;
}

int bench_run(bench_t *b)
{
    uint32_t i;

    for (i = 0u; i < 200000u; i++) {
        int e;
        uds_poll(&b->server);
        e = isotp_poll(&b->ecu_link);
        int t = isotp_poll(&b->tester_link);

        if (e < 0) {
            return e;
        }
        if (t < 0) {
            return t;
        }
        if (!isotp_busy(&b->ecu_link) && !isotp_busy(&b->tester_link)) {
            return 0;
        }
        b->now_ms++;
    }
    return -99;
}

int bench_request(bench_t *b, const uint8_t *req, uint16_t req_len,
                  uint8_t *resp, uint16_t *resp_len)
{
    uint32_t i;

    b->resp_ready = 0;
    b->tester_rx_len = 0u;

    if (isotp_send(&b->tester_link, req, req_len) != ISOTP_OK) {
        return -1;
    }

    for (i = 0u; i < 200000u; i++) {
        int ev;
        uds_poll(&b->server);
        ev = isotp_poll(&b->ecu_link);

        if (ev == 1) {
            uint16_t rl = 0u;
            if (isotp_read(&b->ecu_link, b->req_buf, BENCH_REQ_MAX, &rl) == 1) {
                uint16_t out = uds_handle(&b->server, b->req_buf, rl,
                                          b->resp_buf, BENCH_RESP_MAX);
                if (out > 0u) {
                    (void)isotp_send(&b->ecu_link, b->resp_buf, out);
                }
            }
        } else if (ev < 0) {
            return -1;
        }

        if (!b->resp_ready) {
            int tev = isotp_poll(&b->tester_link);
            if (tev == 1) {
                uint16_t l = 0u;
                if (isotp_read(&b->tester_link, b->tester_rx, BENCH_RESP_MAX, &l) == 1) {
                    b->tester_rx_len = l;
                    b->resp_ready = 1;
                }
            } else if (tev < 0) {
                return -1;
            }
        }

        if (b->resp_ready && !isotp_busy(&b->ecu_link) && !isotp_busy(&b->tester_link)) {
            break;
        }
        b->now_ms++;
    }

    if (!b->resp_ready) {
        return 0;
    }
    if (resp != 0 && resp_len != 0) {
        memcpy(resp, b->tester_rx, b->tester_rx_len);
        *resp_len = b->tester_rx_len;
    }
    return 1;
}

int bench_send_tester(bench_t *b, const uint8_t *data, uint16_t len)
{
    return isotp_send(&b->tester_link, data, len) == ISOTP_OK ? 0 : -1;
}

int bench_read_ecu(bench_t *b, uint8_t *out, uint16_t out_max, uint16_t *out_len)
{
    return isotp_read(&b->ecu_link, out, out_max, out_len);
}

void bench_raw_to_ecu(bench_t *b, const can_frame_t *f)
{
    isotp_on_frame(&b->ecu_link, f);
}

void bench_init(bench_t *b)
{
    memset(b,0,sizeof *b);
    memset(b->internal_mem,0xFF,sizeof b->internal_mem);
    memset(b->external_mem,0xFF,sizeof b->external_mem);
    memset(b->eeprom.mem,0xFF,sizeof b->eeprom.mem);
    bench_reboot(b);
}
