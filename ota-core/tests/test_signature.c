#include "test_util.h"
#include "ota/image.h"
#include "ota/signature.h"
#include "ota/crc32.h"
#include "ed25519.h"
#include <stdio.h>
#include <string.h>

#define SIGNED_TEST_PAYLOAD 513u
static uint8_t bytes[IMAGE_HEADER_SIZE+SIGNED_TEST_PAYLOAD+OTA_SIGNATURE_SIZE];
static uint8_t pub[32],priv[64];
static uint32_t base,reads;
static int fail_at;

static int read_bytes(void *user,uint32_t offset,uint8_t *out,uint32_t n)
{
    uint32_t call=reads++;
    (void)user;
    if(fail_at>=0 && call==(uint32_t)fail_at) return -1;
    if(offset<base || offset-base>sizeof bytes || n>sizeof bytes-(offset-base))
        return -1;
    memcpy(out,bytes+offset-base,n); return 0;
}
static void put_le(uint8_t *out,uint32_t v)
{
    out[0]=(uint8_t)v;out[1]=(uint8_t)(v>>8);
    out[2]=(uint8_t)(v>>16);out[3]=(uint8_t)(v>>24);
}
static flash_port_t make_signed(unsigned seed_start)
{
    uint8_t seed[32];
    image_header_t h;
    flash_port_t port;
    uint32_t i;
    memset(&port,0,sizeof port); port.read=read_bytes;
    for(i=0u;i<sizeof seed;i++) seed[i]=(uint8_t)(seed_start+i);
    ed25519_create_keypair(pub,priv,seed);
    memset(bytes,0xA5,sizeof bytes);
    put_le(bytes+IMAGE_HEADER_SIZE,0x20020000u);
    put_le(bytes+IMAGE_HEADER_SIZE+4u,APP_BASE_ADDR+9u);
    memset(&h,0,sizeof h);h.magic=IMAGE_MAGIC;h.version=7u;
    h.image_size=SIGNED_TEST_PAYLOAD;h.load_address=APP_BASE_ADDR;
    h.flags=IMAGE_FLAG_SIGNED;
    h.payload_crc=crc32_calc(bytes+IMAGE_HEADER_SIZE,SIGNED_TEST_PAYLOAD);
    image_header_finalize(&h);image_header_encode(&h,bytes);
    ed25519_sign(bytes+IMAGE_HEADER_SIZE+SIGNED_TEST_PAYLOAD,bytes,
                 IMAGE_HEADER_SIZE+SIGNED_TEST_PAYLOAD,pub,priv);
    base=DOWNLOAD_OFFSET;reads=0u;fail_at=-1;
    return port;
}
void run_signature_tests(void)
{
    static const uint8_t order[32]={
        0xED,0xD3,0xF5,0x5C,0x1A,0x63,0x12,0x58,
        0xD6,0x9C,0xF7,0xA2,0xDE,0xF9,0xDE,0x14,
        0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0x10
    };
    flash_port_t port=make_signed(0u),absent;
    image_header_t h;
    uint8_t *signature=bytes+IMAGE_HEADER_SIZE+SIGNED_TEST_PAYLOAD;
    uint32_t version=0u,size=0u,good_reads,i,carry=0u;
    puts("[signed image validation]");
    TEST_ASSERT(image_validate(port,DOWNLOAD_OFFSET,&version,&size)==0);
    TEST_ASSERT(version==7u && size==SIGNED_TEST_PAYLOAD);
    good_reads=reads;
    /* Every read in the streamed validation must propagate storage failure. */
    for(i=0u;i<good_reads;i++) {
        reads=0u;fail_at=(int)i;
        TEST_ASSERT(image_validate(port,DOWNLOAD_OFFSET,0,0)!=0);
    }
    fail_at=-1;

    /* S+L leaves the group equation unchanged but is forbidden by RFC 8032. */
    port=make_signed(0u);
    for(i=0u;i<32u;i++) {
        uint32_t value=(uint32_t)signature[32u+i]+order[i]+carry;
        signature[32u+i]=(uint8_t)value;carry=value>>8;
    }
    TEST_ASSERT(image_validate(port,DOWNLOAD_OFFSET,0,0)!=0);
    TEST_ASSERT(image_signature_verify(port,DOWNLOAD_OFFSET,SIGNED_TEST_PAYLOAD,signature)!=0);

    port=make_signed(0u);signature[0]^=1u;
    TEST_ASSERT(image_validate(port,DOWNLOAD_OFFSET,0,0)!=0);
    port=make_signed(1u); /* different signer, same well-formed IMG2 */
    TEST_ASSERT(image_validate(port,DOWNLOAD_OFFSET,0,0)!=0);

    port=make_signed(0u);
    image_header_decode(&h,bytes);h.version++;
    image_header_finalize(&h);image_header_encode(&h,bytes);
    TEST_ASSERT(image_header_ok(&h));
    TEST_ASSERT(image_validate(port,DOWNLOAD_OFFSET,0,0)!=0);

    port=make_signed(0u);bytes[IMAGE_HEADER_SIZE+100u]^=1u;
    image_header_decode(&h,bytes);
    h.payload_crc=crc32_calc(bytes+IMAGE_HEADER_SIZE,SIGNED_TEST_PAYLOAD);
    image_header_finalize(&h);image_header_encode(&h,bytes);
    TEST_ASSERT(image_header_ok(&h));
    TEST_ASSERT(image_validate(port,DOWNLOAD_OFFSET,0,0)!=0);

    port=make_signed(0u);image_header_decode(&h,bytes);h.flags=0u;
    image_header_finalize(&h);image_header_encode(&h,bytes);
    TEST_ASSERT(image_validate(port,DOWNLOAD_OFFSET,0,0)!=0);
    base=BACKUP_OFFSET;
    TEST_ASSERT(image_validate_backup(port,BACKUP_OFFSET,&version,&size)==0);
    TEST_ASSERT(version==7u && size==SIGNED_TEST_PAYLOAD);
    /* The CRC-only exception must not validate a download or a signed header. */
    reads=0u;
    TEST_ASSERT(image_validate_backup(port,DOWNLOAD_OFFSET,0,0)!=0 && reads==0u);
    h.flags=IMAGE_FLAG_SIGNED;image_header_finalize(&h);image_header_encode(&h,bytes);
    TEST_ASSERT(image_validate_backup(port,BACKUP_OFFSET,0,0)!=0);

    port=make_signed(0u);reads=0u;
    TEST_ASSERT(image_signature_verify(port,DOWNLOAD_OFFSET,SIGNED_TEST_PAYLOAD,0)!=0);
    TEST_ASSERT(image_signature_verify(port,DOWNLOAD_OFFSET,7u,signature)!=0);
    TEST_ASSERT(image_signature_verify(port,DOWNLOAD_OFFSET,IMAGE_MAX_PAYLOAD+1u,signature)!=0);
    TEST_ASSERT(image_signature_verify(port,1u,SIGNED_TEST_PAYLOAD,signature)!=0);
    TEST_ASSERT(reads==0u);
    memset(&absent,0,sizeof absent);
    TEST_ASSERT(image_signature_verify(absent,DOWNLOAD_OFFSET,SIGNED_TEST_PAYLOAD,signature)!=0);
}
