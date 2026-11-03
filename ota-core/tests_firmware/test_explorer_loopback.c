/* Execute the real bring-up entry against the real CAN receive queue.
 * This verifies control flow and error exits, not HAL or physical hardware. */
#include "ota_stm32.h"
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
#define main ota_test_loopback_entry
#include "../firmware/explorer_loopback/main.c"
#undef main
static jmp_buf escape;
static uint32_t tick,tx_calls,accepted,idles;
static int scenario,busy_left,checks,failures,guard_fired,scenario_count;
enum { GOOD,INIT_FAIL,UNVERIFIED,START_FAIL,TX_FAIL,BUSY_ONCE,BUSY_FOREVER,
       LOST_RX,BAD_DATA,BAD_ID,BAD_EXTENDED,BAD_DLC,ZERO_DLC };
#define CHECK(x) do {checks++;if(!(x)){failures++;printf("FAIL line %d: %s\n",__LINE__,#x);}} while(0)
int ota_bsp_init(ota_board_config_t *config)
{
    memset(config,0,sizeof *config);config->board_verified=scenario!=UNVERIFIED;
    return scenario==INIT_FAIL?-1:0;
}
int ota_bsp_can_start(void) { return scenario==START_FAIL?-1:0; }
uint32_t ota_bsp_now_ms(void) { return tick++; }
int ota_bsp_can_try_tx(const can_frame_t *frame)
{
    can_frame_t back=*frame;tx_calls++;
    if(scenario==TX_FAIL) return -1;
    if(scenario==BUSY_FOREVER) return 1;
    if(busy_left) { busy_left--;return 1; }
    accepted++;
    if(scenario==LOST_RX) return 0;
    if(scenario==BAD_DATA) back.data[6]^=1u;
    if(scenario==BAD_ID) back.id++;
    if(scenario==BAD_EXTENDED) back.extended=1u;
    if(scenario==BAD_DLC) back.dlc=7u;
    if(scenario==ZERO_DLC) back.dlc=0u;
    ota_bootloader_can_rx(&back);return 0;
}
int ota_bsp_can_tx_idle(void) { return 1; }
void ota_bsp_idle(void)
{
    if(ota_explorer_loopback_stage==3u || ota_explorer_loopback_stage>=0xE1u)
        longjmp(escape,1);
    if(++idles>500u) { guard_fired=1;longjmp(escape,1); }
    tick++;
}
void ota_bsp_log(const char *s) { (void)s; }
void ota_bsp_heartbeat(void) {}
uint32_t ota_arch_irq_save(void) { return 0; }
void ota_arch_irq_restore(uint32_t state) { (void)state; }
void ota_arch_irq_enable(void) {}
static void execute(int selected,uint32_t expected,uint32_t count,uint32_t start)
{
    scenario_count++;
    scenario=selected;tick=start;tx_calls=accepted=idles=0u;guard_fired=0;
    busy_left=selected==BUSY_ONCE?2:0;
    ota_explorer_loopback_passed=ota_explorer_loopback_stage=0u;
    if(!setjmp(escape)) (void)ota_test_loopback_entry();
    CHECK(!guard_fired);CHECK(ota_explorer_loopback_stage==expected);
    CHECK(ota_explorer_loopback_passed==count);
}
int main(void)
{
    execute(GOOD,3u,16u,0u);CHECK(tx_calls==16u && accepted==16u);
    execute(GOOD,3u,16u,0xFFFFFFE0u);CHECK(tx_calls==16u && accepted==16u);
    execute(INIT_FAIL,0xE1u,0u,0u);CHECK(!tx_calls);
    execute(UNVERIFIED,0xE1u,0u,0u);CHECK(!tx_calls);
    execute(START_FAIL,0xE2u,0u,0u);CHECK(!tx_calls);
    execute(TX_FAIL,0xE3u,0u,0u);CHECK(tx_calls==1u && !accepted);
    execute(BUSY_ONCE,3u,16u,0u);CHECK(tx_calls==18u && accepted==16u);
    execute(BUSY_FOREVER,0xE4u,0u,0u);CHECK(!accepted);
    execute(LOST_RX,0xE5u,0u,0u);CHECK(accepted==1u);
    execute(BAD_DATA,0xE6u,0u,0u);
    execute(BAD_ID,0xE6u,0u,0u);
    execute(BAD_EXTENDED,0xE6u,0u,0u);
    execute(BAD_DLC,0xE6u,0u,0u);
    execute(ZERO_DLC,0xE5u,0u,0u);
    printf("Explorer loopback entry model: %d checks, %d failures; %d scenarios\n",checks,failures,scenario_count);
    return failures?1:0;
}
