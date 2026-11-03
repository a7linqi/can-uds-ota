#include "ota_stm32.h"
static int eeprom_read(void *u,uint32_t off,uint8_t *out,uint32_t n)
{
    ota_stm32_ports_t *p=(ota_stm32_ports_t *)u;
    if(!p->storage_ready || !out || off>EEPROM_TOTAL_SIZE || n>EEPROM_TOTAL_SIZE-off)
        return -1;
    if(ota_stm32_storage_service(p) || ota_bsp_eeprom_read(off,out,n)) return -1;
    return ota_stm32_storage_service(p);
}
static int eeprom_write(void *u,uint32_t off,const uint8_t *data,uint32_t n)
{
    ota_stm32_ports_t *p=(ota_stm32_ports_t *)u;
    if(!p->storage_ready || !data || !n || off>EEPROM_TOTAL_SIZE ||
       n>EEPROM_TOTAL_SIZE-off || n>EEPROM_PAGE_SIZE-off%EEPROM_PAGE_SIZE) return -1;
    if(ota_stm32_storage_service(p) || ota_bsp_eeprom_page_write(off,data,n)) return -1;
    return ota_stm32_storage_service(p);
}
eeprom_port_t ota_stm32_eeprom_port(ota_stm32_ports_t *p)
{
    eeprom_port_t f;
    f.read=eeprom_read; f.write=eeprom_write; f.user=p; return f;
}
