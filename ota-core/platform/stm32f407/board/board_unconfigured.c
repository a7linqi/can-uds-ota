#include "ota_stm32.h"
#include <string.h>
/* This file deliberately contains NO chosen pins, clock or chip profile.
 * Replace it with a real board binding. Never link both board bindings. */
int ota_bsp_init(ota_board_config_t *c) { memset(c,0,sizeof *c); return -1; }
uint32_t ota_bsp_now_ms(void) { return 0u; }
void ota_bsp_idle(void) { ota_arch_wait(); }
void ota_bsp_log(const char *s) { (void)s; }
void ota_bsp_heartbeat(void) {}
int ota_bsp_force_loader(void) { return 1; }
int ota_bsp_app_healthy(void) { return 0; }
int ota_bsp_watchdog_start(void) { return -1; }
int ota_bsp_watchdog_reload(void) { return -1; }
int ota_bsp_quiesce(void) { return -1; }
void ota_bsp_reset(void) { ota_arch_reset(); }
int ota_bsp_can_try_tx(const can_frame_t *f) { (void)f; return -1; }
int ota_bsp_can_start(void) { return -1; }
int ota_bsp_can_tx_idle(void) { return 0; }
int ota_bsp_iflash_read(uint32_t a,uint8_t *p,uint32_t n) { (void)a;(void)p;(void)n;return -1; }
int ota_bsp_iflash_unlock(void) { return -1; }
int ota_bsp_iflash_lock(void) { return -1; }
int ota_bsp_iflash_erase_sector(uint32_t s) { (void)s;return -1; }
int ota_bsp_iflash_program_word(uint32_t a,uint32_t w) { (void)a;(void)w;return -1; }
int ota_bsp_nor_read(uint32_t a,uint8_t *p,uint32_t n) { (void)a;(void)p;(void)n;return -1; }
int ota_bsp_nor_erase(uint32_t a,uint32_t n) { (void)a;(void)n;return -1; }
int ota_bsp_nor_page_program(uint32_t a,const uint8_t *p,uint32_t n) { (void)a;(void)p;(void)n;return -1; }
int ota_bsp_eeprom_read(uint32_t a,uint8_t *p,uint32_t n) { (void)a;(void)p;(void)n;return -1; }
int ota_bsp_eeprom_page_write(uint32_t a,const uint8_t *p,uint32_t n) { (void)a;(void)p;(void)n;return -1; }
