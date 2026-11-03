#ifndef NOR_MAILBOX_H
#define NOR_MAILBOX_H
#include <stdint.h>

/* SWD RAM mailbox protocol v2 between the NOR backup firmware and the host.
 *
 * Publication ordering contract: the firmware sets status=BUSY before touching
 * the block buffer/header, fills the buffer, writes every header field, then
 * publishes status=READY as the LAST store. The host consumes a block only
 * while status==READY and re-checks magic/version/session/seq/offset/length
 * before and after copying, so a mid-update header is never accepted.
 */
#ifndef NOR_TOTAL_SIZE
#define NOR_TOTAL_SIZE       0x01000000u  /* 16 MiB, fixed for the real run */
#endif
#define NOR_BLOCK_SIZE       8192u        /* 8 KiB data buffer */
#define NOR_MAILBOX_MAGIC    0x4E4F5244u  /* "NORD" */
#define NOR_MAILBOX_VERSION  2u

#define MB_STATUS_IDLE       0u
#define MB_STATUS_BUSY       1u  /* firmware is preparing the next block */
#define MB_STATUS_READY      2u  /* block is a consistent, published snapshot */
#define MB_STATUS_DONE       3u  /* full image streamed */
#define MB_STATUS_ERROR      0xE0u

#define MB_ERR_NONE          0u
#define MB_ERR_INIT          1u
#define MB_ERR_JEDEC         2u
#define MB_ERR_SPI           3u
#define MB_ERR_ACK_TIMEOUT   4u
#define MB_ERR_BAD_SESSION   5u

/* Header is exactly 10 little-endian uint32 (40 bytes); data follows. */
typedef struct {
    uint32_t magic;                 /* NOR_MAILBOX_MAGIC */
    uint32_t version;               /* NOR_MAILBOX_VERSION */
    uint32_t session;               /* host nonce; firmware echoes it per block */
    uint32_t seq;                   /* block number, starts at 1, strictly +1 */
    uint32_t offset;                /* NOR offset of this block */
    uint32_t length;                /* bytes in this block (<= NOR_BLOCK_SIZE) */
    uint32_t status;                /* IDLE/BUSY/READY/DONE/ERROR */
    uint32_t error;                 /* MB_ERR_* */
    uint32_t crc32;                 /* CRC32 of data[0..length) */
    uint32_t ack;                   /* host echoes seq after consuming the block */
    uint8_t  data[NOR_BLOCK_SIZE];
} nor_mailbox_t;

#define NOR_MAILBOX_HEADER_WORDS 10u
#define NOR_MAILBOX_HEADER_BYTES (NOR_MAILBOX_HEADER_WORDS * 4u)
#define NOR_MAILBOX_STRUCT_BYTES (NOR_MAILBOX_HEADER_BYTES + NOR_BLOCK_SIZE)

#endif
