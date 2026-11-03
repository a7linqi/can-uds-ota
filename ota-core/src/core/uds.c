#include "ota/uds.h"
#include "ota/boot.h"
#include "ota/flash_layout.h"
#include "ota/image.h"

#include <string.h>

/* ---- negative response codes (ISO 14229-1) ---- */
#define NRC_SERVICE_NOT_SUPPORTED       0x11u
#define NRC_SUBFUNCTION_NOT_SUPPORTED   0x12u
#define NRC_INCORRECT_LENGTH            0x13u
#define NRC_CONDITIONS_NOT_CORRECT      0x22u
#define NRC_REQUEST_SEQUENCE_ERROR      0x24u
#define NRC_REQUEST_OUT_OF_RANGE        0x31u
#define NRC_SECURITY_ACCESS_DENIED      0x33u
#define NRC_INVALID_KEY                 0x35u
#define NRC_EXCEEDED_ATTEMPTS           0x36u
#define NRC_GENERAL_PROGRAMMING_FAILURE 0x72u

static const uint8_t VIN_VALUE[]  = "LOTA0000000000001"; /* 17 chars */
static const uint8_t PART_VALUE[] = "OTA-ECU-0001";
static const uint8_t SW_VALUE[]   = "0.2.0";

static uint32_t get_be32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16)
         | ((uint32_t)p[2] << 8)  | (uint32_t)p[3];
}

static void put_be32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

static uint16_t nrc_reply(uint8_t *resp, uint16_t max, uint8_t sid, uint8_t code)
{
    if (max < 3u) {
        return 0u;
    }
    resp[0] = 0x7Fu;
    resp[1] = sid;
    resp[2] = code;
    return 3u;
}

static uint32_t server_now(uds_server_t *s)
{
    if (s->port != 0 && s->port->now_ms != 0) {
        return s->port->now_ms(s->port->user);
    }
    return 0u;
}

uint32_t uds_calc_key(uint32_t seed)
{
    uint32_t k = seed ^ 0xA5A5A5A5u;

    k = (k << 7) | (k >> 25);
    k += 0x9E3779B9u;
    return k;
}

static int lockout_active(uds_server_t *s)
{
    uint32_t now;

    if (!s->lockout_active) {
        return 0;
    }
    now = server_now(s);
    if ((uint32_t)(now-s->lockout_started_ms) >= 10000u) {
        s->lockout_active = 0u;
        s->key_failures = 0u;
        return 0;
    }
    return 1;
}

/* ---- 0x10 ---- */
static uint16_t handle_session(uds_server_t *s, const uint8_t *req, uint16_t req_len,
                               uint8_t *resp, uint16_t max)
{
    uint8_t sub;

    if (req_len != 2u) {
        return nrc_reply(resp, max, 0x10u, NRC_INCORRECT_LENGTH);
    }
    sub = (uint8_t)(req[1] & 0x7Fu);
    if (sub != UDS_SESSION_DEFAULT && sub != UDS_SESSION_PROGRAMMING && sub != UDS_SESSION_EXTENDED) {
        return nrc_reply(resp, max, 0x10u, NRC_SUBFUNCTION_NOT_SUPPORTED);
    }
    s->session = sub;
    s->unlocked = 0u;
    s->seed_valid = 0u;
    s->seed = 0u;
    s->dl_active = 0u;
    if ((req[1] & 0x80u) != 0u) {
        return 0u; /* positive response suppressed */
    }
    resp[0] = 0x50u;
    resp[1] = sub;
    resp[2] = 0x00u; /* P2   = 50 ms  */
    resp[3] = 0x32u;
    resp[4] = 0x01u; /* P2* = 500 * 10 ms = 5000 ms */
    resp[5] = 0xF4u;
    return 6u;
}

/* ---- 0x11 ---- */
static uint16_t handle_ecu_reset(uds_server_t *s, const uint8_t *req, uint16_t req_len,
                                 uint8_t *resp, uint16_t max)
{
    uint8_t sub;

    if (req_len != 2u) {
        return nrc_reply(resp, max, 0x11u, NRC_INCORRECT_LENGTH);
    }
    sub = (uint8_t)(req[1] & 0x7Fu);
    if (sub != 0x01u && sub != 0x03u) {
        return nrc_reply(resp, max, 0x11u, NRC_SUBFUNCTION_NOT_SUPPORTED);
    }
    s->reset_requested = 1u;
    if ((req[1] & 0x80u) != 0u) {
        return 0u;
    }
    resp[0] = 0x51u;
    resp[1] = sub;
    return 2u;
}

/* ---- 0x22 ---- */
static int append_did(uds_server_t *s, uint16_t did, uint8_t *resp, uint16_t *pos, uint16_t max)
{
    const uint8_t *data;
    uint16_t len;
    uint8_t session = s->session;
    uint8_t status[UDS_UPDATE_STATUS_SIZE];

    switch (did) {
    case 0xF190u:
        data = VIN_VALUE;
        len = (uint16_t)(sizeof VIN_VALUE - 1u);
        break;
    case 0xF187u:
        data = PART_VALUE;
        len = (uint16_t)(sizeof PART_VALUE - 1u);
        break;
    case 0xF195u:
        data = SW_VALUE;
        len = (uint16_t)(sizeof SW_VALUE - 1u);
        break;
    case 0xF186u:
        data = &session;
        len = 1u;
        break;
    case UDS_DID_UPDATE_STATUS: {
        metadata_t m;
        int loaded;
        if((uint32_t)*pos+2u+UDS_UPDATE_STATUS_SIZE>max) return -2;
        loaded=metadata_load(s->boot->store,&m);
        if(loaded<0) return -3;
        if(!loaded) memset(&m,0,sizeof m);
        status[0]=1u; status[1]=(uint8_t)(loaded==1);
        status[2]=(uint8_t)m.state; status[3]=(uint8_t)m.boot_attempts;
        put_be32(status+4,m.active_version); put_be32(status+8,m.pending_version);
        put_be32(status+12,m.min_version); put_be32(status+16,m.active_size);
        put_be32(status+20,m.pending_size); put_be32(status+24,m.active_crc);
        put_be32(status+28,m.pending_crc); put_be32(status+32,m.seq);
        put_be32(status+36,FLASH_TOTAL_SIZE);
        data=status; len=UDS_UPDATE_STATUS_SIZE;
        break;
    }
    default:
        return -1;
    }
    if ((uint32_t)*pos + 2u + len > (uint32_t)max) {
        return -2;
    }
    resp[*pos] = (uint8_t)(did >> 8);
    (*pos)++;
    resp[*pos] = (uint8_t)(did & 0xFFu);
    (*pos)++;
    memcpy(&resp[*pos], data, len);
    *pos = (uint16_t)(*pos + len);
    return 0;
}

static uint16_t handle_read_data(uds_server_t *s, const uint8_t *req, uint16_t req_len,
                                 uint8_t *resp, uint16_t max)
{
    uint16_t count;
    uint16_t i;
    uint16_t pos = 1u;

    if (req_len < 3u || (uint16_t)((req_len - 1u) % 2u) != 0u) {
        return nrc_reply(resp, max, 0x22u, NRC_INCORRECT_LENGTH);
    }
    count = (uint16_t)((req_len - 1u) / 2u);
    resp[0] = 0x62u;
    for (i = 0u; i < count; i++) {
        uint16_t did = (uint16_t)((((uint16_t)req[1u + i * 2u]) << 8) | (uint16_t)req[2u + i * 2u]);
        int r = append_did(s, did, resp, &pos, max);
        if (r == -1) {
            return nrc_reply(resp, max, 0x22u, NRC_REQUEST_OUT_OF_RANGE);
        }
        if (r == -2 || r == -3) {
            return nrc_reply(resp, max, 0x22u, NRC_GENERAL_PROGRAMMING_FAILURE);
        }
    }
    return pos;
}

/* ---- 0x27 ---- */
static uint16_t handle_security(uds_server_t *s, const uint8_t *req, uint16_t req_len,
                                uint8_t *resp, uint16_t max)
{
    uint8_t sub;

    if (req_len < 2u || (req[1]==0x01u && req_len!=2u)) {
        return nrc_reply(resp, max, 0x27u, NRC_INCORRECT_LENGTH);
    }
    sub = req[1];
    if (lockout_active(s)) {
        return nrc_reply(resp, max, 0x27u, NRC_EXCEEDED_ATTEMPTS);
    }

    if (sub == 0x01u) { /* requestSeed */
        if (s->unlocked) {
            resp[0] = 0x67u;
            resp[1] = 0x01u;
            put_be32(&resp[2], 0u); /* already unlocked */
            return 6u;
        }
        s->request_count++;
        s->seed = 0xC0DE0000u ^ (s->request_count * 2654435761u);
        if (s->seed == 0u) {
            s->seed = 0x12345678u;
        }
        s->seed_valid = 1u;
        resp[0] = 0x67u;
        resp[1] = 0x01u;
        put_be32(&resp[2], s->seed);
        return 6u;
    }

    if (sub == 0x02u) { /* sendKey */
        uint32_t key;

        if (req_len != 6u) {
            return nrc_reply(resp, max, 0x27u, NRC_INCORRECT_LENGTH);
        }
        if (!s->seed_valid) {
            return nrc_reply(resp, max, 0x27u, NRC_REQUEST_SEQUENCE_ERROR);
        }
        key = get_be32(&req[2]);
        if (key != uds_calc_key(s->seed)) {
            s->seed_valid = 0u;
            s->key_failures++;
            if (s->key_failures >= 3u) {
                s->lockout_started_ms = server_now(s);
                s->lockout_active = 1u;
                return nrc_reply(resp, max, 0x27u, NRC_EXCEEDED_ATTEMPTS);
            }
            return nrc_reply(resp, max, 0x27u, NRC_INVALID_KEY);
        }
        s->unlocked = 1u;
        s->seed_valid = 0u;
        s->seed = 0u;
        s->key_failures = 0u;
        resp[0] = 0x67u;
        resp[1] = 0x02u;
        return 2u;
    }

    return nrc_reply(resp, max, 0x27u, NRC_SUBFUNCTION_NOT_SUPPORTED);
}

/* ---- 0x34 ---- */
static uint16_t handle_request_download(uds_server_t *s,const uint8_t *req,uint16_t len,
                                        uint8_t *resp,uint16_t max)
{
    metadata_t m;
    uint32_t addr,size;
    int loaded;
    if(len!=11u || req[1]!=0u || req[2]!=0x44u)
        return nrc_reply(resp,max,0x34u,NRC_INCORRECT_LENGTH);
    if(s->session!=UDS_SESSION_PROGRAMMING)
        return nrc_reply(resp,max,0x34u,NRC_CONDITIONS_NOT_CORRECT);
    if(!s->unlocked) return nrc_reply(resp,max,0x34u,NRC_SECURITY_ACCESS_DENIED);
    if(s->dl_active) return nrc_reply(resp,max,0x34u,NRC_REQUEST_SEQUENCE_ERROR);
    addr=get_be32(req+3); size=get_be32(req+7);
    /* This is the app's logical execution address; writes go to external
     * DOWNLOAD_OFFSET. Neither internal APP nor external BACKUP is writable. */
    if(addr!=APP_BASE_ADDR || size<IMAGE_HEADER_SIZE+8u ||
       size>IMAGE_HEADER_SIZE+IMAGE_MAX_PAYLOAD+OTA_SIGNATURE_SIZE)
        return nrc_reply(resp,max,0x34u,NRC_REQUEST_OUT_OF_RANGE);
    loaded=metadata_load(s->boot->store,&m);
    if(loaded<0) return nrc_reply(resp,max,0x34u,NRC_GENERAL_PROGRAMMING_FAILURE);
    if(!loaded) memset(&m,0,sizeof m);
    if(m.state!=UPDATE_IDLE && m.state!=UPDATE_DOWNLOADING)
        return nrc_reply(resp,max,0x34u,NRC_CONDITIONS_NOT_CORRECT);
    m.state=UPDATE_DOWNLOADING;
    /* Invalidate the old staged package durably BEFORE any erase. */
    if(metadata_save(s->boot->store,&m) || !s->boot->external.erase ||
       s->boot->external.erase(s->boot->external.user,DOWNLOAD_OFFSET,EXT_IMAGE_SLOT_SIZE))
        return nrc_reply(resp,max,0x34u,NRC_GENERAL_PROGRAMMING_FAILURE);
    s->dl_active=1u; s->dl_has_previous=0u; s->dl_offset=DOWNLOAD_OFFSET;
    s->dl_size=size; s->dl_written=0u; s->dl_next_counter=1u;
    resp[0]=0x74u; resp[1]=0x20u;
    resp[2]=(uint8_t)(UDS_MAX_DOWNLOAD_BLOCK>>8);
    resp[3]=(uint8_t)UDS_MAX_DOWNLOAD_BLOCK;
    return 4u;
}

/* ---- 0x36 ---- */
static uint16_t handle_transfer_data(uds_server_t *s,const uint8_t *req,uint16_t len,
                                     uint8_t *resp,uint16_t max)
{
    uint8_t counter;
    uint32_t n;
    flash_port_t p=s->boot->external;
    if(len<3u || len>UDS_MAX_DOWNLOAD_BLOCK)
        return nrc_reply(resp,max,0x36u,NRC_INCORRECT_LENGTH);
    if(s->session!=UDS_SESSION_PROGRAMMING)
        return nrc_reply(resp,max,0x36u,NRC_CONDITIONS_NOT_CORRECT);
    if(!s->unlocked) return nrc_reply(resp,max,0x36u,NRC_SECURITY_ACCESS_DENIED);
    if(!s->dl_active) return nrc_reply(resp,max,0x36u,NRC_REQUEST_SEQUENCE_ERROR);
    counter=req[1]; n=(uint32_t)len-2u;
    if(s->dl_has_previous && counter==s->dl_previous_counter) {
        uint8_t previous[UDS_MAX_DOWNLOAD_DATA];
        if(n!=s->dl_last_size) return nrc_reply(resp,max,0x36u,0x73u);
        if(!p.read || p.read(p.user,s->dl_offset+s->dl_last_offset,previous,n))
            return nrc_reply(resp,max,0x36u,NRC_GENERAL_PROGRAMMING_FAILURE);
        if(memcmp(previous,req+2,n)!=0) return nrc_reply(resp,max,0x36u,0x73u);
    } else {
        if(counter!=s->dl_next_counter) return nrc_reply(resp,max,0x36u,0x73u);
        if(n>s->dl_size-s->dl_written)
            return nrc_reply(resp,max,0x36u,NRC_REQUEST_OUT_OF_RANGE);
        if(!p.write || p.write(p.user,s->dl_offset+s->dl_written,req+2,n))
            return nrc_reply(resp,max,0x36u,NRC_GENERAL_PROGRAMMING_FAILURE);
        s->dl_last_offset=s->dl_written; s->dl_last_size=(uint16_t)n;
        s->dl_previous_counter=counter; s->dl_has_previous=1u;
        s->dl_written+=n;
        s->dl_next_counter=(uint8_t)(counter+1u); /* FF -> 00 is valid */
    }
    resp[0]=0x76u; resp[1]=counter; return 2u;
}

/* ---- 0x37 ---- */
static uint16_t handle_transfer_exit(uds_server_t *s,const uint8_t *req,uint16_t len,
                                     uint8_t *resp,uint16_t max)
{
    metadata_t m;
    image_header_t h;
    (void)req;
    if(len!=1u) return nrc_reply(resp,max,0x37u,NRC_INCORRECT_LENGTH);
    if(s->session!=UDS_SESSION_PROGRAMMING)
        return nrc_reply(resp,max,0x37u,NRC_CONDITIONS_NOT_CORRECT);
    if(!s->unlocked) return nrc_reply(resp,max,0x37u,NRC_SECURITY_ACCESS_DENIED);
    if(!s->dl_active || s->dl_written!=s->dl_size)
        return nrc_reply(resp,max,0x37u,NRC_REQUEST_SEQUENCE_ERROR);
    if(image_read_header(s->boot->external,DOWNLOAD_OFFSET,&h) ||
       h.image_size+IMAGE_HEADER_SIZE+
           ((h.flags&IMAGE_FLAG_SIGNED)?OTA_SIGNATURE_SIZE:0u)!=s->dl_size ||
       image_validate(s->boot->external,DOWNLOAD_OFFSET,0,0))
        return nrc_reply(resp,max,0x37u,NRC_GENERAL_PROGRAMMING_FAILURE);
    if(metadata_load(s->boot->store,&m)!=1 || m.state!=UPDATE_DOWNLOADING)
        return nrc_reply(resp,max,0x37u,NRC_GENERAL_PROGRAMMING_FAILURE);
    if(h.version<=m.min_version || h.version<=m.active_version)
        return nrc_reply(resp,max,0x37u,NRC_REQUEST_OUT_OF_RANGE);
    m.state=UPDATE_READY; m.pending_version=h.version;
    m.pending_size=h.image_size; m.pending_crc=h.payload_crc; m.boot_attempts=0u;
    if(metadata_save(s->boot->store,&m))
        return nrc_reply(resp,max,0x37u,NRC_GENERAL_PROGRAMMING_FAILURE);
    s->dl_active=0u;
    resp[0]=0x77u; return 1u;
}

/* ---- 0x31 ---- */
static uint16_t handle_routine(uds_server_t *s,const uint8_t *req,uint16_t len,
                               uint8_t *resp,uint16_t max)
{
    metadata_t m;
    image_header_t h;
    if(len!=4u) return nrc_reply(resp,max,0x31u,NRC_INCORRECT_LENGTH);
    if((req[1]&0x7Fu)!=1u)
        return nrc_reply(resp,max,0x31u,NRC_SUBFUNCTION_NOT_SUPPORTED);
    if(req[2]!=0u || req[3]!=1u)
        return nrc_reply(resp,max,0x31u,NRC_REQUEST_OUT_OF_RANGE);
    if(s->session!=UDS_SESSION_PROGRAMMING || !s->unlocked)
        return nrc_reply(resp,max,0x31u,NRC_SECURITY_ACCESS_DENIED);
    /* Routine 0001 verifies a READY package. Health confirmation belongs
     * to the running app; a tester cannot confirm a package before boot. */
    if(metadata_load(s->boot->store,&m)!=1 || m.state!=UPDATE_READY)
        return nrc_reply(resp,max,0x31u,NRC_CONDITIONS_NOT_CORRECT);
    if(image_read_header(s->boot->external,DOWNLOAD_OFFSET,&h) ||
       h.version!=m.pending_version || h.image_size!=m.pending_size ||
       h.payload_crc!=m.pending_crc ||
       image_validate(s->boot->external,DOWNLOAD_OFFSET,0,0))
        return nrc_reply(resp,max,0x31u,NRC_GENERAL_PROGRAMMING_FAILURE);
    if(req[1]&0x80u) return 0u;
    resp[0]=0x71u; resp[1]=1u; resp[2]=0u; resp[3]=1u; resp[4]=0u;
    return 5u;
}

/* ---- 0x3E ---- */
static uint16_t handle_tester_present(const uint8_t *req, uint16_t req_len,
                                      uint8_t *resp, uint16_t max)
{
    if (req_len != 2u) {
        return nrc_reply(resp, max, 0x3Eu, NRC_INCORRECT_LENGTH);
    }
    if ((uint8_t)(req[1] & 0x7Fu) != 0x00u) {
        return nrc_reply(resp, max, 0x3Eu, NRC_SUBFUNCTION_NOT_SUPPORTED);
    }
    if ((req[1] & 0x80u) != 0u) {
        return 0u;
    }
    resp[0] = 0x7Eu;
    resp[1] = 0x00u;
    return 2u;
}

void uds_init(uds_server_t *s, boot_context_t *boot, const ota_port_t *port)
{
    memset(s, 0, sizeof *s);
    s->boot = boot;
    s->port = port;
    s->session = UDS_SESSION_DEFAULT;
    s->dl_next_counter = 1u;
    s->last_request_ms = server_now(s);
}

void uds_poll(uds_server_t *s)
{
    if(s && s->session!=UDS_SESSION_DEFAULT &&
       (uint32_t)(server_now(s)-s->last_request_ms)>UDS_S3_TIMEOUT_MS) {
        s->session=UDS_SESSION_DEFAULT; s->unlocked=0u;
        s->seed_valid=0u; s->dl_active=0u;
    }
}

uint16_t uds_handle(uds_server_t *s, const uint8_t *req, uint16_t req_len,
                    uint8_t *resp, uint16_t resp_max)
{
    uint8_t sid;
    uint16_t result;

    if (s == 0 || req == 0 || req_len == 0u) {
        return 0u;
    }
    if (resp == 0 || resp_max < 3u) {
        return 0u;
    }

    if (!s->boot || !s->boot->store) return 0u;
    uds_poll(s);
    s->last_request_ms=server_now(s);
    sid = req[0];
    /* Reserve full positive-response space before changing state. */
    if (((sid==0x10u || sid==0x27u) && resp_max<6u) ||
        (sid==0x34u && resp_max<4u) || (sid==0x31u && resp_max<5u)) return 0u;
    switch (sid) {
    case 0x10u:
        result=handle_session(s, req, req_len, resp, resp_max); break;
    case 0x11u:
        result=handle_ecu_reset(s, req, req_len, resp, resp_max); break;
    case 0x22u:
        result=handle_read_data(s, req, req_len, resp, resp_max); break;
    case 0x27u:
        result=handle_security(s, req, req_len, resp, resp_max); break;
    case 0x31u:
        result=handle_routine(s, req, req_len, resp, resp_max); break;
    case 0x34u:
        result=handle_request_download(s, req, req_len, resp, resp_max); break;
    case 0x36u:
        result=handle_transfer_data(s, req, req_len, resp, resp_max); break;
    case 0x37u:
        result=handle_transfer_exit(s, req, req_len, resp, resp_max); break;
    case 0x3Eu:
        result=handle_tester_present(req, req_len, resp, resp_max); break;
    default:
        result=nrc_reply(resp, resp_max, sid, NRC_SERVICE_NOT_SUPPORTED); break;
    }
    /* S3 is an idle timeout: time spent processing a synchronous storage
     * operation must not expire the session immediately on the next request. */
    s->last_request_ms=server_now(s);
    return result;
}
