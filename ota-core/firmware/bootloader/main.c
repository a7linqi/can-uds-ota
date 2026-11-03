#include "ota_stm32.h"
static ota_stm32_ports_t storage;
static ota_stm32_diag_t diagnostics;
#if OTA_ENABLE_WATCHDOG
static watchdog_policy_t watchdog;
static int watchdog_started;
#endif
int ota_firmware_watchdog_service(void)
{
#if OTA_ENABLE_WATCHDOG
    if(!watchdog_started) return 0;
    if(watchdog.phase==WATCHDOG_FAILED) return -1;
    if(watchdog_policy_should_feed(&watchdog,ota_bsp_now_ms(),1) && ota_bsp_watchdog_reload()) {
        watchdog_policy_fail(&watchdog); return -1;
    }
#endif
    return 0;
}
int ota_firmware_service(void) { return ota_stm32_storage_service(&storage); }
/* Call from the board's CAN FIFO0 callback after HAL has read one frame. */
void ota_bootloader_can_rx(const can_frame_t *frame)
{ (void)ota_stm32_can_rx_isr(&diagnostics.can,frame); }
int main(void)
{
    ota_board_config_t config;
    uint32_t last;
    int logged_error=0;
    ota_arch_irq_enable();
    if(ota_bsp_init(&config) || !config.board_verified) {
        ota_bsp_log("BSP unconfigured: no erase, install or handoff");
        for(;;) ota_bsp_idle();
    }
#if OTA_ENABLE_WATCHDOG
    watchdog_policy_init(&watchdog,0,ota_bsp_now_ms(),0u);
    if(ota_bsp_watchdog_start()) {
        ota_bsp_log("IWDG start failed: no install or handoff");
        for(;;) ota_bsp_idle();
    }
    watchdog_started=1;
#endif
    ota_stm32_ports_init(&storage,&config);
#if OTA_ENABLE_BOOT_HANDOFF
    if(storage.storage_ready && !ota_bsp_force_loader()) {
        if(boot_select(&storage.boot,0)==BOOT_TARGET_APP)
            (void)ota_stm32_jump_to_app(&storage);
    }
#endif
    /* Bring-up first supports version reads even without storage drivers. */
    ota_stm32_diag_init(&diagnostics,&storage);
    if(ota_bsp_can_start()) {
        ota_bsp_log("CAN start failed; verify board configuration");
        for(;;) ota_bsp_idle();
    }
    ota_bsp_log("loader diagnostics: request 7E0, response 7E8");
    last=ota_bsp_now_ms();
    for(;;) {
        if(ota_firmware_service()) {
            ota_bsp_log("background service failed: awaiting watchdog/reset");
            for(;;) ota_bsp_idle();
        }
        if(ota_stm32_diag_poll(&diagnostics)<0 && !logged_error) {
            ota_bsp_log("diagnostic transport error; inspect CAN state");
            logged_error=1;
        }
        if((uint32_t)(ota_bsp_now_ms()-last)>=500u) {
            last=ota_bsp_now_ms(); ota_bsp_heartbeat();
        }
        ota_bsp_idle();
    }
}
