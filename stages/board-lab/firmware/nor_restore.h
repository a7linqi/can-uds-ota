#ifndef NOR_RESTORE_H
#define NOR_RESTORE_H
#include <stdint.h>

/* Restore protocol v3: the host pushes a golden NOR image back into the chip
 * over the same SWD RAM mailbox used by v2, and the firmware erases,
 * programs and read-back-verifies every page before acknowledging a block.
 *
 * v3 is deliberately a different magic/version from the read-only v2 dump
 * firmware, so a restore session can never be mistaken for a backup session.
 *
 * Field roles (both directions share one 40-byte header):
 *   host -> device : session (nonce), seq, offset, length, crc32, data[],
 *                    ack (command or submitted seq, always written LAST)
 *
 * ack is a doorbell: the device clears it (back to 0) when it consumes a
 * command/block, and only then publishes READY/BLOCK_OK/DONE. Because the host
 * owns the submit and the device owns the clear, "ack == 0" is an unambiguous
 * "the block was really taken" token; without it a host that polls before
 * driving the device would accept the previous block's BLOCK_OK again.
 *   device -> host : status, error, and echo of session/seq/offset/length/crc32
 *
 * Commands are carried in ack and must stay >= NR_CMD_BASE, which a legal seq
 * can never reach (seq <= NR_MAX_SEQ). That keeps "start" and "block N"
 * unambiguous in a single word.
 */

#define NR_MAGIC             0x4E4F5257u  /* "NORW" */
#define NR_VERSION           3u

#ifndef NR_TOTAL_SIZE
#define NR_TOTAL_SIZE        0x01000000u  /* 16 MiB, fixed for the real chip */
#endif
#ifndef NR_BLOCK_SIZE
#define NR_BLOCK_SIZE        32768u       /* 32 KiB: SWD transfer is the bottleneck */
#endif
#define NR_SECTOR_SIZE       4096u
#define NR_PAGE_SIZE         256u

#define NR_HEADER_BYTES      40u
#define NR_MAILBOX_BYTES     (NR_HEADER_BYTES + NR_BLOCK_SIZE)

#define NR_CMD_BASE          0x80000000u
#define NR_CMD_RESTORE       0xA1000000u
#define NR_CMD_VERIFY        0xA2000000u
#define NR_MAX_SEQ           0x7FFFFFFFu

#define NR_ST_IDLE           0u   /* boot state, waiting for a command */
#define NR_ST_BUSY           1u   /* device working, host must not touch anything */
#define NR_ST_READY          2u   /* device idle, ready for the next block */
#define NR_ST_BLOCK_OK       3u   /* submitted block erased+programmed+verified */
#define NR_ST_DONE           4u   /* image complete (or verify complete) */
#define NR_ST_ERROR          0xE0u

#define NR_ERR_NONE          0u
#define NR_ERR_INIT          1u
#define NR_ERR_JEDEC         2u
#define NR_ERR_SPI           3u
#define NR_ERR_BAD_REQUEST   4u   /* seq/offset/length/alignment disagree */
#define NR_ERR_BLOCK_CRC     5u   /* host block CRC wrong: NOR was NOT touched */
#define NR_ERR_ERASE         6u
#define NR_ERR_PROGRAM       7u
#define NR_ERR_VERIFY        8u   /* read-back differs from what was submitted */

typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t session;
    uint32_t seq;
    uint32_t offset;
    uint32_t length;
    uint32_t status;
    uint32_t error;
    uint32_t crc32;
    uint32_t ack;
} nr_header_t;

typedef struct {
    nr_header_t h;
    uint8_t data[NR_BLOCK_SIZE];
} nr_mailbox_t;

/* Byte offsets used by the SWD host; must match nr_header_t exactly. */
#define NR_OFF_SESSION 8u
#define NR_OFF_SEQ     12u
#define NR_OFF_LENGTH  20u
#define NR_OFF_STATUS  24u
#define NR_OFF_CRC32   32u
#define NR_OFF_ACK     36u
#define NR_OFF_DATA    40u

typedef int (*nr_erase_fn)(void *ctx, uint32_t addr);
typedef int (*nr_program_fn)(void *ctx, uint32_t addr, const uint8_t *src, uint32_t n);
typedef int (*nr_read_fn)(void *ctx, uint32_t addr, uint8_t *out, uint32_t n);

typedef struct {
    nr_erase_fn erase;
    nr_program_fn program;
    nr_read_fn read;
    void *ctx;
} nr_io_t;

typedef struct {
    volatile nr_mailbox_t *mb;
    nr_io_t io;
    uint32_t total;        /* bytes to restore/verify, multiple of NR_SECTOR_SIZE */
    uint32_t session;
    uint32_t seq;          /* blocks committed so far (also last committed seq) */
    uint32_t offset;       /* next block offset */
    uint32_t blk_len;      /* current block */
    uint32_t blk_crc;
    uint32_t cursor;       /* erase/program/verify/CRC cursor */
    uint32_t crc;          /* running full-image CRC32 */
    uint32_t blocks_done;
    uint32_t bytes_done;
    uint8_t  mode;         /* 0 none, 1 restore, 2 verify */
    uint8_t  state;
    uint16_t page_len;     /* 1..NR_PAGE_SIZE: needs 9 bits, not 8 */
    uint8_t  scratch[NR_PAGE_SIZE];
} nr_ctx_t;

/* Initialise the mailbox to IDLE and the machine to WAIT. total must be a
 * non-zero multiple of NR_SECTOR_SIZE and <= NR_TOTAL_SIZE. */
void nr_init(nr_ctx_t *c, volatile nr_mailbox_t *mb, const nr_io_t *io, uint32_t total);

/* Run one bounded step: 0 = keep going, 1 = DONE published, -1 = ERROR
 * published (code in mb->h.error). Never blocks longer than one erase or one
 * page program plus its read-back. */
int nr_service(nr_ctx_t *c);

#endif
