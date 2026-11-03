#include "bench.h"
#include "test_util.h"
#include "ota/image.h"
#include "ota/crc32.h"
#include "ota/signature.h"
#include "ota/watchdog_policy.h"
#include "ed25519.h"
#include <string.h>

extern int g_fault_scenarios;
static bench_t B,Idle,Ready,Trial;
static uint8_t img[132000],payload[132000],req[UDS_MAX_DOWNLOAD_BLOCK],resp[64];
static uint16_t rl;
static uint8_t demo_pub[OTA_PUBLIC_KEY_SIZE],demo_priv[64];
static void init_sign_key(void)
{
    unsigned char seed[32];
    int i;
    for(i=0;i<32;i++) seed[i]=(unsigned char)i;
    ed25519_create_keypair(demo_pub,demo_priv,seed);
}
static void put_le(uint8_t *p,uint32_t v)
{ p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8); p[2]=(uint8_t)(v>>16); p[3]=(uint8_t)(v>>24); }
static uint32_t make_image(uint32_t version,uint32_t n,int corrupt)
{
    image_header_t h;
    uint32_t i;
    for(i=0u;i<n;i++) payload[i]=(uint8_t)(i*37u+version);
    put_le(payload,0x20020000u); put_le(payload+4,APP_BASE_ADDR+9u);
    memset(&h,0,sizeof h); h.magic=IMAGE_MAGIC; h.version=version;
    h.load_address=APP_BASE_ADDR; h.image_size=n;
    h.payload_crc=crc32_calc(payload,n); h.flags=IMAGE_FLAG_SIGNED;
    image_header_finalize(&h);
    image_header_encode(&h,img); memcpy(img+IMAGE_HEADER_SIZE,payload,n);
    ed25519_sign(img+IMAGE_HEADER_SIZE+n,img,IMAGE_HEADER_SIZE+n,demo_pub,demo_priv);
    if(corrupt) img[IMAGE_HEADER_SIZE+n/2u]^=0x55u;
    return IMAGE_HEADER_SIZE+n+OTA_SIGNATURE_SIZE;
}
/* Build an IMG2 container, then inject one deliberate signature-layer fault.
 * Modes exist to separate "CRC caught it" from "only the signature caught it":
 *   0  valid control
 *   1  payload altered, payload_crc AND header_crc recomputed (signature stale)
 *   2  header version forged, header_crc recomputed (signature stale)
 *   3  well-formed signature from a key the device does not trust
 *   4  one bit flipped inside the signature
 *   5  unsigned container: flags cleared and no signature appended
 * Returns the exact on-wire byte count the tester must transfer. */
static uint32_t make_signed_image(uint32_t version,uint32_t n,int mode)
{
    image_header_t h;
    uint32_t i,total;
    for(i=0u;i<n;i++) payload[i]=(uint8_t)(i*37u+version);
    put_le(payload,0x20020000u); put_le(payload+4,APP_BASE_ADDR+9u);

    memset(&h,0,sizeof h); h.magic=IMAGE_MAGIC; h.version=version;
    h.load_address=APP_BASE_ADDR; h.image_size=n;
    h.payload_crc=crc32_calc(payload,n);
    h.flags=(mode==5)?0u:IMAGE_FLAG_SIGNED;
    image_header_finalize(&h);
    image_header_encode(&h,img); memcpy(img+IMAGE_HEADER_SIZE,payload,n);
    if(mode==5) return IMAGE_HEADER_SIZE+n; /* no signature on the wire */

    /* Sign the pristine container FIRST, then inject the fault. Modes 1 and 2
     * must leave a STALE signature over content whose CRC and header checks
     * still pass; signing after tampering would prove nothing. */
    if(mode==3) {
        unsigned char seed[32],other_pub[32],other_priv[64];
        uint32_t k;
        for(k=0u;k<32u;k++) seed[k]=(unsigned char)(0xFFu-k);
        ed25519_create_keypair(other_pub,other_priv,seed);
        ed25519_sign(img+IMAGE_HEADER_SIZE+n,img,IMAGE_HEADER_SIZE+n,
                     other_pub,other_priv);
    } else {
        ed25519_sign(img+IMAGE_HEADER_SIZE+n,img,IMAGE_HEADER_SIZE+n,
                     demo_pub,demo_priv);
    }
    if(mode==1) {
        img[IMAGE_HEADER_SIZE+n/2u]^=0x55u;
        image_header_decode(&h,img);
        h.payload_crc=crc32_calc(img+IMAGE_HEADER_SIZE,n);
        image_header_finalize(&h); image_header_encode(&h,img);
    } else if(mode==2) {
        image_header_decode(&h,img); h.version=version+1000u;
        image_header_finalize(&h); image_header_encode(&h,img);
    } else if(mode==4) {
        img[IMAGE_HEADER_SIZE+n+10u]^=0x01u;
    }
    total=IMAGE_HEADER_SIZE+n+OTA_SIGNATURE_SIZE;
    return total;
}
static int call(uint16_t n)
{ rl=0u; return bench_request(&B,req,n,resp,&rl); }
static int unlock(void)
{
    uint32_t seed;
    req[0]=0x10u; req[1]=2u;
    if(call(2u)!=1 || resp[0]!=0x50u) return -1;
    req[0]=0x27u; req[1]=1u;
    if(call(2u)!=1 || resp[0]!=0x67u) return -1;
    seed=t_get_be32(resp+2); req[1]=2u; t_put_be32(req+2,uds_calc_key(seed));
    return call(6u)==1 && resp[0]==0x67u ? 0 : -1;
}
static int start(uint32_t total,uint32_t addr)
{
    req[0]=0x34u; req[1]=0u; req[2]=0x44u;
    t_put_be32(req+3,addr); t_put_be32(req+7,total);
    if(call(11u)!=1) return -1;
    return resp[0]==0x74u ? 0 : (int)resp[2];
}
static int download(uint32_t total)
{
    uint32_t off=0u;
    uint8_t counter=1u;
    if(unlock() || start(total,APP_BASE_ADDR)) return -1;
    while(off<total) {
        uint32_t n=total-off;
        if(n>UDS_MAX_DOWNLOAD_DATA) n=UDS_MAX_DOWNLOAD_DATA;
        req[0]=0x36u; req[1]=counter;
        memcpy(req+2,img+off,n);
        if(call((uint16_t)(n+2u))!=1) return -1;
        if(resp[0]!=0x76u) return resp[2];
        off+=n; counter=(uint8_t)(counter+1u);
    }
    req[0]=0x37u;
    if(call(1u)!=1) return -1;
    return resp[0]==0x77u ? 0 : resp[2];
}
static void save_fixture(bench_t *to)
{
    memcpy(to->internal_mem,B.internal_mem,sizeof B.internal_mem);
    memcpy(to->external_mem,B.external_mem,sizeof B.external_mem);
    memcpy(to->eeprom.mem,B.eeprom.mem,sizeof B.eeprom.mem);
}
static void restore_fixture(const bench_t *from)
{
    memcpy(B.internal_mem,from->internal_mem,sizeof B.internal_mem);
    memcpy(B.external_mem,from->external_mem,sizeof B.external_mem);
    memcpy(B.eeprom.mem,from->eeprom.mem,sizeof B.eeprom.mem);
    bench_reboot(&B);
}
static int boot_area_ok(void)
{
    uint32_t i;
    for(i=0u;i<BOOT_SIZE;i++) if(B.internal_mem[i]!=0xA6u) return 0;
    return 1;
}
static int running_version(uint32_t v)
{
    metadata_t m;
    uint32_t size,crc,version;
    if(metadata_load(&B.meta,&m)!=1) return 0;
    if(m.state==UPDATE_TRIAL) { size=m.pending_size; crc=m.pending_crc; version=m.pending_version; }
    else { size=m.active_size; crc=m.active_crc; version=m.active_version; }
    return version==v && image_validate_app(B.boot.internal,size,crc)==0;
}
static void fixtures(void)
{
    metadata_t m;
    bench_init(&B); memset(B.internal_mem,0xA6,BOOT_SIZE);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_LOADER);
    TEST_ASSERT(download(make_image(1u,1003u,0))==0);
    TEST_ASSERT(boot_confirm(&B.boot)!=0); /* READY is not a booted app */
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP);
    TEST_ASSERT(memcmp(B.internal_mem+APP_OFFSET,payload,1003u)==0);
    TEST_ASSERT(B.internal_mem[APP_OFFSET+1003u]==0xFFu);
    TEST_ASSERT(boot_confirm(&B.boot)==0);
    TEST_ASSERT(running_version(1u));
    save_fixture(&Idle);
    TEST_ASSERT(download(make_image(2u,1503u,0))==0);
    save_fixture(&Ready);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP);
    TEST_ASSERT(m.state==UPDATE_TRIAL && m.boot_attempts==1u);
    TEST_ASSERT(running_version(2u));
    save_fixture(&Trial);
    TEST_ASSERT(boot_area_ok());
}
static void rejected_signed_download(uint32_t total)
{
    metadata_t m;
    TEST_ASSERT(download(total)==0x72);
    TEST_ASSERT(memcmp(B.internal_mem,Idle.internal_mem,sizeof B.internal_mem)==0);
    TEST_ASSERT(metadata_load(&B.meta,&m)==1 && m.state==UPDATE_DOWNLOADING);
    TEST_ASSERT(m.active_version==1u && m.pending_version==0u);
    bench_reboot(&B);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && running_version(1u));
}
static void signed_package_paths(void)
{
    metadata_t m;
    image_header_t h;
    uint32_t total;
    puts("[signed UDS commit/install/recovery paths]");

    restore_fixture(&Idle);total=make_image(2u,1503u,0);
    img[total-1u]^=1u;
    rejected_signed_download(total);

    /* CRCs are repaired after tampering; the signature must still reject it. */
    restore_fixture(&Idle);total=make_image(2u,1503u,0);
    img[IMAGE_HEADER_SIZE+99u]^=1u;
    image_header_decode(&h,img);
    h.payload_crc=crc32_calc(img+IMAGE_HEADER_SIZE,h.image_size);
    image_header_finalize(&h);image_header_encode(&h,img);
    rejected_signed_download(total);

    restore_fixture(&Idle);total=make_image(2u,1503u,0);
    image_header_decode(&h,img);h.version=3u;
    image_header_finalize(&h);image_header_encode(&h,img);
    rejected_signed_download(total);

    /* Clearing the signed flag and truncating the tail cannot downgrade policy. */
    restore_fixture(&Idle);total=make_image(2u,1503u,0);
    image_header_decode(&h,img);h.flags=0u;
    image_header_finalize(&h);image_header_encode(&h,img);
    rejected_signed_download(total-OTA_SIGNATURE_SIZE);
    restore_fixture(&Idle);total=make_image(2u,1503u,0);
    rejected_signed_download(total-1u);

    /* A package that changes after 0x37 is rechecked before any App erasure. */
    restore_fixture(&Ready);
    B.external_mem[IMAGE_HEADER_SIZE+1503u+7u]^=1u;
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && m.state==UPDATE_IDLE);
    TEST_ASSERT(memcmp(B.internal_mem,Idle.internal_mem,sizeof B.internal_mem)==0);
    TEST_ASSERT(running_version(1u) && boot_area_ok());

    /* Interrupted install + invalidated signature must recover the old image. */
    restore_fixture(&Trial);
    TEST_ASSERT(metadata_load(&B.meta,&m)==1);
    m.state=UPDATE_INSTALLING;TEST_ASSERT(metadata_save(&B.meta,&m)==0);
    B.external_mem[IMAGE_HEADER_SIZE+1503u+7u]^=1u;
    memset(B.internal_mem+APP_OFFSET,0,64u);
    bench_reboot(&B);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && m.state==UPDATE_IDLE);
    TEST_ASSERT(running_version(1u) && boot_area_ok());

    restore_fixture(&Idle);
    TEST_ASSERT(download(make_image(2u,1503u,0))==0);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && m.state==UPDATE_TRIAL);
    TEST_ASSERT(boot_accept_app_health(&B.boot)==0);
    TEST_ASSERT(metadata_load(&B.meta,&m)==1 && m.active_version==2u && m.min_version==2u);
}
static void ordinary_paths(void)
{
    metadata_t m;
    image_header_t header;
    uint32_t total,ops;
    uint8_t routine[]={0x31u,1u,0u,1u};
    printf("[boot/UDS storage paths]\n");
    fixtures();

    restore_fixture(&Ready);
    TEST_ASSERT(unlock()==0);
    memcpy(req,routine,sizeof routine);
    TEST_ASSERT(call(sizeof routine)==1 && resp[0]==0x71u && rl==5u);
    TEST_ASSERT(metadata_load(&B.meta,&m)==1 && m.state==UPDATE_READY);
    TEST_ASSERT(boot_confirm(&B.boot)!=0);
    TEST_ASSERT(start(100u,APP_BASE_ADDR)==0x22); /* cannot erase a pending package */

    restore_fixture(&Trial);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && m.boot_attempts==2u);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && m.boot_attempts==3u);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && m.state==UPDATE_IDLE);
    TEST_ASSERT(running_version(1u) && boot_area_ok());

    restore_fixture(&Trial);
    TEST_ASSERT(boot_confirm(&B.boot)==0);
    TEST_ASSERT(metadata_load(&B.meta,&m)==1 && m.active_version==2u && m.min_version==2u);
    TEST_ASSERT(download(make_image(1u,1003u,0))==0x31); /* downgrade */
    TEST_ASSERT(download(make_image(2u,1003u,0))==0x31); /* equal version */
    TEST_ASSERT(running_version(2u));
    TEST_ASSERT(download(make_image(3u,1003u,1))==0x72);
    bench_reboot(&B); TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP);
    TEST_ASSERT(running_version(2u));

    /* Download interrupted before commit: internal app is untouched. */
    restore_fixture(&Trial); TEST_ASSERT(boot_confirm(&B.boot)==0);
    total=make_image(3u,1503u,0); TEST_ASSERT(unlock()==0);
    TEST_ASSERT(start(total,APP_BASE_ADDR)==0);
    TEST_ASSERT(rl==4u && resp[1]==0x20u && resp[2]==2u && resp[3]==0u);
    req[0]=0x36u; req[1]=1u; memcpy(req+2,img,200u);
    TEST_ASSERT(call(202u)==1 && resp[0]==0x76u);
    ops=B.external.write_ops;
    TEST_ASSERT(call(202u)==1 && resp[0]==0x76u && B.external.write_ops==ops);
    req[20]^=1u;
    TEST_ASSERT(call(202u)==1 && resp[2]==0x73u);
    req[20]^=1u; req[1]=3u;
    TEST_ASSERT(call(202u)==1 && resp[2]==0x73u);
    req[0]=0x37u; TEST_ASSERT(call(1u)==1 && resp[2]==0x24u);
    bench_reboot(&B);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && running_version(2u));
    TEST_ASSERT(download(total)==0);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && running_version(3u));
    TEST_ASSERT(boot_confirm(&B.boot)==0);

    /* FF -> 00 block-counter wrap through the actual CAN/ISO-TP bench. */
    restore_fixture(&Trial); TEST_ASSERT(boot_confirm(&B.boot)==0);
    TEST_ASSERT(download(make_image(3u,131000u,0))==0);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && running_version(3u));

    /* Invalid RAM vectors / wrong fixed execution address / header CRC. */
    restore_fixture(&Trial); TEST_ASSERT(boot_confirm(&B.boot)==0);
    total=make_image(3u,1003u,0);
    image_header_decode(&header,img); header.load_address=APP_BASE_ADDR+0x20000u;
    image_header_finalize(&header); image_header_encode(&header,img);
    TEST_ASSERT(download(total)==0x72); /* correct CRC, wrong linked address */
    total=make_image(3u,1003u,0);
    put_le(img+IMAGE_HEADER_SIZE,0x20020001u);
    image_header_decode(&header,img);
    header.payload_crc=crc32_calc(img+IMAGE_HEADER_SIZE,header.image_size);
    image_header_finalize(&header); image_header_encode(&header,img);
    TEST_ASSERT(download(total)==0x72); /* correct CRC, misaligned stack */
    total=make_image(3u,1003u,0);
    put_le(img+IMAGE_HEADER_SIZE+4u,APP_BASE_ADDR+8u);
    image_header_decode(&header,img);
    header.payload_crc=crc32_calc(img+IMAGE_HEADER_SIZE,header.image_size);
    image_header_finalize(&header); image_header_encode(&header,img);
    TEST_ASSERT(download(total)==0x72); /* correct CRC, Thumb bit missing */
    total=make_image(3u,1003u,0); img[28]^=1u;
    TEST_ASSERT(download(total)==0x72);
    total=make_image(3u,1003u,0);
    TEST_ASSERT(download(total-1u)==0x72); /* full transfer, truncated container */
    image_header_decode(&header,img); header.image_size=IMAGE_MAX_PAYLOAD+1u;
    image_header_finalize(&header);
    TEST_ASSERT(!image_header_ok(&header));

    restore_fixture(&Ready);
    B.external_mem[IMAGE_HEADER_SIZE+100u]^=1u;
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && running_version(1u));
    restore_fixture(&Trial);
    B.internal_mem[APP_OFFSET+100u]^=1u;
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && running_version(1u));

    /* A corrupt staged package while INSTALLING restores the durable backup. */
    restore_fixture(&Trial);
    TEST_ASSERT(metadata_load(&B.meta,&m)==1);
    m.state=UPDATE_INSTALLING; m.boot_attempts=0u;
    TEST_ASSERT(metadata_save(&B.meta,&m)==0);
    B.external_mem[IMAGE_HEADER_SIZE+100u]^=1u;
    memset(B.internal_mem+APP_OFFSET,0xFF,APP_SIZE/2u);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && running_version(1u));

    restore_fixture(&Ready);
    B.eeprom.read_fail_after=1; /* only one copy can be read */
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_LOADER);
    TEST_ASSERT(memcmp(B.internal_mem,Ready.internal_mem,sizeof B.internal_mem)==0);
    restore_fixture(&Ready);
    B.eeprom.ignore_writes=1;
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_LOADER);
    TEST_ASSERT(memcmp(B.internal_mem,Ready.internal_mem,sizeof B.internal_mem)==0);

    restore_fixture(&Trial); TEST_ASSERT(boot_confirm(&B.boot)==0);
    TEST_ASSERT(unlock()==0);
    TEST_ASSERT(start(100u,FLASH_BASE_ADDR)==0x31);
    TEST_ASSERT(start(100u,0x08020000u)==0x31);
    B.now_ms+=UDS_S3_TIMEOUT_MS+1u; uds_poll(&B.server);
    TEST_ASSERT(B.server.session==UDS_SESSION_DEFAULT && !B.server.unlocked);
    TEST_ASSERT(boot_area_ok());
}
static uint64_t cuts[FAULT_EVENT_MAX*3u+1u];
static uint32_t cut_count;
static void add_cut(uint64_t cut)
{
    uint32_t i;
    for(i=0;i<cut_count;i++) if(cuts[i]==cut) return;
    cuts[cut_count++]=cut;
}
static void sweep(const bench_t *fixture,uint32_t expected_version)
{
    uint32_t i;
    uint64_t total;
    metadata_t m;
    restore_fixture(fixture);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP);
    TEST_ASSERT(B.fault.event_count<FAULT_EVENT_MAX);
    total=B.fault.bytes; cut_count=0u;
    /* Sample BEFORE, halfway through and just before completion of EVERY
     * mutation call, including page writes and partial erase/program calls. */
    for(i=0u;i<B.fault.event_count;i++) {
        const fault_event_t *e=&B.fault.events[i];
        add_cut(e->start); add_cut(e->start+e->len/2u); add_cut(e->start+e->len-1u);
    }
    add_cut(total);
    printf("  sampled %u durable-mutation cut points (expected v%u)\n",
           (unsigned)cut_count,(unsigned)expected_version);
    for(i=0u;i<cut_count;i++) {
        boot_target_t target;
        restore_fixture(fixture); B.fault.remaining=(int64_t)cuts[i];
        target=boot_select(&B.boot,&m);
        if(cuts[i]<total) TEST_ASSERT(B.fault.powered_off && target==BOOT_TARGET_LOADER);
        else TEST_ASSERT(target==BOOT_TARGET_APP);
        /* New link/server/boot/port objects, same persistent bytes. */
        bench_reboot(&B);
        TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP);
        TEST_ASSERT(running_version(expected_version));
        TEST_ASSERT(boot_area_ok());
        if(m.state==UPDATE_TRIAL) TEST_ASSERT(boot_confirm(&B.boot)==0);
        TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP);
        TEST_ASSERT(running_version(expected_version));
        g_fault_scenarios++;
    }
}
static void metadata_faults(void)
{
    uint32_t cut;
    metadata_t m,before,loaded;
    int result,index;
    uint8_t *raw;
    printf("[EEPROM transaction cuts]\n");
    /* 4 invalidation + 60 body/CRC + 4 commit bytes: every byte boundary. */
    for(cut=0u;cut<=68u;cut++) {
        restore_fixture(&Ready);
        TEST_ASSERT(metadata_load(&B.meta,&m)==1); before=m;
        m.state=UPDATE_INSTALLING;
        B.fault.remaining=cut;
        result=metadata_save(&B.meta,&m);
        if(cut<68u) TEST_ASSERT(result!=0 && m.seq==before.seq);
        else TEST_ASSERT(result==0);
        bench_reboot(&B);
        TEST_ASSERT(metadata_load(&B.meta,&loaded)==1);
        TEST_ASSERT(loaded.state==(cut<68u ? UPDATE_READY : UPDATE_INSTALLING));
        TEST_ASSERT(loaded.seq==before.seq+(cut==68u ? 1u : 0u));
        g_fault_scenarios++;
    }
    /* Corrupt newest record: the other committed copy remains usable. */
    restore_fixture(&Ready);
    TEST_ASSERT(metadata_load_ex(&B.meta,&m,&index)==1);
    B.eeprom.mem[(index ? META_B_OFFSET : META_A_OFFSET)+56u]^=1u;
    TEST_ASSERT(metadata_load(&B.meta,&m)==1 && m.state==UPDATE_DOWNLOADING);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && running_version(1u));

    /* Sequence comparison across UINT32_MAX, with valid serialized CRCs. */
    restore_fixture(&Ready);
    raw=B.eeprom.mem+META_A_OFFSET;
    put_le(raw+4u,0xFFFFFFFEu); put_le(raw+56u,crc32_calc(raw,56u));
    raw=B.eeprom.mem+META_B_OFFSET;
    put_le(raw+4u,0xFFFFFFFFu); put_le(raw+56u,crc32_calc(raw,56u));
    TEST_ASSERT(metadata_load(&B.meta,&m)==1 && m.seq==0xFFFFFFFFu);
    TEST_ASSERT(metadata_save(&B.meta,&m)==0 && m.seq==0u);
    TEST_ASSERT(metadata_load(&B.meta,&m)==1 && m.seq==0u);
    /* Exactly half a sequence space is ambiguous; fail closed. */
    raw=B.eeprom.mem+META_B_OFFSET;
    put_le(raw+4u,0x80000000u); put_le(raw+56u,crc32_calc(raw,56u));
    TEST_ASSERT(metadata_load(&B.meta,&m)==-1);
}

static void first_install_recovery(void)
{
    metadata_t m;
    uint32_t i;
    bench_init(&B); memset(B.internal_mem,0xA6,BOOT_SIZE);
    TEST_ASSERT(download(make_image(1u,1003u,0))==0);
    for(i=0u;i<MAX_BOOT_ATTEMPTS;i++)
        TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_LOADER);
    TEST_ASSERT(metadata_load(&B.meta,&m)==1 && m.state==UPDATE_IDLE && !m.active_size);
    /* No previous known-good image: remain in loader and accept a fresh package. */
    TEST_ASSERT(download(make_image(2u,1003u,0))==0);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP);
    TEST_ASSERT(boot_confirm(&B.boot)==0 && running_version(2u) && boot_area_ok());
    restore_fixture(&Trial);
    B.eeprom.ignore_writes=1;
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_LOADER);
}
static void confirmation_cuts(void)
{
    uint32_t cut;
    metadata_t m;
    printf("[application-confirmation transaction cuts]\n");
    for(cut=0u;cut<=68u;cut++) {
        restore_fixture(&Trial); B.fault.remaining=cut;
        TEST_ASSERT((boot_confirm(&B.boot)==0)==(cut==68u));
        bench_reboot(&B);
        TEST_ASSERT(metadata_load(&B.meta,&m)==1);
        TEST_ASSERT(m.state==(cut==68u ? UPDATE_IDLE : UPDATE_TRIAL));
        TEST_ASSERT(m.min_version==(cut==68u ? 2u : 1u));
        TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && running_version(2u));
        if(m.state==UPDATE_TRIAL) TEST_ASSERT(boot_confirm(&B.boot)==0);
        TEST_ASSERT(boot_area_ok());
        g_fault_scenarios++;
    }
}
static void download_cuts(void)
{
    uint32_t i,total_image=make_image(2u,1503u,0);
    uint64_t total;
    metadata_t m;
    printf("[download/commit reset sweep]\n");
    restore_fixture(&Idle);
    TEST_ASSERT(download(total_image)==0);
    TEST_ASSERT(B.fault.event_count<FAULT_EVENT_MAX);
    total=B.fault.bytes; cut_count=0u;
    for(i=0u;i<B.fault.event_count;i++) {
        const fault_event_t *e=&B.fault.events[i];
        add_cut(e->start); add_cut(e->start+e->len/2u); add_cut(e->start+e->len-1u);
    }
    add_cut(total);
    printf("  sampled %u download mutation cut points\n",(unsigned)cut_count);
    for(i=0u;i<cut_count;i++) {
        int r;
        restore_fixture(&Idle); B.fault.remaining=(int64_t)cuts[i];
        r=download(total_image);
        TEST_ASSERT((r==0)==(cuts[i]==total));
        TEST_ASSERT(memcmp(B.internal_mem,Idle.internal_mem,sizeof B.internal_mem)==0);
        bench_reboot(&B);
        TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP);
        TEST_ASSERT(running_version(cuts[i]==total ? 2u : 1u));
        TEST_ASSERT(boot_area_ok());
        g_fault_scenarios++;
    }
}

static void watchdog_recovery(void)
{
    watchdog_policy_t guard;
    metadata_t m;
    uint32_t attempt,tick,last_feed,writes;
    puts("[watchdog-driven reset model]");
    restore_fixture(&Ready);
    for(attempt=1u;attempt<=MAX_BOOT_ATTEMPTS;attempt++) {
        TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && m.boot_attempts==attempt);
        watchdog_policy_init(&guard,1,0u,1000u); last_feed=0u;
        for(tick=250u;tick<=2000u;tick+=250u) {
            TEST_ASSERT(!watchdog_policy_should_feed(&guard,tick,0));
            if(tick-last_feed>=2000u) break; /* modeled hardware watchdog */
        }
        TEST_ASSERT(tick==2000u && guard.phase==WATCHDOG_FAILED);
        bench_reboot(&B); /* only watchdog model requests this reset */
    }
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && m.state==UPDATE_IDLE);
    TEST_ASSERT(running_version(1u) && boot_area_ok());

    restore_fixture(&Ready); TEST_ASSERT(boot_accept_app_health(&B.boot)!=0);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP);
    watchdog_policy_init(&guard,1,0u,1000u);
    TEST_ASSERT(watchdog_policy_may_confirm(&guard,500u));
    TEST_ASSERT(!boot_accept_app_health(&B.boot) && !watchdog_policy_confirmed(&guard));
    last_feed=500u;
    for(tick=500u;tick<=4000u;tick+=250u) {
        TEST_ASSERT(watchdog_policy_should_feed(&guard,tick,1));
        last_feed=tick;
    }
    TEST_ASSERT(tick-last_feed<2000u);
    bench_reboot(&B);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && m.active_version==2u);

    restore_fixture(&Ready); TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP);
    B.eeprom.ignore_writes=1;
    TEST_ASSERT(boot_accept_app_health(&B.boot)!=0);
    bench_reboot(&B);
    TEST_ASSERT(metadata_load(&B.meta,&m)==1 && m.state==UPDATE_TRIAL);

    restore_fixture(&Idle); TEST_ASSERT(metadata_load(&B.meta,&m)==1);
    m.state=UPDATE_DOWNLOADING; TEST_ASSERT(!metadata_save(&B.meta,&m));
    writes=B.eeprom.write_ops;
    TEST_ASSERT(!boot_accept_app_health(&B.boot));
    TEST_ASSERT(B.eeprom.write_ops==writes);
    TEST_ASSERT(metadata_load(&B.meta,&m)==1 && m.state==UPDATE_DOWNLOADING);
    B.internal_mem[APP_OFFSET+10u]^=1u;
    TEST_ASSERT(boot_accept_app_health(&B.boot)!=0);
}
/* Proves the signature layer is wired into every acceptance point, and that
 * the rejection is not merely the older CRC/vector checks firing first. */
static void signature_paths(void)
{
#if OTA_REQUIRE_SIGNATURE
    metadata_t m;
    printf("[signature: download/install/restore paths]\n");
    restore_fixture(&Trial); TEST_ASSERT(boot_confirm(&B.boot)==0);

    /* Control: a correctly signed package still reaches READY. */
    TEST_ASSERT(download(make_signed_image(3u,1003u,0))==0);

    /* 1. Payload altered with BOTH CRCs recomputed. CRC32 alone would accept
     *    this; only the Ed25519 signature can reject it. This is the case the
     *    signature was added for. */
    restore_fixture(&Trial); TEST_ASSERT(boot_confirm(&B.boot)==0);
    TEST_ASSERT(download(make_signed_image(3u,1003u,1))==0x72);

    /* 2. Version forged with a valid header CRC. The signature covers the
     *    header, so this fails before the monotonic-version check runs. */
    restore_fixture(&Trial); TEST_ASSERT(boot_confirm(&B.boot)==0);
    TEST_ASSERT(download(make_signed_image(3u,1003u,2))==0x72);

    /* 3. Well-formed signature produced by an untrusted key. */
    restore_fixture(&Trial); TEST_ASSERT(boot_confirm(&B.boot)==0);
    TEST_ASSERT(download(make_signed_image(3u,1003u,3))==0x72);

    /* 4. Single bit flipped inside the signature. */
    restore_fixture(&Trial); TEST_ASSERT(boot_confirm(&B.boot)==0);
    TEST_ASSERT(download(make_signed_image(3u,1003u,4))==0x72);

    /* 5. Unsigned container while mandatory signing is enabled. */
    restore_fixture(&Trial); TEST_ASSERT(boot_confirm(&B.boot)==0);
    TEST_ASSERT(download(make_signed_image(3u,1003u,5))==0x72);

    /* No reject may advance the plan, replace the running app or touch boot. */
    TEST_ASSERT(metadata_load(&B.meta,&m)==1 && m.state!=UPDATE_READY);
    TEST_ASSERT(running_version(2u));
    TEST_ASSERT(boot_area_ok());

    /* A genuine package must still install afterwards: rejects leave no
     * stuck download/session state behind. */
    restore_fixture(&Trial); TEST_ASSERT(boot_confirm(&B.boot)==0);
    TEST_ASSERT(download(make_signed_image(3u,1003u,0))==0);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP && running_version(3u));
    TEST_ASSERT(boot_confirm(&B.boot)==0);

    /* The backup container is intentionally unsigned, but that exemption must
     * apply to the backup validator only. Drive READY -> backup+install once,
     * then show the CRC-only validator accepts the backup while the signed
     * validator rejects it, so restore is not a global signature bypass. */
    restore_fixture(&Ready);
    TEST_ASSERT(boot_select(&B.boot,&m)==BOOT_TARGET_APP);
    TEST_ASSERT(image_validate_backup(B.boot.external,BACKUP_OFFSET,0,0)==0);
    TEST_ASSERT(image_validate(B.boot.external,BACKUP_OFFSET,0,0)!=0);
#endif
}
void run_boot_tests(void)
{
    metadata_t m;
    init_sign_key();
    ordinary_paths();
    signed_package_paths();
    signature_paths();
    watchdog_recovery();
    first_install_recovery();
    metadata_faults();
    confirmation_cuts();
    download_cuts();
    printf("[installation/reset sweep]\n");
    sweep(&Ready,2u);
    restore_fixture(&Trial);
    TEST_ASSERT(metadata_load(&B.meta,&m)==1);
    m.boot_attempts=MAX_BOOT_ATTEMPTS;
    TEST_ASSERT(metadata_save(&B.meta,&m)==0);
    save_fixture(&Trial);
    printf("[rollback/reset sweep]\n");
    sweep(&Trial,1u);
}
