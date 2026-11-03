#include "ota/image.h"
#include "ota/crc32.h"
#include "ota/signature.h"
#include <string.h>

static uint32_t get_le(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1]<<8)
         | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}
static void put_le(uint8_t *p,uint32_t v)
{
    p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8);
    p[2]=(uint8_t)(v>>16); p[3]=(uint8_t)(v>>24);
}
void image_header_encode(const image_header_t *h,uint8_t raw[IMAGE_HEADER_SIZE])
{
    put_le(raw,h->magic); put_le(raw+4,h->version);
    put_le(raw+8,h->image_size); put_le(raw+12,h->payload_crc);
    put_le(raw+16,h->load_address); put_le(raw+20,h->flags);
    put_le(raw+24,h->reserved); put_le(raw+28,h->header_crc);
}
void image_header_decode(image_header_t *h,const uint8_t raw[IMAGE_HEADER_SIZE])
{
    h->magic=get_le(raw); h->version=get_le(raw+4);
    h->image_size=get_le(raw+8); h->payload_crc=get_le(raw+12);
    h->load_address=get_le(raw+16); h->flags=get_le(raw+20);
    h->reserved=get_le(raw+24); h->header_crc=get_le(raw+28);
}
void image_header_finalize(image_header_t *h)
{
    uint8_t raw[IMAGE_HEADER_SIZE];
    h->header_crc=0u;
    image_header_encode(h,raw);
    h->header_crc=crc32_calc(raw,28u);
}
int image_header_ok(const image_header_t *h)
{
    uint8_t raw[IMAGE_HEADER_SIZE];
    if(h->magic!=IMAGE_MAGIC || h->version==0u || h->image_size<8u ||
       h->image_size>IMAGE_MAX_PAYLOAD || h->load_address!=APP_BASE_ADDR ||
       (h->flags & ~IMAGE_FLAG_SIGNED)!=0u || h->reserved!=0u) return 0;
    image_header_encode(h,raw);
    return h->header_crc==crc32_calc(raw,28u);
}
int image_read_header(flash_port_t p,uint32_t offset,image_header_t *h)
{
    uint8_t raw[IMAGE_HEADER_SIZE];
    if(!h || !p.read || (offset!=DOWNLOAD_OFFSET && offset!=BACKUP_OFFSET))
        return -1;
    if(p.read(p.user,offset,raw,sizeof raw)) return -1;
    image_header_decode(h,raw);
    return image_header_ok(h) ? 0 : -1;
}
/* F407 main SRAM (128 KiB) and CCM (64 KiB); stack top may equal RAM end.
 * This is a range/alignment check, not proof that the payload will run. */
static int vectors_ok(const uint8_t *v,uint32_t size)
{
    uint32_t sp=get_le(v),reset=get_le(v+4),pc=reset&~1u;
    int ram=(sp>0x20000000u && sp<=0x20020000u) ||
            (sp>0x10000000u && sp<=0x10010000u);
    return ram && (sp&7u)==0u && (reset&1u)!=0u &&
           pc>=APP_BASE_ADDR+8u && pc-APP_BASE_ADDR<size;
}
static int validate_payload(flash_port_t p,uint32_t offset,uint32_t size,uint32_t expected)
{
    uint8_t chunk[256];
    uint32_t off=0u,crc=0u;
    if(!p.read || size<8u || size>APP_SIZE) return -1;
    while(off<size) {
        uint32_t n=size-off;
        if(n>sizeof chunk) n=sizeof chunk;
        if(p.read(p.user,offset+off,chunk,n)) return -1;
        if(off==0u && !vectors_ok(chunk,size)) return -1;
        crc=crc32_update(crc,chunk,n); off+=n;
    }
    return crc==expected ? 0 : -1;
}
int image_validate(flash_port_t p,uint32_t offset,uint32_t *version,uint32_t *size)
{
    image_header_t h;
    uint8_t signature[OTA_SIGNATURE_SIZE];
    if(image_read_header(p,offset,&h) ||
       validate_payload(p,offset+IMAGE_HEADER_SIZE,h.image_size,h.payload_crc))
        return -1;
    if(h.flags & IMAGE_FLAG_SIGNED) {
        if(p.read(p.user,offset+IMAGE_HEADER_SIZE+h.image_size,
                  signature,sizeof signature))
            return -1;
        if(image_signature_verify(p,offset,h.image_size,signature))
            return -1;
    } else if(OTA_REQUIRE_SIGNATURE) {
        return -1; /* unsigned image rejected when signatures are required */
    }
    if(version) *version=h.version;
    if(size) *size=h.image_size;
    return 0;
}
int image_validate_app(flash_port_t p,uint32_t size,uint32_t crc)
{ return validate_payload(p,APP_OFFSET,size,crc); }

int image_validate_backup(flash_port_t p,uint32_t offset,uint32_t *version,uint32_t *size)
{
    image_header_t h;
    if(offset!=BACKUP_OFFSET || image_read_header(p,offset,&h) || h.flags!=0u ||
       validate_payload(p,offset+IMAGE_HEADER_SIZE,h.image_size,h.payload_crc))
        return -1;
    if(version) *version=h.version;
    if(size) *size=h.image_size;
    return 0;
}
