#include "ota_stm32.h"
static int boundary(uint32_t off)
{
    if(off==0x10000u) return 4;
    if(off>=0x20000u && off<=FLASH_TOTAL_SIZE && off%0x20000u==0u)
        return (int)(off/0x20000u)+4;
    return -1;
}
static int read_internal(void *u,uint32_t off,uint8_t *out,uint32_t n)
{
    ota_stm32_ports_t *p=(ota_stm32_ports_t *)u;
    if(!p->storage_ready || !out || off>FLASH_TOTAL_SIZE || n>FLASH_TOTAL_SIZE-off)
        return -1;
    if(ota_stm32_storage_service(p) || ota_bsp_iflash_read(FLASH_BASE_ADDR+off,out,n)) return -1;
    return ota_stm32_storage_service(p);
}
static int erase_internal(void *u,uint32_t off,uint32_t n)
{
    ota_stm32_ports_t *p=(ota_stm32_ports_t *)u;
    int first,last,s,result=0,locked;
    if(!p->storage_ready || !n || off<APP_OFFSET ||
       off>FLASH_TOTAL_SIZE || n>FLASH_TOTAL_SIZE-off) return -1;
    first=boundary(off); last=boundary(off+n);
    if(first<4 || last<=first) return -1;
    if(ota_bsp_iflash_unlock()) return -1;
    for(s=first;s<last;s++) {
        if(ota_stm32_storage_service(p) || ota_bsp_iflash_erase_sector((uint32_t)s) ||
           ota_stm32_storage_service(p)) { result=-1; break; }
    }
    locked=ota_bsp_iflash_lock();
    return result || locked ? -1 : 0;
}
static int write_internal(void *u,uint32_t off,const uint8_t *data,uint32_t n)
{
    ota_stm32_ports_t *p=(ota_stm32_ports_t *)u;
    uint32_t i,word;
    uint8_t previous[4];
    int result=0,locked;
    if(!p->storage_ready || !data || !n || off<APP_OFFSET ||
       off>FLASH_TOTAL_SIZE || n>FLASH_TOTAL_SIZE-off || (off&3u) || (n&3u))
        return -1;
    if(ota_bsp_iflash_unlock()) return -1;
    for(i=0;i<n;i+=4u) {
        uint32_t k;
        if(ota_stm32_storage_service(p) || ota_bsp_iflash_read(FLASH_BASE_ADDR+off+i,previous,4u))
            { result=-1; break; }
        for(k=0;k<4u;k++) if((previous[k]&data[i+k])!=data[i+k]) result=-1;
        if(result) break;
        word=(uint32_t)data[i]|((uint32_t)data[i+1u]<<8)|
             ((uint32_t)data[i+2u]<<16)|((uint32_t)data[i+3u]<<24);
        if(ota_bsp_iflash_program_word(FLASH_BASE_ADDR+off+i,word) || ota_stm32_storage_service(p))
            { result=-1; break; }
    }
    locked=ota_bsp_iflash_lock();
    return result || locked ? -1 : 0;
}
flash_port_t ota_stm32_internal_port(ota_stm32_ports_t *p)
{
    flash_port_t f;
    f.read=read_internal; f.erase=erase_internal; f.write=write_internal; f.user=p;
    return f;
}
