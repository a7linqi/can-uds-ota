#include "bench.h"
#include "test_util.h"

#include <string.h>

static bench_t B;
static uint8_t tx[ISOTP_MAX_PAYLOAD];
static uint8_t rx[ISOTP_MAX_PAYLOAD];
static uint8_t echo[ISOTP_MAX_PAYLOAD];

static void roundtrip(uint16_t len)
{
    uint16_t out_len = 0u;
    uint16_t i;

    for (i = 0u; i < len; i++) {
        tx[i] = (uint8_t)((i * 31u) + 7u);
    }

    TEST_ASSERT(isotp_send(&B.tester_link, tx, len) == ISOTP_OK);
    TEST_ASSERT(bench_run(&B) == 0);
    TEST_ASSERT(isotp_read(&B.ecu_link, rx, ISOTP_MAX_PAYLOAD, &out_len) == 1);
    TEST_ASSERT(out_len == len);
    TEST_ASSERT(memcmp(tx, rx, len) == 0);

    TEST_ASSERT(isotp_send(&B.ecu_link, rx, out_len) == ISOTP_OK);
    TEST_ASSERT(bench_run(&B) == 0);
    out_len = 0u;
    TEST_ASSERT(isotp_read(&B.tester_link, echo, ISOTP_MAX_PAYLOAD, &out_len) == 1);
    TEST_ASSERT(out_len == len);
    TEST_ASSERT(memcmp(tx, echo, len) == 0);
}

static void stmin_test(void)
{
    uint16_t out = 0u;
    uint32_t t0;
    uint16_t i;

    for (i = 0u; i < 200u; i++) {
        tx[i] = (uint8_t)i;
    }
    isotp_set_flow(&B.ecu_link, 0u, 5u);
    t0 = B.now_ms;
    TEST_ASSERT(isotp_send(&B.tester_link, tx, 200u) == ISOTP_OK);
    TEST_ASSERT(bench_run(&B) == 0);
    TEST_ASSERT(isotp_read(&B.ecu_link, rx, ISOTP_MAX_PAYLOAD, &out) == 1);
    TEST_ASSERT(out == 200u);
    /* 194 payload bytes -> 28 consecutive frames, >= 100 ms pacing */
    TEST_ASSERT((B.now_ms - t0) >= 100u);
    isotp_set_flow(&B.ecu_link, 8u, 0u);
}

static void drop_fc_test(void)
{
    uint16_t i;

    for (i = 0u; i < 100u; i++) {
        tx[i] = (uint8_t)i;
    }
    B.drop_fc = 1; /* the ECU's flow control answer never reaches the tester */
    TEST_ASSERT(isotp_send(&B.tester_link, tx, 100u) == ISOTP_OK);
    TEST_ASSERT(bench_run(&B) < 0);
    B.drop_fc = 0;
    TEST_ASSERT(B.tester_link.tx_error == ISOTP_ERR_TIMEOUT);
}

static void frame_mismatch_test(void)
{
    can_frame_t f;

    memset(&f, 0, sizeof f);
    f.id = 0x7E0u;
    f.dlc = 4u;
    f.data[0] = 0x11u; /* FF, length 0x014 = 20 */
    f.data[1] = 0x14u;
    f.data[2] = 0x01u;
    f.data[3] = 0x02u;
    bench_raw_to_ecu(&B, &f);
    TEST_ASSERT(B.ecu_link.rx_state == ISOTP_WAIT_CF);

    f.data[0] = 0x22u; /* SN = 2, expected 1 */
    f.dlc = 8u;
    f.data[1] = 0x03u;
    f.data[2] = 0x04u;
    f.data[3] = 0x05u;
    f.data[4] = 0x06u;
    f.data[5] = 0x07u;
    f.data[6] = 0x08u;
    f.data[7] = 0x09u;
    bench_raw_to_ecu(&B, &f);
    TEST_ASSERT(B.ecu_link.rx_state == ISOTP_IDLE);
    TEST_ASSERT(B.ecu_link.rx_done == 0u);
    TEST_ASSERT(B.ecu_link.rx_error == ISOTP_ERR_SEQUENCE);
}

static void rx_timeout_test(void)
{
    can_frame_t f;

    memset(&f, 0, sizeof f);
    f.id = 0x7E0u;
    f.dlc = 6u;
    f.data[0] = 0x11u; /* FF len 0x014 = 20, only 4 bytes follow */
    f.data[1] = 0x14u;
    f.data[2] = 0x01u;
    f.data[3] = 0x02u;
    f.data[4] = 0x03u;
    f.data[5] = 0x04u;
    bench_raw_to_ecu(&B, &f);
    TEST_ASSERT(B.ecu_link.rx_state == ISOTP_WAIT_CF);
    B.now_ms += 1500u;
    TEST_ASSERT(isotp_poll(&B.ecu_link) < 0);
    TEST_ASSERT(B.ecu_link.rx_state == ISOTP_IDLE);
}

static void short_frame_test(void)
{
    can_frame_t f;
    uint32_t last;
    memset(&f,0,sizeof f); f.id=0x7E0u;
    f.data[0]=0x10u; f.data[1]=8u;
    f.dlc=1u; bench_raw_to_ecu(&B,&f);
    TEST_ASSERT(B.ecu_link.rx_state==ISOTP_IDLE);
    f.dlc=2u; bench_raw_to_ecu(&B,&f);
    TEST_ASSERT(B.ecu_link.rx_state==ISOTP_IDLE);
    f.dlc=8u; bench_raw_to_ecu(&B,&f);
    TEST_ASSERT(B.ecu_link.rx_state==ISOTP_WAIT_CF);
    last=B.ecu_link.rx_last_ms; B.now_ms+=1u;
    f.dlc=1u; f.data[0]=0x21u; bench_raw_to_ecu(&B,&f);
    TEST_ASSERT(B.ecu_link.rx_last_ms==last && !B.ecu_link.rx_done);
}

void run_isotp_tests(void)
{
    bench_init(&B);
    printf("[isotp]\n");

    roundtrip(1u);
    roundtrip(2u);
    roundtrip(3u);
    roundtrip(4u);
    roundtrip(5u);
    roundtrip(6u);
    roundtrip(7u);
    roundtrip(8u);
    roundtrip(20u);
    roundtrip(100u);
    roundtrip(1024u);
    roundtrip(2000u);

    bench_reset_links(&B);
    stmin_test();

    bench_reset_links(&B);
    drop_fc_test();

    bench_reset_links(&B);
    frame_mismatch_test();

    bench_reset_links(&B);
    rx_timeout_test();

    bench_reset_links(&B);
    short_frame_test();
}
