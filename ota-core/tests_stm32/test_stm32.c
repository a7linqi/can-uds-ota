#include "mock_board.h"
#include "ota/crc32.h"
#include "ota/image.h"
#include "ota/signature.h"
#include "ed25519.h"
#include <stdio.h>
#include <string.h>
static int checks,failures;
static uint8_t demo_pub[OTA_PUBLIC_KEY_SIZE],demo_priv[64];
static void init_sign_key(void)
{
    unsigned char seed[32];
    int i;
    for(i=0;i<32;i++) seed[i]=(unsigned char)i;
    ed25519_create_keypair(demo_pub,demo_priv,seed);
}
#define CHECK(x) do {checks++;if(!(x)){failures++;printf("FAIL line %d: %s\n",__LINE__,#x);}} while(0)
static ota_stm32_ports_t P;
static ota_stm32_diag_t D;
static void fresh(void)
{ ota_board_config_t c=mock_config(); mock_board_reset(); ota_stm32_ports_init(&P,&c); }
static void put_le(uint8_t *p,uint32_t v)
{ p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);p[2]=(uint8_t)(v>>16);p[3]=(uint8_t)(v>>24); }
static void storage_checks(void)
{
    ota_board_config_t c;
    uint8_t data[300],readback[300],word[4]={0x12,0x34,0x56,0x78};
    uint32_t sectors=(FLASH_TOTAL_SIZE==0x100000u)?8u:4u,i;
    c=mock_config(); c.detected_flash_bytes=(FLASH_TOTAL_SIZE==0x100000u)?0x80000u:0x100000u;
    mock_board_reset(); ota_stm32_ports_init(&P,&c);
    CHECK(!P.storage_ready);
    CHECK(P.internal.erase(P.internal.user,APP_OFFSET,APP_SIZE)!=0 && !M.unlocks);
    c=mock_config(); c.storage_verified=0; ota_stm32_ports_init(&P,&c);
    CHECK(P.external.write(P.external.user,0u,word,4u)!=0 && !M.nor_writes);
    fresh(); CHECK(P.storage_ready);
    CHECK(P.internal.erase(P.internal.user,0u,0x10000u)!=0);
    CHECK(P.internal.erase(P.internal.user,APP_OFFSET+1u,0x10000u)!=0);
    CHECK(!M.unlocks);
    memset(M.internal,0xA6,BOOT_SIZE);
    CHECK(P.internal.erase(P.internal.user,APP_OFFSET,APP_SIZE)==0);
    CHECK(M.sectors==sectors && M.unlocks==1u && M.locks==1u);
    CHECK(M.internal[0]==0xA6u && M.internal[BOOT_SIZE-1u]==0xA6u);
    CHECK(P.internal.write(P.internal.user,APP_OFFSET,word,4u)==0);
    CHECK(P.internal.read(P.internal.user,APP_OFFSET,readback,4u)==0 && !memcmp(word,readback,4u));
    CHECK(P.internal.write(P.internal.user,APP_OFFSET+1u,word,4u)!=0);
    CHECK(P.internal.write(P.internal.user,0u,word,4u)!=0);
    memset(word,0xFF,4u);
    CHECK(P.internal.write(P.internal.user,APP_OFFSET,word,4u)!=0); /* cannot set 0 -> 1 */
    CHECK(M.programs==1u && M.unlocks==M.locks);
    fresh(); M.fail_sector=5;
    CHECK(P.internal.erase(P.internal.user,APP_OFFSET,APP_SIZE)!=0);
    CHECK(M.sectors==2u && M.locks==1u);
    fresh(); M.fail_program=0;
    CHECK(P.internal.write(P.internal.user,APP_OFFSET,word,4u)!=0 && M.locks==1u);
    fresh(); M.fail_lock=1;
    CHECK(P.internal.write(P.internal.user,APP_OFFSET,word,4u)!=0);

    fresh(); for(i=0u;i<sizeof data;i++) data[i]=(uint8_t)i;
    CHECK(P.external.write(P.external.user,250u,data,sizeof data)==0);
    CHECK(M.nor_writes==3u);
    CHECK(M.page_offsets[0]==250u && M.page_lengths[0]==6u);
    CHECK(M.page_offsets[1]==256u && M.page_lengths[1]==256u);
    CHECK(M.page_offsets[2]==512u && M.page_lengths[2]==38u);
    CHECK(P.external.read(P.external.user,250u,readback,sizeof data)==0);
    CHECK(!memcmp(data,readback,sizeof data));
    CHECK(P.external.write(P.external.user,0xFFFFFFFFu,data,1u)!=0);
    CHECK(P.external.write(P.external.user,EXT_FLASH_TOTAL_SIZE-1u,data,2u)!=0);
    CHECK(P.external.erase(P.external.user,1u,4096u)!=0);
    CHECK(P.external.erase(P.external.user,0u,8192u)==0 && M.nor_erases==2u);
    CHECK(P.eeprom.write(P.eeprom.user,7u,data,2u)!=0 && !M.ee_writes);
    CHECK(P.eeprom.write(P.eeprom.user,8u,data,8u)==0 && M.ee_writes==1u);
    CHECK(P.eeprom.write(P.eeprom.user,255u,data,2u)!=0);
    c=mock_config(); c.eeprom_page_bytes=16u; ota_stm32_ports_init(&P,&c);
    CHECK(!P.storage_ready);
}
static void can_checks(void)
{
    ota_stm32_can_t q;
    ota_port_t port;
    can_frame_t f,out;
    uint32_t i;
    fresh(); ota_stm32_can_init(&q);
    memset(&f,0,sizeof f); f.id=0x7E0u; f.dlc=1u;
    M.irq_state=1u; CHECK(ota_stm32_can_rx_isr(&q,&f)==0 && M.irq_state==1u);
    CHECK(ota_stm32_can_pop(&q,&out)==1); M.irq_state=0u;
    for(i=0u;i<OTA_RX_QUEUE-1u;i++) { f.data[0]=(uint8_t)i; CHECK(!ota_stm32_can_rx_isr(&q,&f)); }
    CHECK(ota_stm32_can_rx_isr(&q,&f)!=0 && q.rx_dropped==1u);
    for(i=0u;i<OTA_RX_QUEUE-1u;i++) {
        CHECK(ota_stm32_can_pop(&q,&out)==1 && out.data[0]==(uint8_t)i);
    }
    CHECK(!ota_stm32_can_pop(&q,&out) && M.irq_state==0u);
    f.dlc=9u; CHECK(ota_stm32_can_rx_isr(&q,&f)!=0);
    f.dlc=1u; f.id=0x800u; CHECK(ota_stm32_can_rx_isr(&q,&f)!=0);
    f.extended=1; CHECK(ota_stm32_can_rx_isr(&q,&f)==0);
    port=ota_stm32_can_port(&q);
    for(i=0u;i<OTA_TX_QUEUE-1u;i++) CHECK(!port.can_send(port.user,&f));
    CHECK(port.can_send(port.user,&f)!=0);
    M.can_busy=1; CHECK(!ota_stm32_can_poll(&q) && !M.sent_count);
    M.can_busy=0; M.hardware_idle=0;
    for(i=0u;i<OTA_TX_QUEUE-1u;i++) CHECK(!ota_stm32_can_poll(&q));
    CHECK(!ota_stm32_can_idle(&q));
    M.hardware_idle=1; CHECK(ota_stm32_can_idle(&q));
}
static void receive(const uint8_t *data,uint8_t n)
{
    can_frame_t f;
    memset(&f,0,sizeof f); f.id=0x7E0u; f.dlc=n; memcpy(f.data,data,n);
    CHECK(ota_stm32_can_rx_isr(&D.can,&f)==0);
    CHECK(ota_stm32_diag_poll(&D)==0);
}
static void diagnostics_checks(void)
{
    const uint8_t version[]={3,0x22,0xF1,0x95};
    const uint8_t flow[]={0x30,0,0};
    const uint8_t first[]={0x10,0x0B,0x34,0,0x44,8,1,0};
    const uint8_t next[]={0x21,0,0,0,6,0};
    const uint8_t reset[]={2,0x11,1};
    ota_board_config_t c=mock_config();
    fresh(); c.storage_verified=0; ota_stm32_ports_init(&P,&c);
    ota_stm32_diag_init(&D,&P);
    CHECK(!D.programming_allowed && !P.storage_ready);
    receive(version,sizeof version);
    CHECK(M.sent_count==1u && M.sent[0].id==0x7E8u);
    CHECK(M.sent[0].data[0]==0x10u && M.sent[0].data[1]==8u && M.sent[0].data[2]==0x62u);
    receive(flow,sizeof flow);
    CHECK(M.sent_count==2u && M.sent[1].data[0]==0x21u);
    CHECK(M.sent[1].data[1]=='.' && M.sent[1].data[2]=='0');
    receive(first,sizeof first); receive(next,sizeof next);
    CHECK(M.sent_count==4u && M.sent[3].data[1]==0x7Fu &&
          M.sent[3].data[2]==0x34u && M.sent[3].data[3]==0x22u);
    CHECK(!M.unlocks && !M.nor_erases && !M.ee_writes);
    fresh(); ota_stm32_diag_init(&D,&P);
    M.can_busy=1; M.hardware_idle=0;
    receive(reset,sizeof reset);
    CHECK(!M.sent_count && !M.reset_count);
    M.can_busy=0; CHECK(!ota_stm32_diag_poll(&D));
    CHECK(M.sent_count==1u && M.sent[0].data[1]==0x51u && !M.reset_count);
    M.hardware_idle=1; CHECK(!ota_stm32_diag_poll(&D) && M.reset_count==1u);
}
static void handoff_checks(void)
{
    metadata_t m;
    uint32_t stack,entry;
    fresh(); memset(M.internal+APP_OFFSET,0xA5,16u);
    put_le(M.internal+APP_OFFSET,0x20020000u); put_le(M.internal+APP_OFFSET+4u,APP_BASE_ADDR+9u);
    memset(&m,0,sizeof m); m.active_version=1u; m.active_size=16u;
    m.active_crc=crc32_calc(M.internal+APP_OFFSET,16u); m.min_version=1u;
    CHECK(!metadata_save(&P.store,&m));
    CHECK(!ota_stm32_app_vectors(&P,&stack,&entry));
    CHECK(stack==0x20020000u && entry==APP_BASE_ADDR+9u);
#if OTA_ENABLE_BOOT_HANDOFF
    CHECK(ota_stm32_jump_to_app(&P)!=0 && M.handoff_count==1u); /* mocked branch returns */
    M.quiesce=-1;
    CHECK(ota_stm32_jump_to_app(&P)!=0 && M.handoff_count==1u);
    M.quiesce=0;
#else
    CHECK(ota_stm32_jump_to_app(&P)!=0 && !M.handoff_count);
#endif
    m.state=UPDATE_READY; m.pending_version=2u; m.pending_size=16u; m.pending_crc=m.active_crc;
    CHECK(!metadata_save(&P.store,&m));
    CHECK(ota_stm32_app_vectors(&P,&stack,&entry)!=0);
    m.state=UPDATE_TRIAL; CHECK(!metadata_save(&P.store,&m));
    CHECK(ota_stm32_app_vectors(&P,&stack,&entry)!=0); /* attempt not committed */
    m.boot_attempts=1u; CHECK(!metadata_save(&P.store,&m));
    CHECK(!ota_stm32_app_vectors(&P,&stack,&entry));
    M.internal[APP_OFFSET+10u]^=1u;
    CHECK(ota_stm32_app_vectors(&P,&stack,&entry)!=0);
}
static void queue_download(void)
{
    const uint8_t first[]={0x10,0x0B,0x34,0,0x44,8,1,0};
    const uint8_t next[]={0x21,0,0,0,6,0};
    D.uds.session=UDS_SESSION_PROGRAMMING; D.uds.unlocked=1u;
    receive(first,sizeof first); receive(next,sizeof next);
}
static void status_checks(void)
{
    const uint8_t status[]={3,0x22,0x02,0};
    const uint8_t flow[]={0x30,0,0};
    uint32_t i;
    fresh(); ota_stm32_diag_init(&D,&P); M.eeprom_delay_ms=100u;
    receive(status,sizeof status);
    CHECK(D.long_announced && M.sent_count==1u && M.sent[0].data[3]==0x78u);
    CHECK(!ota_stm32_diag_poll(&D));
    CHECK(M.sent_count==2u && M.sent[1].data[0]==0x10u && M.sent[1].data[1]==43u);
    receive(flow,sizeof flow);
    for(i=0u;i<8u;i++) CHECK(!ota_stm32_diag_poll(&D));
    CHECK(!isotp_busy(&D.link) && !D.request_pending);
    CHECK(!M.ee_writes && !M.nor_writes && !M.nor_erases && !M.sectors);
}
static void pending_checks(void)
{
    uint32_t i,pending;
    fresh(); ota_stm32_diag_init(&D,&P);
    M.nor_erase_delay_ms=40u;
    queue_download();
#if OTA_ENABLE_FLASHING
    CHECK(D.long_announced && !M.nor_erases && !M.ee_writes);
    CHECK(M.sent_count==2u && M.sent[1].data[1]==0x7Fu && M.sent[1].data[3]==0x78u);
    M.hardware_idle=0; CHECK(!ota_stm32_diag_poll(&D) && !M.nor_erases);
    M.hardware_idle=1; CHECK(!ota_stm32_diag_poll(&D));
    CHECK(M.nor_erases==EXT_IMAGE_SLOT_SIZE/EXT_ERASE_SIZE);
    CHECK(M.first_erase_sent_count>=2u && D.uds.dl_active && !D.request_pending);
    CHECK(M.sent[M.sent_count-1u].data[1]==0x74u);
    pending=0u;
    for(i=0u;i<M.sent_count;i++) {
        if(M.sent[i].data[0]==3u && M.sent[i].data[1]==0x7Fu && M.sent[i].data[3]==0x78u)
            pending++;
        if(i>1u) CHECK((uint32_t)(M.sent_at[i]-M.sent_at[i-1u])<5000u);
    }
    CHECK(pending>=10u && D.pending_count==pending);
    CHECK(D.max_service_gap_ms==40u && !D.service_deadline_misses);
    CHECK(D.programming_allowed && D.uds.last_request_ms==M.now);
    CHECK(!ota_stm32_diag_poll(&D) && D.uds.session==UDS_SESSION_PROGRAMMING);
    CHECK(!M.sectors && !M.programs); /* never touched internal APP */

    fresh(); ota_stm32_diag_init(&D,&P); queue_download();
    M.hardware_idle=0; M.now=1001u;
    CHECK(ota_stm32_diag_poll(&D)<0 && !M.ee_writes && !M.nor_erases);
    CHECK(!D.programming_allowed && !D.request_pending);

    fresh(); ota_stm32_diag_init(&D,&P); queue_download();
    M.nor_erase_delay_ms=1001u;
    CHECK(ota_stm32_diag_poll(&D)<0);
    CHECK(M.nor_erases==1u && D.service_deadline_misses==1u);
    CHECK(!D.programming_allowed && !D.job_running && !M.sectors);
    CHECK(M.sent[M.sent_count-1u].data[3]==0x78u); /* no success after missed bound */
    {
        const uint8_t write[]={4,0x31,1,0,1};
        const uint8_t version[]={3,0x22,0xF1,0x86};
        receive(write,sizeof write);
        CHECK(M.sent[M.sent_count-1u].data[3]==0x22u); /* disabled writes still answer */
        receive(version,sizeof version);
        CHECK(M.sent[M.sent_count-1u].data[1]==0x62u); /* reads remain available */
    }

    fresh(); ota_stm32_diag_init(&D,&P); queue_download();
    M.nor_erase_delay_ms=40u; M.fail_can_after=(int)M.sent_count;
    CHECK(ota_stm32_diag_poll(&D)<0);
    CHECK(!D.programming_allowed && M.nor_erases<EXT_IMAGE_SLOT_SIZE/EXT_ERASE_SIZE);
    CHECK(!M.sectors && !M.programs);

    fresh(); M.now=0xFFFFFF00u; ota_stm32_diag_init(&D,&P);
    M.nor_erase_delay_ms=40u; queue_download();
    CHECK(!ota_stm32_diag_poll(&D) && D.uds.dl_active && D.pending_count>=10u);
    CHECK(!D.service_deadline_misses && D.uds.session==UDS_SESSION_PROGRAMMING);

    /* Exercise delayed image CRC validation and EEPROM commit through the
     * actual 0x37/0x31 dispatcher, without pretending these bytes execute. */
    {
        metadata_t m;
        image_header_t h;
        const uint8_t exit_request[]={1,0x37};
        const uint8_t verify_suppressed[]={4,0x31,0x81,0,1};
        fresh(); memset(&m,0,sizeof m); m.state=UPDATE_DOWNLOADING;
        CHECK(!metadata_save(&P.store,&m));
        memset(M.nor+IMAGE_HEADER_SIZE,0xA5,16u);
        put_le(M.nor+IMAGE_HEADER_SIZE,0x20020000u);
        put_le(M.nor+IMAGE_HEADER_SIZE+4u,APP_BASE_ADDR+9u);
        memset(&h,0,sizeof h); h.magic=IMAGE_MAGIC; h.version=1u; h.image_size=16u;
        h.payload_crc=crc32_calc(M.nor+IMAGE_HEADER_SIZE,16u); h.load_address=APP_BASE_ADDR;
        h.flags=IMAGE_FLAG_SIGNED;
        image_header_finalize(&h); image_header_encode(&h,M.nor);
        ed25519_sign(M.nor+IMAGE_HEADER_SIZE+16u,M.nor,IMAGE_HEADER_SIZE+16u,demo_pub,demo_priv);
        ota_stm32_diag_init(&D,&P); D.uds.session=UDS_SESSION_PROGRAMMING;
        D.uds.unlocked=1u; D.uds.dl_active=1u;
        D.uds.dl_size=D.uds.dl_written=IMAGE_HEADER_SIZE+16u+OTA_SIGNATURE_SIZE;
        M.nor_read_delay_ms=450u; M.eeprom_delay_ms=20u;
        receive(exit_request,sizeof exit_request);
        CHECK(!ota_stm32_diag_poll(&D));
        CHECK(M.sent[M.sent_count-1u].data[1]==0x77u && D.pending_count>=2u);
        CHECK(metadata_load(&P.store,&m)==1 && m.state==UPDATE_READY && m.pending_version==1u);
        pending=D.pending_count;
        receive(verify_suppressed,sizeof verify_suppressed);
        CHECK(!ota_stm32_diag_poll(&D));
        CHECK(M.sent[M.sent_count-1u].data[1]==0x71u && D.pending_count>pending);
        CHECK(D.uds.session==UDS_SESSION_PROGRAMMING && !M.reset_count && !M.sectors);
    }
#else
    (void)i; (void)pending;
    CHECK(!D.long_announced && !M.nor_erases && !M.ee_writes);
    CHECK(M.sent[M.sent_count-1u].data[1]==0x7Fu && M.sent[M.sent_count-1u].data[3]==0x22u);
#endif
}
int main(void)
{
    init_sign_key();
    storage_checks(); can_checks(); diagnostics_checks(); handoff_checks(); pending_checks(); status_checks();
    printf("STM32 adapter mocks: %d checks, %d failures\n",checks,failures);
    return failures?1:0;
}
