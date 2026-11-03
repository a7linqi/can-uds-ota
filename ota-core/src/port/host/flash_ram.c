#include "flash_ram.h"
#include <string.h>

void storage_fault_reset(storage_fault_t *f)
{ memset(f,0,sizeof *f); f->remaining=-1; }

static void event(storage_fault_t *f,uint32_t len,uint8_t device,uint8_t erase)
{
    if(f->event_count<FAULT_EVENT_MAX) {
        fault_event_t *e=&f->events[f->event_count++];
        e->start=f->bytes; e->len=len; e->device=device; e->erase=erase;
    }
}
static int mutate(storage_fault_t *f)
{
    if(f->powered_off) return -1;
    if(f->remaining==0) { f->powered_off=1; return -1; }
    if(f->remaining>0) f->remaining--;
    f->bytes++;
    return 0;
}
static int readable(storage_fault_t *f,int *count)
{
    if(f->powered_off || *count==0) return 0;
    if(*count>0) (*count)--;
    return 1;
}
void flash_ram_attach(flash_ram_t *f,uint8_t *mem,uint32_t size,int external,storage_fault_t *fault)
{
    memset(f,0,sizeof *f); f->mem=mem; f->size=size; f->external=external;
    f->fault=fault; f->read_fail_after=-1;
}
static int in_range(uint32_t size,uint32_t off,uint32_t n)
{ return off<=size && n<=size-off; }
static int boundary(uint32_t offset)
{
    return offset==0u || offset==0x4000u || offset==0x8000u ||
           offset==0xC000u || offset==0x10000u ||
           (offset>=0x20000u && offset%0x20000u==0u);
}
static int ram_read(void *u,uint32_t off,uint8_t *out,uint32_t n)
{
    flash_ram_t *f=(flash_ram_t *)u;
    if(!f || !out || !in_range(f->size,off,n) || !readable(f->fault,&f->read_fail_after))
        return -1;
    memcpy(out,f->mem+off,n); return 0;
}
static int ram_write(void *u,uint32_t off,const uint8_t *data,uint32_t n)
{
    flash_ram_t *f=(flash_ram_t *)u;
    uint32_t i;
    if(!f || !data || !in_range(f->size,off,n) || f->fault->powered_off) return -1;
    if(!f->external && (off<APP_OFFSET || (off&3u) || (n&3u))) return -1;
    for(i=0;i<n;i++) if((f->mem[off+i]&data[i])!=data[i]) return -1;
    f->write_ops++; event(f->fault,n,(uint8_t)f->external,0u);
    for(i=0;i<n;i++) {
        if(mutate(f->fault)) return -1;
        if(!f->ignore_writes) f->mem[off+i]&=data[i];
    }
    return 0;
}
static int ram_erase(void *u,uint32_t off,uint32_t n)
{
    flash_ram_t *f=(flash_ram_t *)u;
    uint32_t i;
    if(!f || !n || !in_range(f->size,off,n) || f->fault->powered_off) return -1;
    if(f->external) {
        if(off%EXT_ERASE_SIZE || n%EXT_ERASE_SIZE) return -1;
    } else if(off<APP_OFFSET || !boundary(off) || !boundary(off+n)) return -1;
    f->erase_ops++; event(f->fault,n,(uint8_t)f->external,1u);
    for(i=0;i<n;i++) { if(mutate(f->fault)) return -1; f->mem[off+i]=0xFFu; }
    return 0;
}
flash_port_t flash_ram_port(flash_ram_t *f)
{
    flash_port_t p;
    p.erase=ram_erase; p.write=ram_write; p.read=ram_read; p.user=f; return p;
}
static int eeprom_read(void *u,uint32_t off,uint8_t *out,uint32_t n)
{
    eeprom_ram_t *f=(eeprom_ram_t *)u;
    if(!out || !in_range(EEPROM_TOTAL_SIZE,off,n) || !readable(f->fault,&f->read_fail_after))
        return -1;
    memcpy(out,f->mem+off,n); return 0;
}
static int eeprom_write(void *u,uint32_t off,const uint8_t *data,uint32_t n)
{
    eeprom_ram_t *f=(eeprom_ram_t *)u;
    uint32_t i;
    if(!data || !n || !in_range(EEPROM_TOTAL_SIZE,off,n) || f->fault->powered_off ||
       n>EEPROM_PAGE_SIZE-off%EEPROM_PAGE_SIZE) return -1;
    f->write_ops++; event(f->fault,n,2u,0u);
    for(i=0;i<n;i++) {
        if(mutate(f->fault)) return -1;
        if(!f->ignore_writes) f->mem[off+i]=data[i];
    }
    return 0;
}
eeprom_port_t eeprom_ram_port(eeprom_ram_t *f)
{
    eeprom_port_t p; p.read=eeprom_read; p.write=eeprom_write; p.user=f; return p;
}
