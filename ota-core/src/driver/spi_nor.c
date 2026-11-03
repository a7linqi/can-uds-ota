#include "ota/spi_nor.h"
#include <string.h>

static int fault(nor_spi_t *d,int error) { d->ready=0u; return error; }
static int service(nor_spi_t *d)
{
    return d->bus.service && d->bus.service(d->bus.user) ? fault(d,NOR_ERR_SERVICE) : 0;
}
static int transaction(nor_spi_t *d,const uint8_t *command,uint32_t command_size,
                       const uint8_t *tx,uint8_t *rx,uint32_t n)
{
    int result,closed;
    if(service(d)) return NOR_ERR_SERVICE;
    result=d->bus.select(d->bus.user,1);
    if(!result) result=d->bus.transfer(d->bus.user,command,0,command_size,d->limits.io_ms);
    if(!result && n) result=d->bus.transfer(d->bus.user,tx,rx,n,d->limits.io_ms);
    /* Even an assert/transfer failure must attempt to release /CS. */
    closed=d->bus.select(d->bus.user,0);
    if(result || closed) return fault(d,NOR_ERR_IO);
    return service(d);
}
static int status(nor_spi_t *d,uint8_t command,uint8_t *out)
{ return transaction(d,&command,1u,0,out,1u); }
static int wait_ready(nor_spi_t *d,uint32_t timeout)
{
    uint32_t start=d->bus.now_ms(d->bus.user),polls=0u;
    uint8_t sr;
    for(;;) {
        int result=status(d,0x05u,&sr);
        if(result) return result;
        if(!(sr&1u)) return 0;
        if((uint32_t)(d->bus.now_ms(d->bus.user)-start)>=timeout || polls++>=timeout)
            return fault(d,NOR_ERR_TIMEOUT);
        /* The poll-count bound also terminates if the BSP tick stops. */
        if(d->bus.wait_ms(d->bus.user,1u)) return fault(d,NOR_ERR_IO);
    }
}
static void address_command(uint8_t *command,uint8_t opcode,uint32_t address)
{
    command[0]=opcode; command[1]=(uint8_t)(address>>16);
    command[2]=(uint8_t)(address>>8); command[3]=(uint8_t)address;
}
static int valid(const nor_spi_t *d,uint32_t address,uint32_t n)
{ return d && d->ready && n && address<d->capacity_bytes && n<=d->capacity_bytes-address; }
static int enable_write(nor_spi_t *d)
{
    uint8_t sr1,sr2,sr3,command=0x06u;
    int result=wait_ready(d,d->limits.erase_ms);
    if(result) return result;
    if((result=status(d,0x05u,&sr1)) || (result=status(d,0x35u,&sr2)) ||
       (result=status(d,0x15u,&sr3))) return result;
    /* Never clear protection automatically. Refuse range protection,
     * complement protection, individual-lock mode, or suspended operations. */
    if((sr1&0x1Cu) || (sr2&0xC0u) || (sr3&4u)) return NOR_ERR_PROTECTED;
    result=transaction(d,&command,1u,0,0,0u);
    if(result) return result;
    result=status(d,0x05u,&sr1);
    if(result) return result;
    return sr1&2u ? 0 : fault(d,NOR_ERR_WEL);
}
static int completed(nor_spi_t *d,uint32_t timeout)
{
    uint8_t sr;
    int result=wait_ready(d,timeout);
    if(result) return result;
    result=status(d,0x05u,&sr);
    if(result) return result;
    return sr&2u ? fault(d,NOR_ERR_WEL) : 0;
}
int nor_spi_init(nor_spi_t *d,const nor_spi_bus_t *bus,const nor_spi_limits_t *limits)
{
    nor_spi_limits_t defaults={50u,20u,1000u,1000u};
    if(!d) return NOR_ERR_ARGUMENT;
    memset(d,0,sizeof *d);
    if(!bus || !bus->select || !bus->transfer || !bus->now_ms || !bus->wait_ms)
        return NOR_ERR_ARGUMENT;
    d->bus=*bus; d->limits=limits ? *limits : defaults;
    if(!d->limits.io_ms || !d->limits.program_ms || !d->limits.erase_ms ||
       !d->limits.probe_ms || d->limits.io_ms>1000u || d->limits.program_ms>60000u ||
       d->limits.erase_ms>60000u || d->limits.probe_ms>60000u) return NOR_ERR_ARGUMENT;
    d->configured=1u;
    return 0;
}
int nor_spi_probe(nor_spi_t *d)
{
    uint8_t id[3],command=0x9Fu,sr2;
    int result;
    if(!d || !d->configured) return NOR_ERR_ARGUMENT;
    d->ready=0u; d->jedec_id=0u; d->capacity_bytes=0u;
    if(d->bus.select(d->bus.user,0)) return fault(d,NOR_ERR_IO);
    result=wait_ready(d,d->limits.probe_ms);
    if(result) return result;
    result=transaction(d,&command,1u,0,id,3u);
    if(result) return result;
    d->jedec_id=((uint32_t)id[0]<<16)|((uint32_t)id[1]<<8)|id[2];
    if(d->jedec_id!=NOR_JEDEC_ID) return NOR_ERR_ID;
    result=status(d,0x35u,&sr2);
    if(result) return result;
    if(sr2&0x80u) return NOR_ERR_PROTECTED;
    d->capacity_bytes=NOR_CAPACITY_BYTES; d->ready=1u;
    return 0;
}
int nor_spi_read(nor_spi_t *d,uint32_t address,uint8_t *out,uint32_t n)
{
    uint8_t command[4];
    int result;
    if(!out || !valid(d,address,n)) return NOR_ERR_ARGUMENT;
    result=wait_ready(d,d->limits.erase_ms);
    if(result) return result;
    while(n) {
        uint32_t chunk=n>256u ? 256u : n;
        address_command(command,0x03u,address);
        result=transaction(d,command,4u,0,out,chunk);
        if(result) return result;
        address+=chunk; out+=chunk; n-=chunk;
    }
    return 0;
}
int nor_spi_program_page(nor_spi_t *d,uint32_t address,const uint8_t *data,uint32_t n)
{
    uint8_t command[4],previous[NOR_PAGE_BYTES];
    uint32_t i;
    int result;
    if(!data || !valid(d,address,n) || n>NOR_PAGE_BYTES-address%NOR_PAGE_BYTES)
        return NOR_ERR_ARGUMENT;
    result=nor_spi_read(d,address,previous,n);
    if(result) return result;
    for(i=0u;i<n;i++) if((previous[i]&data[i])!=data[i]) return NOR_ERR_ERASE_REQUIRED;
    if(!memcmp(previous,data,n)) return 0; /* already durable: avoid extra wear */
    result=enable_write(d);
    if(result) return result;
    address_command(command,0x02u,address);
    result=transaction(d,command,4u,data,0,n);
    if(result) return result;
    result=completed(d,d->limits.program_ms);
    if(result) return result;
    result=nor_spi_read(d,address,previous,n);
    if(result) return result;
    return memcmp(previous,data,n) ? fault(d,NOR_ERR_VERIFY) : 0;
}
int nor_spi_erase_sector(nor_spi_t *d,uint32_t address)
{
    uint8_t command[4],readback[256];
    uint32_t offset,i;
    int result;
    if(!valid(d,address,NOR_SECTOR_BYTES) || address%NOR_SECTOR_BYTES) return NOR_ERR_ARGUMENT;
    result=enable_write(d);
    if(result) return result;
    address_command(command,0x20u,address);
    result=transaction(d,command,4u,0,0,0u);
    if(result) return result;
    result=completed(d,d->limits.erase_ms);
    if(result) return result;
    for(offset=0u;offset<NOR_SECTOR_BYTES;offset+=sizeof readback) {
        result=nor_spi_read(d,address+offset,readback,sizeof readback);
        if(result) return result;
        for(i=0u;i<sizeof readback;i++) if(readback[i]!=0xFFu) return fault(d,NOR_ERR_VERIFY);
    }
    return 0;
}
