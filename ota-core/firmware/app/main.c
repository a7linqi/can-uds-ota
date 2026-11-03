#include "ota_stm32.h"
static ota_stm32_ports_t storage;
/* App storage/ACK callbacks must not keep an unhealthy App alive. Only the
 * main-loop health decision below is allowed to refresh the watchdog. */
int ota_firmware_watchdog_service(void) { return 0; }
int ota_firmware_service(void) { return ota_stm32_storage_service(&storage); }
int main(void)
{
    ota_board_config_t config;
    uint32_t last;
    int confirmed=0;
#if OTA_ENABLE_WATCHDOG
    watchdog_policy_t guard;
#endif
    /* ST startup initialized data/BSS and SystemInit set VTOR to __Vectors.
     * The loader deliberately handed over with interrupts still masked. */
    ota_arch_irq_enable();
    if(ota_bsp_init(&config) || !config.board_verified) {
        for(;;) ota_bsp_idle();
    }
#if OTA_ENABLE_WATCHDOG
    if(ota_bsp_watchdog_start()) for(;;) ota_bsp_idle();
    watchdog_policy_init(&guard,1,ota_bsp_now_ms(),OTA_APP_HEALTH_DEADLINE_MS);
#endif
    ota_stm32_ports_init(&storage,&config);
    ota_bsp_log("app at 08010000: awaiting real health checks");
    last=ota_bsp_now_ms();
    for(;;) {
        if((uint32_t)(ota_bsp_now_ms()-last)>=500u) {
            last=ota_bsp_now_ms(); ota_bsp_heartbeat();
#if OTA_ENABLE_WATCHDOG
            if(guard.phase!=WATCHDOG_FAILED &&
               (confirmed || watchdog_policy_may_confirm(&guard,ota_bsp_now_ms()))) {
                int healthy=ota_bsp_app_healthy();
                if(!confirmed && healthy && storage.storage_ready &&
                   watchdog_policy_may_confirm(&guard,ota_bsp_now_ms()) &&
                   boot_accept_app_health(&storage.boot)==0 && watchdog_policy_confirmed(&guard)==0)
                    confirmed=1;
                if(watchdog_policy_should_feed(&guard,ota_bsp_now_ms(),healthy) && ota_bsp_watchdog_reload())
                    watchdog_policy_fail(&guard);
            }
#else
            if(!confirmed && storage.storage_ready && ota_bsp_app_healthy() &&
               boot_accept_app_health(&storage.boot)==0) confirmed=1;
#endif
        }
        ota_bsp_idle();
    }
}
