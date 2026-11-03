#ifndef BENCH_H
#define BENCH_H
#include "ota/isotp.h"
#include "ota/uds.h"
#include "ota/boot.h"
#include "flash_ram.h"
#define BENCH_REQ_MAX 2048u
#define BENCH_RESP_MAX 512u
typedef struct bench bench_t;
typedef struct { bench_t *b; uint8_t self; } bench_node_t;
struct bench {
    uint8_t internal_mem[FLASH_TOTAL_SIZE],external_mem[EXT_FLASH_TOTAL_SIZE];
    flash_ram_t flash,external;
    eeprom_ram_t eeprom;
    storage_fault_t fault;
    metadata_store_t meta;
    boot_context_t boot;
    ota_port_t ecu_port,tester_port;
    isotp_link_t ecu_link,tester_link;
    uds_server_t server;
    bench_node_t ecu_node,tester_node;
    uint32_t now_ms,frames;
    int drop_fc,drop_cf;
    uint8_t req_buf[BENCH_REQ_MAX],resp_buf[BENCH_RESP_MAX],tester_rx[BENCH_RESP_MAX];
    uint16_t tester_rx_len;
    int resp_ready;
};
void bench_init(bench_t *b);
/* Recreate ALL volatile core/driver objects; preserve storage bytes. */
void bench_reboot(bench_t *b);
void bench_reset_links(bench_t *b);
int bench_run(bench_t *b);
int bench_request(bench_t *b,const uint8_t *req,uint16_t req_len,uint8_t *resp,uint16_t *resp_len);
int bench_send_tester(bench_t *b,const uint8_t *data,uint16_t len);
int bench_read_ecu(bench_t *b,uint8_t *out,uint16_t out_max,uint16_t *out_len);
void bench_raw_to_ecu(bench_t *b,const can_frame_t *f);
#endif
