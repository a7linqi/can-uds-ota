#include "mock_board.h"
#include <string.h>
mock_board_t M;
void mock_board_reset(void)
{
    memset(&M,0,sizeof M); memset(M.internal,0xFF,sizeof M.internal);
    memset(M.nor,0xFF,sizeof M.nor); memset(M.eeprom,0xFF,sizeof M.eeprom);
    M.fail_sector=-1; M.fail_program=-1; M.hardware_idle=1;
    M.fail_can_after=-1;
}
ota_board_config_t mock_config(void)
{
    ota_board_config_t c;
    memset(&c,0,sizeof c); c.board_verified=1u; c.storage_verified=1u;
    c.detected_flash_bytes=FLASH_TOTAL_SIZE; c.nor_bytes=EXT_FLASH_TOTAL_SIZE;
    c.nor_page_bytes=256u; c.nor_erase_bytes=4096u;
    c.eeprom_bytes=256u; c.eeprom_page_bytes=8u; return c;
}
int ota_bsp_init(ota_board_config_t *c) { *c=mock_config(); return 0; }
uint32_t ota_bsp_now_ms(void) { return M.now; }
void ota_bsp_idle(void) { if(M.idle_hook) M.idle_hook(); }
void ota_bsp_log(const char *s) { (void)s; }
void ota_bsp_heartbeat(void) {}
int ota_bsp_force_loader(void) { return 1; }
int ota_bsp_app_healthy(void) { M.now+=M.health_delay_ms; return M.app_healthy; }
int ota_bsp_watchdog_start(void)
{
    M.watchdog_starts++; M.watchdog_last_feed_ms=M.now;
    return M.watchdog_start_fail?-1:0;
}
int ota_bsp_watchdog_reload(void)
{
    if(M.watchdog_reload_fail) return -1;
    M.watchdog_feeds++; M.watchdog_last_feed_ms=M.now; return 0;
}
#ifndef OTA_TEST_REAL_FIRMWARE
int ota_firmware_watchdog_service(void) { return 0; }
#endif
int ota_bsp_quiesce(void) { return M.quiesce; }
void ota_bsp_reset(void) { M.reset_count++; }
int ota_bsp_can_start(void) { return 0; }
int ota_bsp_can_try_tx(const can_frame_t *f)
{
    if(M.can_busy) return 1;
    if(M.fail_can_after>=0 && M.sent_count>=(uint32_t)M.fail_can_after) return -1;
    if(M.sent_count>=64u) return -1;
    M.sent_at[M.sent_count]=M.now; M.sent[M.sent_count++]=*f; return 0;
}
int ota_bsp_can_tx_idle(void) { return M.hardware_idle; }
int ota_bsp_iflash_read(uint32_t a,uint8_t *out,uint32_t n)
{
    if(a<FLASH_BASE_ADDR || a-FLASH_BASE_ADDR>FLASH_TOTAL_SIZE ||
       n>FLASH_TOTAL_SIZE-(a-FLASH_BASE_ADDR)) return -1;
    memcpy(out,M.internal+a-FLASH_BASE_ADDR,n); return 0;
}
int ota_bsp_iflash_unlock(void) { M.unlocks++; return 0; }
int ota_bsp_iflash_lock(void) { M.locks++; return M.fail_lock ? -1 : 0; }
int ota_bsp_iflash_erase_sector(uint32_t s)
{
    uint32_t off,n;
    M.sectors++;
    if((int)s==M.fail_sector) return -1;
    if(s<4u || s>11u) return -1;
    off=s==4u ? 0x10000u : (s-4u)*0x20000u;
    n=s==4u ? 0x10000u : 0x20000u;
    if(off+n>FLASH_TOTAL_SIZE) return -1;
    memset(M.internal+off,0xFF,n); return 0;
}
int ota_bsp_iflash_program_word(uint32_t a,uint32_t word)
{
    uint32_t i;
    if((int)M.programs++==M.fail_program) return -1;
    if(a<APP_BASE_ADDR || a+4u>FLASH_BASE_ADDR+FLASH_TOTAL_SIZE) return -1;
    for(i=0;i<4u;i++) M.internal[a-FLASH_BASE_ADDR+i]&=(uint8_t)(word>>(8u*i));
    return 0;
}
int ota_bsp_nor_read(uint32_t a,uint8_t *out,uint32_t n)
{
    if(a>EXT_FLASH_TOTAL_SIZE || n>EXT_FLASH_TOTAL_SIZE-a) return -1;
    M.now+=M.nor_read_delay_ms; memcpy(out,M.nor+a,n); return 0;
}
int ota_bsp_nor_erase(uint32_t a,uint32_t n)
{
    if(a>EXT_FLASH_TOTAL_SIZE || n>EXT_FLASH_TOTAL_SIZE-a) return -1;
    if(!M.nor_erases) M.first_erase_sent_count=M.sent_count;
    M.nor_erases++; M.now+=M.nor_erase_delay_ms; memset(M.nor+a,0xFF,n); return 0;
}
int ota_bsp_nor_page_program(uint32_t a,const uint8_t *data,uint32_t n)
{
    uint32_t i;
    if(a>EXT_FLASH_TOTAL_SIZE || n>EXT_FLASH_TOTAL_SIZE-a || n>256u-a%256u)
        return -1;
    if(M.nor_writes<16u) {
        M.page_offsets[M.nor_writes]=a; M.page_lengths[M.nor_writes]=n;
    }
    M.nor_writes++;
    for(i=0;i<n;i++) M.nor[a+i]&=data[i];
    return 0;
}
int ota_bsp_eeprom_read(uint32_t a,uint8_t *out,uint32_t n)
{
    if(a>256u || n>256u-a) return -1;
    M.now+=M.eeprom_delay_ms; memcpy(out,M.eeprom+a,n); return 0;
}
int ota_bsp_eeprom_page_write(uint32_t a,const uint8_t *data,uint32_t n)
{
    if(a>256u || n>256u-a || n>8u-a%8u) return -1;
    M.ee_writes++; M.now+=M.eeprom_delay_ms;
    if(!M.eeprom_ignore_writes) memcpy(M.eeprom+a,data,n);
    return 0;
}
uint32_t ota_arch_irq_save(void) { uint32_t v=M.irq_state; M.irq_state=1u; return v; }
void ota_arch_irq_restore(uint32_t v) { M.irq_state=v; }
void ota_arch_irq_enable(void) { M.irq_state=0u; }
void ota_arch_wait(void) {}
void ota_arch_reset(void) { M.reset_count++; }
int ota_arch_can_handoff(void) { return 1; }
void ota_arch_handoff(uint32_t s,uint32_t e)
{ M.handoff_count++; M.stack=s; M.entry=e; }
