#include "ota_stm32.h"
#include <string.h>
static int valid(const can_frame_t *f)
{ return f && f->dlc>0u && f->dlc<=8u && f->id<=(f->extended?0x1FFFFFFFu:0x7FFu); }
void ota_stm32_can_init(ota_stm32_can_t *c) { memset(c,0,sizeof *c); }
int ota_stm32_can_rx_isr(ota_stm32_can_t *c,const can_frame_t *f)
{
    uint32_t irq,next;
    if(!c || !valid(f)) return -1;
    irq=ota_arch_irq_save();
    next=(c->rx_head+1u)%OTA_RX_QUEUE;
    if(next==c->rx_tail) { c->rx_dropped++; ota_arch_irq_restore(irq); return -1; }
    c->rx[c->rx_head]=*f; c->rx_head=next;
    ota_arch_irq_restore(irq); return 0;
}
int ota_stm32_can_pop(ota_stm32_can_t *c,can_frame_t *f)
{
    uint32_t irq=ota_arch_irq_save();
    if(c->rx_head==c->rx_tail) { ota_arch_irq_restore(irq); return 0; }
    *f=c->rx[c->rx_tail]; c->rx_tail=(c->rx_tail+1u)%OTA_RX_QUEUE;
    ota_arch_irq_restore(irq); return 1;
}
static int send_frame(void *u,const can_frame_t *f)
{
    ota_stm32_can_t *c=(ota_stm32_can_t *)u;
    uint32_t next;
    if(!valid(f) || c->tx_error) return -1;
    next=(c->tx_head+1u)%OTA_TX_QUEUE;
    if(next==c->tx_tail) return -1;
    c->tx[c->tx_head]=*f; c->tx_head=next; return 0;
}
static uint32_t now(void *u) { (void)u; return ota_bsp_now_ms(); }
ota_port_t ota_stm32_can_port(ota_stm32_can_t *c)
{ ota_port_t p; p.can_send=send_frame; p.now_ms=now; p.user=c; return p; }
int ota_stm32_can_poll(ota_stm32_can_t *c)
{
    if(c->tx_error) return -1;
    if(c->tx_tail!=c->tx_head) {
        int r=ota_bsp_can_try_tx(&c->tx[c->tx_tail]);
        if(r<0) { c->tx_error=1; return -1; }
        if(r==0) c->tx_tail=(c->tx_tail+1u)%OTA_TX_QUEUE;
    }
    return 0;
}
int ota_stm32_can_idle(const ota_stm32_can_t *c)
{ return !c->tx_error && c->tx_head==c->tx_tail && ota_bsp_can_tx_idle()==1; }
