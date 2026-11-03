#ifndef MOCK_BOARD_H
#define MOCK_BOARD_H
#include "ota_stm32.h"
typedef struct {
    uint8_t internal[FLASH_TOTAL_SIZE],nor[EXT_FLASH_TOTAL_SIZE],eeprom[EEPROM_TOTAL_SIZE];
    uint32_t unlocks,locks,sectors,programs,nor_writes,nor_erases,ee_writes;
    uint32_t page_offsets[16],page_lengths[16];
    int fail_sector,fail_program,fail_lock,can_busy,hardware_idle,quiesce;
    uint32_t now,reset_count,irq_state,handoff_count,stack,entry;
    can_frame_t sent[64];
    uint32_t sent_count;
    uint32_t sent_at[64],nor_erase_delay_ms,nor_read_delay_ms,eeprom_delay_ms;
    uint32_t first_erase_sent_count;
    int fail_can_after;
    int app_healthy,watchdog_start_fail,watchdog_reload_fail,eeprom_ignore_writes;
    uint32_t watchdog_starts,watchdog_feeds,watchdog_last_feed_ms,health_delay_ms;
    void (*idle_hook)(void);
} mock_board_t;
extern mock_board_t M;
void mock_board_reset(void);
ota_board_config_t mock_config(void);
#endif
