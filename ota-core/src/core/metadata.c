#include "ota/metadata.h"
#include "ota/crc32.h"
#include <string.h>

static const uint32_t offsets[2] = { META_A_OFFSET, META_B_OFFSET };
typedef char metadata_size_check[(sizeof(metadata_t) == META_RECORD_SIZE) ? 1 : -1];

static uint32_t get_le(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8)
         | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static void put_le(uint8_t *p, uint32_t v)
{
    p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8);
    p[2]=(uint8_t)(v>>16); p[3]=(uint8_t)(v>>24);
}
static void encode(const metadata_t *m, uint8_t *raw)
{
    const uint32_t words[16] = {
        m->magic,m->seq,m->state,m->active_version,m->active_size,m->active_crc,
        m->pending_version,m->pending_size,m->pending_crc,m->boot_attempts,
        m->min_version,m->reserved[0],m->reserved[1],m->reserved[2],m->crc,m->commit
    };
    uint32_t i;
    for(i=0;i<16u;i++) put_le(raw+4u*i,words[i]);
}
static void decode(metadata_t *m, const uint8_t *raw)
{
    m->magic=get_le(raw); m->seq=get_le(raw+4); m->state=get_le(raw+8);
    m->active_version=get_le(raw+12); m->active_size=get_le(raw+16);
    m->active_crc=get_le(raw+20); m->pending_version=get_le(raw+24);
    m->pending_size=get_le(raw+28); m->pending_crc=get_le(raw+32);
    m->boot_attempts=get_le(raw+36); m->min_version=get_le(raw+40);
    m->reserved[0]=get_le(raw+44); m->reserved[1]=get_le(raw+48);
    m->reserved[2]=get_le(raw+52); m->crc=get_le(raw+56);
    m->commit=get_le(raw+60);
}
static int sane(const metadata_t *m)
{
    if(m->state>UPDATE_RESTORING || m->active_size>APP_SIZE ||
       m->pending_size>APP_SIZE || m->boot_attempts>MAX_BOOT_ATTEMPTS)
        return 0;
    if(m->reserved[0] || m->reserved[1] || m->reserved[2]) return 0;
    if(m->active_size==0u) {
        if(m->active_version || m->active_crc || m->min_version) return 0;
    } else if(m->active_size<8u || m->active_version==0u ||
              m->min_version!=m->active_version) return 0;
    if(m->state==UPDATE_IDLE || m->state==UPDATE_DOWNLOADING)
        return m->pending_version==0u && m->pending_size==0u &&
               m->pending_crc==0u && m->boot_attempts==0u;
    if(m->pending_size<8u || m->pending_version<=m->min_version ||
       m->pending_version<=m->active_version) return 0;
    if(m->state==UPDATE_RESTORING && m->active_size==0u) return 0;
    return 1;
}
void metadata_store_init(metadata_store_t *s, eeprom_port_t eeprom)
{ s->eeprom=eeprom; }

/* 0 invalid, 1 valid, -1 unreadable. */
static int read_copy(metadata_store_t *s, uint32_t i, metadata_t *out)
{
    uint8_t raw[META_RECORD_SIZE];
    if(s->eeprom.read(s->eeprom.user,offsets[i],raw,sizeof raw)) return -1;
    decode(out,raw);
    if(out->magic!=META_MAGIC || out->commit!=META_COMMITTED ||
       out->crc!=crc32_calc(raw,56u) || !sane(out)) return 0;
    return 1;
}
int metadata_load_ex(metadata_store_t *s, metadata_t *out, int *copy_index)
{
    metadata_t a,b;
    int ra,rb,index;
    uint32_t delta;
    if(!s || !out || !s->eeprom.read) return -1;
    ra=read_copy(s,0u,&a); rb=read_copy(s,1u,&b);
    if(ra<0 || rb<0) return -1;
    if(!ra && !rb) return 0;
    if(ra && rb) {
        delta=a.seq-b.seq;
        if(delta==0u) {
            if(memcmp(&a,&b,sizeof a)!=0) return -1;
            index=0;
        } else if(delta==0x80000000u) return -1;
        else index=(delta<0x80000000u) ? 0 : 1;
    } else index=ra ? 0 : 1;
    *out=index ? b : a;
    if(copy_index) *copy_index=index;
    return 1;
}
int metadata_load(metadata_store_t *s, metadata_t *out)
{ return metadata_load_ex(s,out,0); }
static int write_bytes(eeprom_port_t *p,uint32_t off,const uint8_t *data,uint32_t n)
{
    while(n) {
        uint32_t k=EEPROM_PAGE_SIZE-(off%EEPROM_PAGE_SIZE);
        if(k>n) k=n;
        if(p->write(p->user,off,data,k)) return -1;
        off+=k; data+=k; n-=k;
    }
    return 0;
}
int metadata_save(metadata_store_t *s, metadata_t *m)
{
    metadata_t current,next;
    uint8_t raw[META_RECORD_SIZE],verify[META_RECORD_SIZE],invalid[4]={0,0,0,0};
    int index=0,have;
    uint32_t target;
    if(!s || !m || !s->eeprom.write || !s->eeprom.read || !sane(m)) return -1;
    have=metadata_load_ex(s,&current,&index);
    if(have<0) return -1;
    target=offsets[have ? (1-index) : 0];
    next=*m; next.magic=META_MAGIC; next.seq=have ? current.seq+1u : 1u;
    memset(next.reserved,0,sizeof next.reserved);
    next.commit=META_COMMITTED; next.crc=0u;
    encode(&next,raw); next.crc=crc32_calc(raw,56u); encode(&next,raw);
    if(write_bytes(&s->eeprom,target+60u,invalid,4u) ||
       s->eeprom.read(s->eeprom.user,target+60u,verify,4u) ||
       memcmp(verify,invalid,4u)!=0) return -1;
    if(write_bytes(&s->eeprom,target,raw,60u) ||
       write_bytes(&s->eeprom,target+60u,raw+60u,4u) ||
       s->eeprom.read(s->eeprom.user,target,verify,sizeof verify) ||
       memcmp(raw,verify,sizeof raw)!=0) return -1;
    *m=next;
    return 0;
}
