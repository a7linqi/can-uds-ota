#include "ota_stm32.h"
#include <string.h>
int ota_stm32_storage_service(ota_stm32_ports_t *p)
{
#if OTA_ENABLE_WATCHDOG
    if(ota_firmware_watchdog_service()) return -1;
#endif
    return p && p->service ? p->service(p->service_user) : 0;
}
void ota_stm32_ports_init(ota_stm32_ports_t *p,const ota_board_config_t *cfg)
{
    memset(p,0,sizeof *p);
    if(cfg) p->config=*cfg;
    p->storage_ready=(uint8_t)(cfg && cfg->board_verified && cfg->storage_verified &&
        cfg->detected_flash_bytes==FLASH_TOTAL_SIZE &&
        cfg->nor_bytes>=EXT_FLASH_TOTAL_SIZE &&
        cfg->nor_page_bytes>0u && cfg->nor_page_bytes<=256u &&
        cfg->nor_erase_bytes==EXT_ERASE_SIZE &&
        cfg->nor_erase_bytes%cfg->nor_page_bytes==0u &&
        cfg->eeprom_bytes==EEPROM_TOTAL_SIZE &&
        cfg->eeprom_page_bytes==EEPROM_PAGE_SIZE);
    p->internal=ota_stm32_internal_port(p);
    p->external=ota_stm32_nor_port(p);
    p->eeprom=ota_stm32_eeprom_port(p);
    metadata_store_init(&p->store,p->eeprom);
    boot_context_init(&p->boot,p->internal,p->external,&p->store);
}
