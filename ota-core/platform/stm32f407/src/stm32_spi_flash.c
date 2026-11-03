#include "ota_stm32.h"
static int region(ota_stm32_ports_t *p,uint32_t off,uint32_t n)
{ return p->storage_ready && off<=EXT_FLASH_TOTAL_SIZE && n<=EXT_FLASH_TOTAL_SIZE-off; }
static int nor_read(void *u,uint32_t off,uint8_t *out,uint32_t n)
{
    ota_stm32_ports_t *p=(ota_stm32_ports_t *)u;
    if(!out || !region(p,off,n) || ota_stm32_storage_service(p)) return -1;
    if(ota_bsp_nor_read(off,out,n)) return -1;
    return ota_stm32_storage_service(p);
}
static int nor_write(void *u,uint32_t off,const uint8_t *data,uint32_t n)
{
    ota_stm32_ports_t *p=(ota_stm32_ports_t *)u;
    if(!data || !n || !region(p,off,n)) return -1;
    while(n) {
        uint32_t k=p->config.nor_page_bytes-off%p->config.nor_page_bytes;
        if(k>n) k=n;
        if(ota_stm32_storage_service(p) || ota_bsp_nor_page_program(off,data,k) ||
           ota_stm32_storage_service(p)) return -1;
        off+=k; data+=k; n-=k;
    }
    return 0;
}
static int nor_erase(void *u,uint32_t off,uint32_t n)
{
    ota_stm32_ports_t *p=(ota_stm32_ports_t *)u;
    if(!n || !region(p,off,n) || off%EXT_ERASE_SIZE || n%EXT_ERASE_SIZE) return -1;
    while(n) {
        if(ota_stm32_storage_service(p) || ota_bsp_nor_erase(off,EXT_ERASE_SIZE) ||
           ota_stm32_storage_service(p)) return -1;
        off+=EXT_ERASE_SIZE; n-=EXT_ERASE_SIZE;
    }
    return 0;
}
flash_port_t ota_stm32_nor_port(ota_stm32_ports_t *p)
{
    flash_port_t f;
    f.read=nor_read; f.write=nor_write; f.erase=nor_erase; f.user=p; return f;
}
