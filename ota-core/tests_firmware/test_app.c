/* Run the REAL application entry with deterministic BSP clock/watchdog hooks.
 * setjmp terminates the infinite firmware loop at reset/observation bounds. */
#include "mock_board.h"
#include "ota/crc32.h"
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
#define main ota_test_app_entry
#include "../firmware/app/main.c"
#undef main
static jmp_buf stop;
static int checks,failures,stop_reason;
static uint32_t origin,observation_ms;
static uint32_t lose_health_ms;
static int background_service;
#define CHECK(x) do {checks++;if(!(x)){failures++;printf("FAIL line %d: %s\n",__LINE__,#x);}} while(0)
static void idle(void)
{
    if(lose_health_ms && M.now>=lose_health_ms) M.app_healthy=0;
    if(background_service) {
        uint32_t feeds=M.watchdog_feeds;
        (void)ota_firmware_service();
        CHECK(M.watchdog_feeds==feeds);
    }
    /* A short model timeout replaces physical LSI; it is not a timing claim. */
    if((uint32_t)(M.now-M.watchdog_last_feed_ms)>=2000u) {
        stop_reason=1; longjmp(stop,1);
    }
    if((uint32_t)(M.now-origin)>=observation_ms) {
        stop_reason=2; longjmp(stop,1);
    }
    M.now+=500u;
}
static void put_le(uint8_t *p,uint32_t value)
{ p[0]=(uint8_t)value;p[1]=(uint8_t)(value>>8);p[2]=(uint8_t)(value>>16);p[3]=(uint8_t)(value>>24); }
static void fixture(uint32_t state)
{
    metadata_t m;
    ota_board_config_t config;
    mock_board_reset(); config=mock_config(); ota_stm32_ports_init(&storage,&config);
    memset(M.internal+APP_OFFSET,0xA5,16u);
    put_le(M.internal+APP_OFFSET,0x20020000u); put_le(M.internal+APP_OFFSET+4u,APP_BASE_ADDR+9u);
    memset(&m,0,sizeof m);m.state=state;
    if(state==UPDATE_TRIAL) {
        m.pending_version=1u;m.pending_size=16u;m.pending_crc=crc32_calc(M.internal+APP_OFFSET,16u);
        m.boot_attempts=1u;
    } else {
        m.active_version=m.min_version=1u;m.active_size=16u;
        m.active_crc=crc32_calc(M.internal+APP_OFFSET,16u);
    }
    CHECK(!metadata_save(&storage.store,&m));
    M.idle_hook=idle; origin=0u; observation_ms=6000u; stop_reason=0;
    lose_health_ms=0u; background_service=0;
}
static void execute(void)
{
    if(!setjmp(stop)) (void)ota_test_app_entry();
    CHECK(M.watchdog_starts==1u);
}
int main(void)
{
    metadata_t m;
    uint32_t writes;
    fixture(UPDATE_TRIAL); writes=M.ee_writes; background_service=1;
    execute();
    CHECK(stop_reason==1 && !M.watchdog_feeds && M.ee_writes==writes);
    CHECK(metadata_load(&storage.store,&m)==1 && m.state==UPDATE_TRIAL);

    fixture(UPDATE_TRIAL); M.app_healthy=1;
    execute();
    CHECK(stop_reason==2 && M.watchdog_feeds>=10u);
    CHECK(metadata_load(&storage.store,&m)==1 && m.state==UPDATE_IDLE && m.active_version==1u);

    fixture(UPDATE_DOWNLOADING); M.app_healthy=1; writes=M.ee_writes;
    execute();
    CHECK(stop_reason==2 && M.watchdog_feeds>=10u && M.ee_writes==writes);
    CHECK(metadata_load(&storage.store,&m)==1 && m.state==UPDATE_DOWNLOADING);

    fixture(UPDATE_TRIAL); M.app_healthy=1; M.eeprom_ignore_writes=1;
    execute();
    CHECK(stop_reason==1 && !M.watchdog_feeds);
    CHECK(metadata_load(&storage.store,&m)==1 && m.state==UPDATE_TRIAL);

    fixture(UPDATE_TRIAL); M.app_healthy=1; lose_health_ms=3000u;
    execute();
    CHECK(stop_reason==1 && M.watchdog_feeds>0u && M.now<6000u);
    CHECK(metadata_load(&storage.store,&m)==1 && m.state==UPDATE_IDLE);

    fixture(UPDATE_TRIAL); M.app_healthy=1; M.health_delay_ms=11000u; writes=M.ee_writes;
    execute();
    CHECK(stop_reason==1 && !M.watchdog_feeds && M.ee_writes==writes);
    CHECK(metadata_load(&storage.store,&m)==1 && m.state==UPDATE_TRIAL);

    fixture(UPDATE_IDLE); M.app_healthy=1; M.internal[APP_OFFSET+10u]^=1u;
    execute();
    CHECK(stop_reason==1 && !M.watchdog_feeds);

    fixture(UPDATE_TRIAL); M.app_healthy=1; M.watchdog_reload_fail=1;
    execute();
    CHECK(stop_reason==1 && !M.watchdog_feeds);

    printf("App entry watchdog model: %d checks, %d failures\n",checks,failures);
    return failures?1:0;
}
