#include "ota_stm32.h"
#include "ota/image.h"
static uint32_t le32(const uint8_t *p)
{ return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24); }
int ota_stm32_app_vectors(ota_stm32_ports_t *p,uint32_t *stack,uint32_t *entry)
{
    metadata_t m;
    uint32_t size,crc;
    uint8_t vectors[8];
    if(!p || !p->storage_ready || !stack || !entry || metadata_load(&p->store,&m)!=1)
        return -1;
    if(m.state==UPDATE_TRIAL && m.boot_attempts>0u) {
        size=m.pending_size; crc=m.pending_crc;
    } else if(m.state==UPDATE_IDLE || m.state==UPDATE_DOWNLOADING) {
        size=m.active_size; crc=m.active_crc;
    } else return -1;
    if(image_validate_app(p->internal,size,crc) ||
       p->internal.read(p->internal.user,APP_OFFSET,vectors,sizeof vectors)) return -1;
    *stack=le32(vectors); *entry=le32(vectors+4); return 0;
}
int ota_stm32_jump_to_app(ota_stm32_ports_t *p)
{
#if OTA_ENABLE_BOOT_HANDOFF
    uint32_t stack,entry;
    if(!ota_arch_can_handoff() || ota_stm32_app_vectors(p,&stack,&entry)) return -1;
    /* Quiesce must stop board IRQ sources, DMA and peripherals. A C-level
     * failure leaves us in the loader; do not relocate VTOR on failure. */
    if(ota_bsp_quiesce()) return -1;
    ota_arch_handoff(stack,entry); /* hardware implementation never returns */
#else
    (void)p;
#endif
    return -1;
}
