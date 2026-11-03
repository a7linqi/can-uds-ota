/* Host ECU: real C ISO-TP/UDS/boot/storage core, line-based CAN adapter.
 * The state machine runs; application instructions are never executed.
 * Control commands below are a SIMULATION test fixture, not UDS services. */
#include "ota/isotp.h"
#include "ota/uds.h"
#include "ota/boot.h"
#include "flash_ram.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

typedef struct {
    uint8_t internal_bytes[FLASH_TOTAL_SIZE],nor_bytes[EXT_FLASH_TOTAL_SIZE];
    flash_ram_t internal,nor;
    eeprom_ram_t eeprom;
    storage_fault_t fault;
    metadata_store_t store;
    boot_context_t boot;
    ota_port_t port;
    isotp_link_t link;
    uds_server_t uds;
    uint8_t request[ISOTP_MAX_PAYLOAD],response[512];
    uint32_t now;
    boot_target_t target;
} ecu_t;
static ecu_t E;
static uint32_t clock_ms(void *user) { (void)user; return E.now; }
static int transmit(void *user,const can_frame_t *f)
{
    uint32_t i;
    (void)user;
    if(E.fault.powered_off) return -1;
    printf("CAN %u %lX %u ",(unsigned)E.now,(unsigned long)f->id,(unsigned)f->extended);
    for(i=0u;i<f->dlc;i++) printf("%02X",f->data[i]);
    puts("");
    return 0;
}
static void report_state(void)
{
    metadata_t m;
    int status=metadata_load(&E.store,&m);
    if(status!=1) memset(&m,0,sizeof m);
    printf("STATE %d %u %u %u %u %d\n",(int)E.target,(unsigned)m.state,
           (unsigned)m.active_version,(unsigned)m.pending_version,
           (unsigned)m.boot_attempts,status);
}
static void reboot(void)
{
    storage_fault_reset(&E.fault);
    flash_ram_attach(&E.internal,E.internal_bytes,FLASH_TOTAL_SIZE,0,&E.fault);
    flash_ram_attach(&E.nor,E.nor_bytes,EXT_FLASH_TOTAL_SIZE,1,&E.fault);
    E.eeprom.fault=&E.fault; E.eeprom.read_fail_after=-1;
    E.eeprom.ignore_writes=0; E.eeprom.write_ops=0u;
    metadata_store_init(&E.store,eeprom_ram_port(&E.eeprom));
    boot_context_init(&E.boot,flash_ram_port(&E.internal),flash_ram_port(&E.nor),&E.store);
    E.port.can_send=transmit; E.port.now_ms=clock_ms; E.port.user=0;
    isotp_init(&E.link,&E.port,0x7E8u,0x7E0u);
    isotp_set_flow(&E.link,8u,0u);
    uds_init(&E.uds,&E.boot,&E.port);
    E.target=boot_select(&E.boot,0);
    report_state();
}
static void pump(void)
{
    uint32_t i;
    if(E.fault.powered_off) return;
    for(i=0u;i<4096u;i++) {
        int event;
        uds_poll(&E.uds);
        event=isotp_poll(&E.link);
        if(event<0) break;
        if(event==1) {
            uint16_t len=0u,out;
            if(isotp_read(&E.link,E.request,sizeof E.request,&len)!=1) break;
            out=uds_handle(&E.uds,E.request,len,E.response,sizeof E.response);
            if(E.fault.powered_off) break; /* device disappears before reply */
            if(out) (void)isotp_send(&E.link,E.response,out);
        }
        if(E.uds.reset_requested && !isotp_busy(&E.link)) {
            /* transmit() already emitted the complete response. */
            reboot(); break;
        }
        if(E.link.tx_state!=ISOTP_SENDING_CF) break;
        E.now++;
    }
}
static int hex_value(char c)
{
    if(c>='0'&&c<='9') return c-'0';
    if(c>='A'&&c<='F') return c-'A'+10;
    if(c>='a'&&c<='f') return c-'a'+10;
    return -1;
}
int main(void)
{
    char line[160],hex[32],extra;
    unsigned stamp,extended;
    unsigned long id;
    setvbuf(stdout,0,_IONBF,0);
    memset(&E,0,sizeof E);
    memset(E.internal_bytes,0xFF,sizeof E.internal_bytes);
    memset(E.nor_bytes,0xFF,sizeof E.nor_bytes);
    memset(E.eeprom.mem,0xFF,sizeof E.eeprom.mem);
    E.now=1u; reboot(); puts("READY");
    while(fgets(line,sizeof line,stdin)) {
        if(!strchr(line,'\n') && !feof(stdin)) {
            int c; while((c=getchar())!='\n' && c!=EOF) {}
            puts("ERROR line-too-long");
        } else if(line[0]=='F' && sscanf(line,"F %u %lx %u %31s %c",&stamp,&id,&extended,hex,&extra)==4) {
            can_frame_t f;
            size_t len=strlen(hex),i;
            int good=len>0u && len<=16u && len%2u==0u && extended<=1u &&
                     id<=(extended?0x1FFFFFFFul:0x7FFul);
            memset(&f,0,sizeof f);
            for(i=0u;good && i<len/2u;i++) {
                int a=hex_value(hex[2u*i]),b=hex_value(hex[2u*i+1u]);
                if(a<0 || b<0) good=0;
                else f.data[i]=(uint8_t)((a<<4)|b);
            }
            if(good) {
                f.id=(uint32_t)id; f.extended=extended!=0u; f.dlc=(uint8_t)(len/2u);
                E.now=stamp;
                if(!E.fault.powered_off && !f.extended && f.id==0x7E0u) { isotp_on_frame(&E.link,&f); pump(); }
            } else puts("ERROR malformed-frame");
        } else if(sscanf(line,"T %u %c",&stamp,&extra)==1) {
            E.now=stamp; pump();
        } else if(strcmp(line,"REBOOT\n")==0) {
            reboot();
        } else if(strcmp(line,"HEALTH\n")==0) {
            printf("RESULT %d\n",boot_confirm(&E.boot)); report_state();
        } else if(strcmp(line,"STATE\n")==0) {
            report_state();
        } else if(strcmp(line,"STATS\n")==0) {
            printf("STATS %u %u %u\n",(unsigned)E.nor.write_ops,
                   (unsigned)E.internal.write_ops,(unsigned)E.eeprom.write_ops);
        } else if(strncmp(line,"FAULT ",6u)==0) {
            char *end;
            long budget=strtol(line+6,&end,10);
            if((*end=='\n'||*end=='\0') && budget>=-1) {
                E.fault.remaining=budget;
            } else puts("ERROR malformed-budget");
        } else if(strcmp(line,"EXIT\n")==0) {
            puts("BYE"); return 0;
        } else puts("ERROR unknown-command");
        printf("DONE %u\n",(unsigned)E.now);
    }
    return 0;
}
