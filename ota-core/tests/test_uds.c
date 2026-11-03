#include "bench.h"
#include "test_util.h"

#include <string.h>

static bench_t B;
static uint8_t req[16];
static uint8_t resp[64];
static uint16_t rl;
static uint32_t seed;

static int call(uint16_t n)
{
    rl = 0u;
    return bench_request(&B, req, n, resp, &rl);
}

static void wrong_key_once(void)
{
    req[0] = 0x27;
    req[1] = 0x01;
    (void)call(2);
    seed = t_get_be32(&resp[2]);
    req[1] = 0x02;
    t_put_be32(&req[2], seed + 1u);
    (void)call(6);
}

void run_uds_tests(void)
{
    bench_init(&B);
    printf("[uds]\n");
    TEST_ASSERT(strlen("LOTA0000000000001") == 17u);

    /* 0x22 VIN */
    req[0] = 0x22;
    req[1] = 0xF1;
    req[2] = 0x90;
    TEST_ASSERT(call(3) == 1);
    TEST_ASSERT(rl == 20u);
    TEST_ASSERT(resp[0] == 0x62u);
    TEST_ASSERT(resp[1] == 0xF1u && resp[2] == 0x90u);
    TEST_ASSERT(memcmp(&resp[3], "LOTA0000000000001", 17u) == 0);

    /* unknown DID -> NRC 0x31 */
    req[1] = 0x12;
    req[2] = 0x34;
    TEST_ASSERT(call(3) == 1);
    TEST_ASSERT(rl == 3u);
    TEST_ASSERT(resp[0] == 0x7Fu && resp[1] == 0x22u && resp[2] == 0x31u);

    /* truncated DID request -> NRC 0x13 */
    TEST_ASSERT(call(2) == 1);
    TEST_ASSERT(resp[2] == 0x13u);

    /* 0x3E */
    req[0] = 0x3E;
    req[1] = 0x00;
    TEST_ASSERT(call(2) == 1);
    TEST_ASSERT(rl == 2u && resp[0] == 0x7Eu && resp[1] == 0x00u);

    /* 0x10 with invalid sub function -> NRC 0x12 */
    req[0] = 0x10;
    req[1] = 0x05;
    TEST_ASSERT(call(2) == 1);
    TEST_ASSERT(resp[2] == 0x12u);

    /* 0x10 extended session */
    req[1] = 0x03;
    TEST_ASSERT(call(2) == 1);
    TEST_ASSERT(resp[0] == 0x50u && resp[1] == 0x03u);
    TEST_ASSERT(rl == 6u);
    TEST_ASSERT(resp[2] == 0u && resp[3] == 0x32u && resp[4] == 1u && resp[5] == 0xF4u);
    TEST_ASSERT(B.server.session == UDS_SESSION_EXTENDED);

    /* 0x11 reset */
    req[0] = 0x11;
    req[1] = 0x01;
    TEST_ASSERT(call(2) == 1);
    TEST_ASSERT(resp[0] == 0x51u && resp[1] == 0x01u);
    TEST_ASSERT(B.server.reset_requested == 1u);
    B.server.reset_requested = 0u;

    /* 0x34 in extended session -> conditions not correct */
    memset(req, 0, sizeof req);
    req[0] = 0x34;
    req[1] = 0x00;
    req[2] = 0x44;
    TEST_ASSERT(call(11) == 1);
    TEST_ASSERT(resp[2] == 0x22u);

    /* programming session, still locked -> security denied */
    req[0] = 0x10;
    req[1] = 0x02;
    TEST_ASSERT(call(2) == 1);
    TEST_ASSERT(resp[0] == 0x50u && resp[1] == 0x02u);

    memset(req, 0, sizeof req);
    req[0] = 0x34;
    req[1] = 0x00;
    req[2] = 0x44;
    TEST_ASSERT(call(11) == 1);
    TEST_ASSERT(resp[2] == 0x33u);

    /* wrong key -> NRC 0x35 */
    wrong_key_once();
    TEST_ASSERT(resp[0] == 0x7Fu && resp[2] == 0x35u);

    /* third consecutive failure -> lockout, NRC 0x36 */
    wrong_key_once();
    TEST_ASSERT(resp[2] == 0x35u);
    wrong_key_once();
    TEST_ASSERT(resp[0] == 0x7Fu && resp[2] == 0x36u);

    /* still locked out */
    req[0] = 0x27;
    req[1] = 0x01;
    TEST_ASSERT(call(2) == 1);
    TEST_ASSERT(resp[2] == 0x36u);

    /* sendKey without requestSeed -> NRC 0x24 */
    bench_init(&B);
    req[0] = 0x27;
    req[1] = 0x02;
    t_put_be32(&req[2], 0u);
    TEST_ASSERT(call(6) == 1);
    TEST_ASSERT(resp[2] == 0x24u);

    /* unknown security sub function -> NRC 0x12 */
    req[1] = 0x03;
    TEST_ASSERT(call(2) == 1);
    TEST_ASSERT(resp[2] == 0x12u);

    /* correct key unlocks, then a new seed request returns zero */
    bench_init(&B);
    req[0] = 0x10;
    req[1] = 0x02;
    (void)call(2);
    req[0] = 0x27;
    req[1] = 0x01;
    TEST_ASSERT(call(2) == 1);
    seed = t_get_be32(&resp[2]);
    TEST_ASSERT(seed != 0u);
    req[1] = 0x02;
    t_put_be32(&req[2], uds_calc_key(seed));
    TEST_ASSERT(call(6) == 1);
    TEST_ASSERT(resp[0] == 0x67u && resp[1] == 0x02u);
    TEST_ASSERT(B.server.unlocked == 1u);

    req[1] = 0x01;
    TEST_ASSERT(call(2) == 1);
    TEST_ASSERT(t_get_be32(&resp[2]) == 0u);

    /* 0x36 without 0x34 -> request sequence error (now unlocked) */
    req[0] = 0x36;
    req[1] = 0x01;
    req[2] = 0xAA;
    TEST_ASSERT(call(3) == 1);
    TEST_ASSERT(resp[2] == 0x24u);

    /* reading the active session via DID 0xF186 */
    req[0] = 0x22;
    req[1] = 0xF1;
    req[2] = 0x86;
    TEST_ASSERT(call(3) == 1);
    TEST_ASSERT(rl == 4u);
    TEST_ASSERT(resp[3] == UDS_SESSION_PROGRAMMING);

    /* Project status DID is telemetry: no App CRC scan or write side effect. */
    req[0]=0x22u; req[1]=0x02u; req[2]=0u;
    TEST_ASSERT(call(3)==1 && rl==43u);
    TEST_ASSERT(resp[3]==1u && resp[4]==0u && resp[5]==UPDATE_IDLE);
    TEST_ASSERT(t_get_be32(resp+39)==FLASH_TOTAL_SIZE);
    {
        metadata_t m;
        uint32_t writes;
        memset(&m,0,sizeof m); m.state=UPDATE_READY;
        m.active_version=1u; m.min_version=1u; m.active_size=16u; m.active_crc=0x11223344u;
        m.pending_version=2u; m.pending_size=19u; m.pending_crc=0x55667788u;
        TEST_ASSERT(!metadata_save(&B.meta,&m)); writes=B.eeprom.write_ops;
        TEST_ASSERT(call(3)==1 && rl==43u);
        TEST_ASSERT(resp[4]==1u && resp[5]==UPDATE_READY && resp[6]==0u);
        TEST_ASSERT(t_get_be32(resp+7)==1u && t_get_be32(resp+11)==2u);
        TEST_ASSERT(t_get_be32(resp+15)==1u && t_get_be32(resp+19)==16u);
        TEST_ASSERT(t_get_be32(resp+23)==19u && t_get_be32(resp+27)==0x11223344u);
        TEST_ASSERT(t_get_be32(resp+31)==0x55667788u && t_get_be32(resp+35)==m.seq);
        TEST_ASSERT(B.eeprom.write_ops==writes);
        req[1]=0xF1u; req[2]=0x90u; req[3]=0x02u; req[4]=0u;
        TEST_ASSERT(call(5)==1 && rl==62u);
        TEST_ASSERT(resp[20]==2u && resp[21]==0u && resp[22]==1u);
        req[1]=2u; req[2]=0u; B.eeprom.read_fail_after=0;
        TEST_ASSERT(call(3)==1 && rl==3u && resp[2]==0x72u);
        B.eeprom.read_fail_after=-1;
        TEST_ASSERT(uds_handle(&B.server,req,3u,resp,42u)==3u && resp[2]==0x72u);
        TEST_ASSERT(B.eeprom.write_ops==writes);
    }
}
