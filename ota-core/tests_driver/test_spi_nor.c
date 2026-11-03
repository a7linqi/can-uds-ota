/* SPI command-level chip fixture, independent of the driver's decisions.
 * Array mutations begin on /CS rising; BUSY clears only after model time. */
#include "ota/spi_nor.h"
#include "ota/uds.h"
#include "ota/image.h"
#include "ota/crc32.h"
#include "ota/signature.h"
#include "flash_ram.h"
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
typedef struct {
    uint8_t memory[NOR_CAPACITY_BYTES],id[3],sr1,sr2,sr3;
    uint8_t command,buffer[256];
    uint32_t position,address,data_size,now,busy_start,busy_ms;
    uint32_t writes,erases,wren,transfers,services,selected,released;
    int active,fail_transfer,fail_select,ignore_wren,ignore_write,ignore_erase;
    int stuck_busy,stopped_clock,service_fail;
} chip_t;
static chip_t C;
static nor_spi_t D;
static nor_spi_bus_t bus;
static int busy(void)
{ return C.stuck_busy || (uint32_t)(C.now-C.busy_start)<C.busy_ms; }
static int select_chip(void *user,int active)
{
    uint32_t i;
    (void)user;
    if(active) {
        C.selected++;
        if(C.fail_select) return -1;
        if(C.active) return -1;
        C.active=1; C.command=0; C.position=0u; C.address=0u; C.data_size=0u;
    } else {
        C.released++;
        if(C.active) {
            if(C.command==0x06u && !C.ignore_wren && !busy()) { C.sr1|=2u; C.wren++; }
            if((C.sr1&2u) && !busy()) {
                if(C.command==0x02u && C.position>=5u && C.data_size) {
                    if(!C.ignore_write) for(i=0;i<C.data_size;i++)
                        C.memory[(C.address&~255u)|((C.address+i)&255u)]&=C.buffer[i];
                    C.writes++; C.sr1&=(uint8_t)~2u; C.busy_start=C.now; C.busy_ms=3u;
                } else if(C.command==0x20u && C.position==4u) {
                    if(!C.ignore_erase) memset(C.memory+(C.address&~4095u),0xFF,4096u);
                    C.erases++; C.sr1&=(uint8_t)~2u; C.busy_start=C.now; C.busy_ms=40u;
                }
            }
        }
        C.active=0;
    }
    return 0;
}
static int transfer(void *user,const uint8_t *tx,uint8_t *rx,uint32_t n,uint32_t timeout)
{
    uint32_t i;
    (void)user;
    if(!C.active || !timeout || (int)C.transfers++==C.fail_transfer) return -1;
    for(i=0;i<n;i++) {
        uint8_t input=tx?tx[i]:0xFFu,output=0xFFu;
        if(!C.position) C.command=input;
        else if(C.command==0x9Fu) output=C.id[(C.position-1u)%3u];
        else if(C.command==0x05u) output=(uint8_t)(C.sr1|(busy()?1u:0u));
        else if(C.command==0x35u) output=C.sr2;
        else if(C.command==0x15u) output=C.sr3;
        else if(C.command==0x03u || C.command==0x02u || C.command==0x20u) {
            if(C.position<=3u) C.address=(C.address<<8)|input;
            else if(C.command==0x03u) output=C.memory[(C.address+C.position-4u)%NOR_CAPACITY_BYTES];
            else if(C.command==0x02u) {
                if(C.data_size>=sizeof C.buffer) return -1;
                C.buffer[C.data_size++]=input;
            }
        }
        if(rx) rx[i]=output;
        C.position++;
    }
    return 0;
}
static uint32_t now_ms(void *user) { (void)user; return C.now; }
static int wait_ms(void *user,uint32_t ms) { (void)user; if(!C.stopped_clock) C.now+=ms; return 0; }
static int service(void *user) { (void)user; C.services++; return C.service_fail?-1:0; }
static void fresh(void)
{
    memset(&C,0,sizeof C); memset(C.memory,0xFF,sizeof C.memory);
    C.id[0]=0xEFu; C.id[1]=0x40u; C.id[2]=0x18u; C.fail_transfer=-1;
    memset(&bus,0,sizeof bus); bus.select=select_chip; bus.transfer=transfer;
    bus.now_ms=now_ms; bus.wait_ms=wait_ms; bus.service=service;
    CHECK(!nor_spi_init(&D,&bus,0));
}
static void successful_io(void)
{
    uint8_t data[256],out[512],next[256];
    uint32_t i,writes;
    fresh(); CHECK(!nor_spi_probe(&D));
    CHECK(D.ready && D.jedec_id==NOR_JEDEC_ID && D.capacity_bytes==NOR_CAPACITY_BYTES);
    CHECK(!C.wren && !C.writes && !C.erases && !C.active);
    for(i=0;i<256u;i++) { data[i]=(uint8_t)i; next[i]=(uint8_t)(255u-i); }
    CHECK(!nor_spi_program_page(&D,0x100u,data,sizeof data));
    CHECK(!C.active && C.writes==1u && C.now>=3u && !(C.sr1&2u));
    CHECK(!nor_spi_program_page(&D,0x200u,next,sizeof next));
    CHECK(!nor_spi_read(&D,0x100u,out,sizeof out));
    CHECK(!memcmp(out,data,256u) && !memcmp(out+256u,next,256u));
    writes=C.writes;
    CHECK(!nor_spi_program_page(&D,0x100u,data,sizeof data) && C.writes==writes);
    CHECK(!nor_spi_erase_sector(&D,0u));
    CHECK(C.erases==1u && C.services>40u && !C.active);
    CHECK(!nor_spi_read(&D,0x100u,out,sizeof out));
    for(i=0;i<sizeof out;i++) CHECK(out[i]==0xFFu);
    CHECK(!nor_spi_program_page(&D,NOR_CAPACITY_BYTES-1u,data,1u));
    CHECK(C.memory[NOR_CAPACITY_BYTES-1u]==data[0]);
}
static void bounds_and_protection(void)
{
    uint8_t data[256],out[2];
    uint32_t transfers;
    fresh(); CHECK(!nor_spi_probe(&D)); memset(data,0x12,sizeof data); transfers=C.transfers;
    CHECK(nor_spi_program_page(&D,255u,data,2u)==NOR_ERR_ARGUMENT);
    CHECK(nor_spi_program_page(&D,0u,data,0u)==NOR_ERR_ARGUMENT);
    CHECK(nor_spi_read(&D,NOR_CAPACITY_BYTES-1u,out,2u)==NOR_ERR_ARGUMENT);
    CHECK(nor_spi_read(&D,0xFFFFFFFFu,out,2u)==NOR_ERR_ARGUMENT);
    CHECK(nor_spi_erase_sector(&D,1u)==NOR_ERR_ARGUMENT);
    CHECK(nor_spi_erase_sector(&D,NOR_CAPACITY_BYTES)==NOR_ERR_ARGUMENT);
    CHECK(C.transfers==transfers && !C.writes && !C.erases);
    CHECK(!nor_spi_program_page(&D,0u,data,1u)); data[0]=0xFFu;
    CHECK(nor_spi_program_page(&D,0u,data,1u)==NOR_ERR_ERASE_REQUIRED && C.writes==1u);
    C.sr1=4u;
    CHECK(nor_spi_erase_sector(&D,0u)==NOR_ERR_PROTECTED && !C.erases);
    C.sr1=0u; C.sr2=0x40u;
    CHECK(nor_spi_erase_sector(&D,0u)==NOR_ERR_PROTECTED && !C.erases);
    C.sr2=0u; C.sr3=4u;
    CHECK(nor_spi_erase_sector(&D,0u)==NOR_ERR_PROTECTED && !C.erases);
    CHECK(!nor_spi_read(&D,0u,out,1u)); /* protection never auto-cleared */
    CHECK(C.sr3==4u && D.ready);
}
static void faults(void)
{
    uint8_t data[4]={1u,2u,3u,4u},out[4];
    uint32_t operations,start;
    int i;
    fresh(); C.id[0]=0u; CHECK(nor_spi_probe(&D)==NOR_ERR_ID && !D.ready && !C.wren);
    fresh(); C.sr2=0x80u; CHECK(nor_spi_probe(&D)==NOR_ERR_PROTECTED && !D.ready);
    fresh(); C.fail_select=1; CHECK(nor_spi_probe(&D)==NOR_ERR_IO && !C.active && C.released>=2u);
    fresh(); CHECK(!nor_spi_probe(&D)); C.ignore_wren=1;
    CHECK(nor_spi_program_page(&D,0u,data,4u)==NOR_ERR_WEL && !D.ready && !C.writes);
    fresh(); CHECK(!nor_spi_probe(&D)); C.ignore_write=1;
    CHECK(nor_spi_program_page(&D,0u,data,4u)==NOR_ERR_VERIFY && !D.ready && !C.active);
    fresh(); CHECK(!nor_spi_probe(&D)); C.memory[1]=0u; C.ignore_erase=1;
    CHECK(nor_spi_erase_sector(&D,0u)==NOR_ERR_VERIFY && !D.ready);
    fresh(); CHECK(!nor_spi_probe(&D)); C.stuck_busy=1;
    CHECK(nor_spi_read(&D,0u,out,4u)==NOR_ERR_TIMEOUT && !D.ready && C.now<=1001u);
    fresh(); CHECK(!nor_spi_probe(&D)); C.stuck_busy=1; C.stopped_clock=1;
    CHECK(nor_spi_read(&D,0u,out,4u)==NOR_ERR_TIMEOUT && !D.ready && C.transfers<3000u);
    fresh(); CHECK(!nor_spi_probe(&D)); C.now=0xFFFFFFFEu;
    CHECK(!nor_spi_program_page(&D,0u,data,4u) && C.now<10u); /* WIP across wrap */
    fresh(); CHECK(!nor_spi_probe(&D)); C.service_fail=1;
    CHECK(nor_spi_program_page(&D,0u,data,4u)==NOR_ERR_SERVICE && !D.ready && !C.writes);
    fresh(); CHECK(!nor_spi_probe(&D)); start=C.transfers;
    CHECK(!nor_spi_program_page(&D,0u,data,4u)); operations=C.transfers-start;
    printf("SPI transfer failure points: %u\n",(unsigned)operations);
    for(i=0;i<(int)operations;i++) {
        fresh(); CHECK(!nor_spi_probe(&D)); C.fail_transfer=(int)C.transfers+i;
        CHECK(nor_spi_program_page(&D,0u,data,4u)==NOR_ERR_IO);
        CHECK(!D.ready && !C.active);
    }
    fresh(); bus.transfer=0;
    CHECK(nor_spi_init(&D,&bus,0)==NOR_ERR_ARGUMENT);
    CHECK(nor_spi_probe(&D)==NOR_ERR_ARGUMENT);
    fresh(); {
        nor_spi_limits_t invalid={1001u,20u,1000u,1000u};
        CHECK(nor_spi_init(&D,&bus,&invalid)==NOR_ERR_ARGUMENT);
        CHECK(nor_spi_probe(&D)==NOR_ERR_ARGUMENT);
    }
}
static uint8_t internal_bytes[FLASH_TOTAL_SIZE];
static flash_ram_t internal;
static eeprom_ram_t eeprom;
static storage_fault_t storage_fault;
static metadata_store_t store;
static boot_context_t boot;
static uds_server_t server;
static int bridge_read(void *user,uint32_t off,uint8_t *out,uint32_t n)
{
    (void)user;
    if(off>EXT_FLASH_TOTAL_SIZE || n>EXT_FLASH_TOTAL_SIZE-off) return -1;
    return nor_spi_read(&D,off,out,n);
}
static int bridge_write(void *user,uint32_t off,const uint8_t *data,uint32_t n)
{
    (void)user;
    if(off>EXT_FLASH_TOTAL_SIZE || n>EXT_FLASH_TOTAL_SIZE-off) return -1;
    while(n) {
        uint32_t count=256u-off%256u;
        if(count>n) count=n;
        if(nor_spi_program_page(&D,off,data,count)) return -1;
        off+=count; data+=count; n-=count;
    }
    return 0;
}
static int bridge_erase(void *user,uint32_t off,uint32_t n)
{
    (void)user;
    if(off>EXT_FLASH_TOTAL_SIZE || n>EXT_FLASH_TOTAL_SIZE-off || off%4096u || n%4096u)
        return -1;
    while(n) { if(nor_spi_erase_sector(&D,off)) return -1; off+=4096u; n-=4096u; }
    return 0;
}
static void put_le(uint8_t *p,uint32_t v)
{ p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8); p[2]=(uint8_t)(v>>16); p[3]=(uint8_t)(v>>24); }
static int download(uint32_t version)
{
    uint8_t image[IMAGE_HEADER_SIZE+16u+OTA_SIGNATURE_SIZE],request[64],response[64];
    image_header_t header;
    uint16_t n;
    memset(&header,0,sizeof header); memset(image,0xA5,sizeof image);
    put_le(image+32,0x20020000u); put_le(image+36,APP_BASE_ADDR+9u);
    image[47]=(uint8_t)version;
    header.magic=IMAGE_MAGIC; header.version=version; header.image_size=16u;
    header.payload_crc=crc32_calc(image+32,16u); header.load_address=APP_BASE_ADDR;
    header.flags=IMAGE_FLAG_SIGNED;
    image_header_finalize(&header); image_header_encode(&header,image);
    ed25519_sign(image+IMAGE_HEADER_SIZE+16u,image,IMAGE_HEADER_SIZE+16u,demo_pub,demo_priv);
    /* Session/unlock are core-tested elsewhere; this case tests storage composition. */
    server.session=UDS_SESSION_PROGRAMMING; server.unlocked=1u;
    server.last_request_ms=C.now;
    memset(request,0,sizeof request); request[0]=0x34u; request[2]=0x44u;
    request[3]=8u; request[4]=1u; request[10]=(uint8_t)sizeof image;
    n=uds_handle(&server,request,11u,response,sizeof response);
    CHECK(n==4u && response[0]==0x74u);
    if(n!=4u) return -1;
    request[0]=0x36u; request[1]=1u; memcpy(request+2,image,sizeof image);
    n=uds_handle(&server,request,(uint16_t)(sizeof image+2u),response,sizeof response);
    CHECK(n==2u && response[0]==0x76u);
    if(n!=2u) return -1;
    request[0]=0x37u;
    n=uds_handle(&server,request,1u,response,sizeof response);
    CHECK(n==1u && response[0]==0x77u);
    return n==1u?0:-1;
}
static void core_integration(void)
{
    flash_port_t external;
    ota_port_t clock;
    metadata_t m;
    fresh(); CHECK(!nor_spi_probe(&D));
    memset(internal_bytes,0xFF,sizeof internal_bytes); memset(&eeprom,0,sizeof eeprom);
    storage_fault_reset(&storage_fault);
    memset(eeprom.mem,0xFF,sizeof eeprom.mem); eeprom.read_fail_after=-1; eeprom.fault=&storage_fault;
    flash_ram_attach(&internal,internal_bytes,sizeof internal_bytes,0,&storage_fault);
    metadata_store_init(&store,eeprom_ram_port(&eeprom));
    external.read=bridge_read; external.write=bridge_write; external.erase=bridge_erase; external.user=0;
    boot_context_init(&boot,flash_ram_port(&internal),external,&store);
    memset(&clock,0,sizeof clock); clock.now_ms=now_ms;
    uds_init(&server,&boot,&clock);
    if(download(1u)) return;
    CHECK(C.now>=10000u); /* real core crossed S3 during synchronous erase */
    CHECK(boot_select(&boot,&m)==BOOT_TARGET_APP && m.state==UPDATE_TRIAL);
    CHECK(!boot_confirm(&boot));
    if(download(2u)) return;
    CHECK(boot_select(&boot,&m)==BOOT_TARGET_APP && m.pending_version==2u);
    CHECK(boot_select(&boot,&m)==BOOT_TARGET_APP);
    CHECK(boot_select(&boot,&m)==BOOT_TARGET_APP);
    CHECK(boot_select(&boot,&m)==BOOT_TARGET_APP && m.active_version==1u && m.state==UPDATE_IDLE);
    CHECK(internal_bytes[APP_OFFSET+15u]==1u && C.memory[BACKUP_OFFSET+47u]==1u);
    CHECK(metadata_load(&store,&m)==1 && m.min_version==1u);
    printf("NOR/core integration: virtual_ms=%u, programs=%u, sectors=%u\n",
           (unsigned)C.now,(unsigned)C.writes,(unsigned)C.erases);
}
int main(void)
{
    setvbuf(stdout,0,_IONBF,0);
    init_sign_key();
    successful_io(); bounds_and_protection(); faults(); core_integration();
    printf("SPI NOR protocol: %d checks, %d failures\n",checks,failures);
    return failures?1:0;
}
