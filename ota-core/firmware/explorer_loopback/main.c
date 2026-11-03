#include "ota_stm32.h"
#include <string.h>
#if !defined(OTA_EXPLORER_CAN_LOOPBACK) || OTA_EXPLORER_CAN_LOOPBACK != 1
#error "The loopback self-test must never run with physical CAN transmission enabled"
#endif
static ota_stm32_can_t received;
/* Debugger-visible progress. These values are set on real hardware only. */
volatile uint32_t ota_explorer_loopback_passed;
volatile uint32_t ota_explorer_loopback_stage;
int ota_firmware_service(void) { return 0; }
int ota_firmware_watchdog_service(void) { return 0; }
void ota_bootloader_can_rx(const can_frame_t *frame)
{ (void)ota_stm32_can_rx_isr(&received,frame); }
static void stop(uint32_t stage,const char *reason)
{
    ota_explorer_loopback_stage=stage;ota_bsp_log(reason);
    for(;;) ota_bsp_idle();
}
int main(void)
{
    ota_board_config_t config;
    uint32_t trial,last;
    ota_arch_irq_enable();
    ota_explorer_loopback_stage=1u;
    if(ota_bsp_init(&config) || !config.board_verified)
        stop(0xE1u,"loopback BSP initialization failed");
    ota_stm32_can_init(&received);
    if(ota_bsp_can_start()) stop(0xE2u,"loopback CAN start failed");
    ota_explorer_loopback_stage=2u;
    for(trial=0u;trial<16u;trial++) {
        can_frame_t sent,back;
        uint32_t i,start=ota_bsp_now_ms();int result;
        memset(&sent,0,sizeof sent);sent.id=0x7E0u;sent.dlc=8u;
        for(i=0u;i<8u;i++) sent.data[i]=(uint8_t)(trial*17u+i*29u);
        do {
            result=ota_bsp_can_try_tx(&sent);
            if(result<0) stop(0xE3u,"loopback transmit error");
            if(ota_bsp_now_ms()-start>=100u) stop(0xE4u,"loopback mailbox timeout");
        } while(result==1);
        start=ota_bsp_now_ms();
        while(ota_stm32_can_pop(&received,&back)==0) {
            if(ota_bsp_now_ms()-start>=100u) stop(0xE5u,"loopback receive timeout");
            ota_bsp_idle();
        }
        if(back.id!=sent.id || back.dlc!=sent.dlc || back.extended ||
           memcmp(back.data,sent.data,8u)) stop(0xE6u,"loopback frame mismatch");
        ota_explorer_loopback_passed=trial+1u;
    }
    ota_explorer_loopback_stage=3u;
    ota_bsp_log("16 internal loopback frames passed; physical CAN wiring untested");
    last=ota_bsp_now_ms();
    for(;;) {
        if(ota_bsp_now_ms()-last>=500u) { last=ota_bsp_now_ms();ota_bsp_heartbeat(); }
        ota_bsp_idle();
    }
}
