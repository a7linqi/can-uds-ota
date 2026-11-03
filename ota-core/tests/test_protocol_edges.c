#include "bench.h"
#include "test_util.h"
#include <string.h>

typedef struct {
    isotp_link_t link;
    ota_port_t port;
    uint32_t now;
    int fail_next;
    uint32_t sent;
    can_frame_t last;
} wire_t;
static wire_t W;
static bench_t B;
static const uint8_t payload[30]={1u,2u,3u,4u,5u,6u,7u,8u};
static int wire_send(void *user,const can_frame_t *frame)
{
    wire_t *w=(wire_t *)user;
    if(w->fail_next) { w->fail_next=0; return -1; }
    w->last=*frame; w->sent++;
    return 0;
}
static uint32_t wire_now(void *user) { return ((wire_t *)user)->now; }
static void fresh(void)
{
    memset(&W,0,sizeof W);
    W.port.can_send=wire_send; W.port.now_ms=wire_now; W.port.user=&W;
    isotp_init(&W.link,&W.port,0x7E0u,0x7E8u);
}
static void feed(uint8_t a,uint8_t b,uint8_t c)
{
    can_frame_t f;
    memset(&f,0,sizeof f); f.id=0x7E8u; f.dlc=3u;
    f.data[0]=a; f.data[1]=b; f.data[2]=c;
    isotp_on_frame(&W.link,&f);
}
static void send_failure_checks(void)
{
    fresh(); W.fail_next=1;
    TEST_ASSERT(isotp_send(&W.link,payload,3u)==ISOTP_ERR_SEND);
    TEST_ASSERT(W.link.tx_error==ISOTP_ERR_SEND && !isotp_busy(&W.link));
    TEST_ASSERT(isotp_send(&W.link,payload,3u)==0);
    TEST_ASSERT(W.link.tx_error==0);
    fresh(); W.fail_next=1;
    TEST_ASSERT(isotp_send(&W.link,payload,20u)==ISOTP_ERR_SEND);
    TEST_ASSERT(W.link.tx_error==ISOTP_ERR_SEND && !isotp_busy(&W.link));
    TEST_ASSERT(isotp_send(&W.link,payload,20u)==0);
    feed(0x30u,0u,0u); W.fail_next=1;
    TEST_ASSERT(isotp_poll(&W.link)==ISOTP_ERR_SEND);
    TEST_ASSERT(W.link.tx_error==ISOTP_ERR_SEND && !isotp_busy(&W.link));
    TEST_ASSERT(isotp_send(&W.link,payload,3u)==0);
    fresh(); W.fail_next=1;
    feed(0x10u,20u,0xAAu);
    TEST_ASSERT(W.link.rx_state==ISOTP_IDLE && W.link.rx_error==ISOTP_ERR_SEND);
}
static void flow_checks(void)
{
    unsigned i;
    fresh(); TEST_ASSERT(!isotp_send(&W.link,payload,20u));
    for(i=0u;i<3u;i++) {
        feed(0x31u,0u,0u);
        TEST_ASSERT(W.link.tx_state==ISOTP_WAIT_FC);
    }
    feed(0x31u,0u,0u);
    TEST_ASSERT(W.link.tx_state==ISOTP_IDLE && W.link.tx_error==ISOTP_ERR_FLOW);
    fresh(); TEST_ASSERT(!isotp_send(&W.link,payload,20u));
    feed(0x30u,0u,5u);
    feed(0x31u,0u,0u); /* unsolicited while CF transmission is active */
    TEST_ASSERT(W.link.tx_state==ISOTP_SENDING_CF);
    feed(0x30u,7u,127u);
    TEST_ASSERT(W.link.fc_bs==0u && W.link.fc_stmin==5u);
    for(i=0xF1u;i<=0xF9u;i++) {
        fresh(); TEST_ASSERT(!isotp_send(&W.link,payload,8u));
        feed(0x30u,0u,(uint8_t)i);
        TEST_ASSERT(W.link.fc_stmin==1u); /* millisecond clock rounds up */
        TEST_ASSERT(!isotp_poll(&W.link) && W.sent==1u);
        W.now=1u;
        TEST_ASSERT(!isotp_poll(&W.link) && W.sent==2u && W.last.data[0]==0x21u);
    }
    fresh(); TEST_ASSERT(!isotp_send(&W.link,payload,20u));
    feed(0x30u,0u,0x80u);
    TEST_ASSERT(W.link.tx_state==ISOTP_IDLE && W.link.tx_error==ISOTP_ERR_FLOW);
}
static void receive_checks(void)
{
    can_frame_t frame;
    uint8_t out[8]; uint16_t size=0u;
    fresh(); memset(&frame,0,sizeof frame);
    frame.id=0x7E8u; frame.extended=1u; frame.dlc=2u;
    frame.data[0]=1u; frame.data[1]=0xABu;
    isotp_on_frame(&W.link,&frame);
    TEST_ASSERT(!W.link.rx_done);
    frame.extended=0u; isotp_on_frame(&W.link,&frame);
    frame.data[1]=0xCDu; isotp_on_frame(&W.link,&frame);
    TEST_ASSERT(isotp_read(&W.link,out,sizeof out,&size)==1);
    TEST_ASSERT(size==1u && out[0]==0xABu); /* don't overwrite unread data */
    fresh(); feed(0x1Fu,0xFFu,0u);
    TEST_ASSERT(W.sent==1u && W.last.data[0]==0x32u);
    TEST_ASSERT(W.link.rx_state==ISOTP_IDLE && W.link.rx_error==ISOTP_ERR_OVERFLOW);
    feed(1u,0xABu,0u);
    TEST_ASSERT(W.link.rx_done && W.link.rx_error==0);
}
static void timer_checks(void)
{
    fresh(); W.now=0xFFFFFF00u;
    TEST_ASSERT(!isotp_send(&W.link,payload,20u));
    W.now+=1000u; TEST_ASSERT(!isotp_poll(&W.link));
    W.now++; TEST_ASSERT(isotp_poll(&W.link)==ISOTP_ERR_TIMEOUT);
    fresh(); W.now=0xFFFFFF00u; feed(0x10u,20u,0xABu);
    W.now+=1001u; TEST_ASSERT(isotp_poll(&W.link)==ISOTP_ERR_TIMEOUT);
}
static void lockout_checks(uint32_t start)
{
    uint8_t request[6]={0x27u,1u,0u,0u,0u,0u},response[16];
    uint32_t elapsed;
    unsigned i;
    bench_init(&B); B.now_ms=start;
    for(i=0u;i<3u;i++) {
        request[1]=1u;
        TEST_ASSERT(uds_handle(&B.server,request,2u,response,sizeof response)==6u);
        request[1]=2u;
        t_put_be32(request+2,uds_calc_key(t_get_be32(response+2))^1u);
        TEST_ASSERT(uds_handle(&B.server,request,6u,response,sizeof response)==3u);
    }
    request[1]=1u;
    for(elapsed=0u;elapsed<10000u;elapsed+=4999u) {
        B.now_ms=start+elapsed;
        TEST_ASSERT(uds_handle(&B.server,request,2u,response,sizeof response)==3u);
        TEST_ASSERT(response[2]==0x36u);
    }
    B.now_ms=start+10000u;
    TEST_ASSERT(uds_handle(&B.server,request,2u,response,sizeof response)==6u);
    TEST_ASSERT(response[0]==0x67u);
    B.now_ms=0xFFFFFF00u; B.server.last_request_ms=B.now_ms;
    B.server.session=UDS_SESSION_PROGRAMMING; B.server.unlocked=1u;
    B.now_ms+=UDS_S3_TIMEOUT_MS+1u; uds_poll(&B.server);
    TEST_ASSERT(B.server.session==UDS_SESSION_DEFAULT && !B.server.unlocked);
}
void run_protocol_edge_tests(void)
{
    puts("[protocol edges]");
    send_failure_checks(); flow_checks(); receive_checks(); timer_checks();
    lockout_checks(0xFFFFFF00u); lockout_checks(0u);
}
