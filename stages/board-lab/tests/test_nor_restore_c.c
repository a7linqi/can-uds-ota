/* Host test for the restore state machine (nor_restore.c) against a chip model
 * that behaves like a real W25Q128: sector erase sets 0xFF, page program can
 * only clear bits, a program without a preceding erase is recorded, and every
 * byte written is read back by the machine itself.
 */
#include "nor_restore.h"
#include "ota/crc32.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define TOTAL 40960u                      /* 32 KiB block + 8 KiB tail = 10 sectors */
#define SECTORS (TOTAL / NR_SECTOR_SIZE)

static uint8_t chip[TOTAL];
static uint8_t sector_erased[SECTORS];
static nr_mailbox_t mailbox;
static nr_ctx_t ctx;

static uint32_t erase_calls, program_calls, read_calls;
static uint32_t program_without_erase;
static int fail_erase_index = -1, fail_program_index = -1, corrupt_read_addr = -1;
static uint32_t status_at_first_erase, status_at_first_program;
static uint32_t last_program_end;

static void die(const char *msg)
{
    printf("FAIL: %s\n", msg);
    exit(1);
}

static void check(int cond, const char *msg)
{
    if (!cond) die(msg);
}

static void model_reset(void)
{
    memset(chip, 0x00, sizeof chip);       /* deliberately NOT erased */
    memset(sector_erased, 0, sizeof sector_erased);
    erase_calls = program_calls = read_calls = 0;
    program_without_erase = 0;
    fail_erase_index = fail_program_index = corrupt_read_addr = -1;
    status_at_first_erase = status_at_first_program = 0xFFFFFFFFu;
    last_program_end = 0;
}

static int sim_erase(void *user, uint32_t addr)
{
    (void)user;
    if (fail_erase_index >= 0 && (uint32_t)fail_erase_index == erase_calls) return -1;
    if ((addr % NR_SECTOR_SIZE) != 0u || addr + NR_SECTOR_SIZE > TOTAL) return -1;
    memset(chip + addr, 0xFF, NR_SECTOR_SIZE);
    sector_erased[addr / NR_SECTOR_SIZE] = 1u;
    if (status_at_first_erase == 0xFFFFFFFFu) status_at_first_erase = mailbox.h.status;
    erase_calls++;
    return 0;
}

static int sim_program(void *user, uint32_t addr, const uint8_t *src, uint32_t n)
{
    uint32_t i;
    (void)user;
    if (fail_program_index >= 0 && (uint32_t)fail_program_index == program_calls) return -1;
    if ((addr % NR_PAGE_SIZE) != 0u || n == 0u || n > NR_PAGE_SIZE) return -1;
    if (addr + n > TOTAL) return -1;
    if (!sector_erased[addr / NR_SECTOR_SIZE]) program_without_erase++;
    for (i = 0; i < n; i++) chip[addr + i] &= src[i];   /* AND-only, like the real chip */
    if (status_at_first_program == 0xFFFFFFFFu) status_at_first_program = mailbox.h.status;
    last_program_end = addr + n;
    program_calls++;
    return 0;
}

static int sim_read(void *user, uint32_t addr, uint8_t *out, uint32_t n)
{
    (void)user;
    if (addr + n > TOTAL) return -1;
    memcpy(out, chip + addr, n);
    if (corrupt_read_addr >= 0 && (uint32_t)corrupt_read_addr >= addr
        && (uint32_t)corrupt_read_addr < addr + n) {
        out[corrupt_read_addr - addr] ^= 0x01u;
    }
    read_calls++;
    return 0;
}

static void machine_reset(uint32_t total)
{
    nr_io_t io;
    io.erase = sim_erase;
    io.program = sim_program;
    io.read = sim_read;
    io.ctx = 0;
    nr_init(&ctx, &mailbox, &io, total);
}

static const uint8_t *make_image(void)
{
    static uint8_t image[TOTAL];
    uint32_t i;
    for (i = 0; i < TOTAL; i++) image[i] = (uint8_t)((i * 37u + (i >> 8) * 11u + 5u) & 0xFFu);
    return image;
}

/* Full restore: returns 1 = DONE, -1 = ERROR, other negative = protocol stall. */
static int restore_all(nr_ctx_t *c, const uint8_t *img, uint32_t total, uint32_t session)
{
    volatile nr_mailbox_t *mb = c->mb;
    uint32_t off = 0u, seq = 0u, i;
    int r;

    mb->h.session = session;
    mb->h.ack = NR_CMD_RESTORE;
    for (i = 0u; i < 2000u; i++) {
        r = nr_service(c);
        if (r < 0) return -1;
        if (mb->h.status == NR_ST_READY) break;
    }
    if (mb->h.status != NR_ST_READY) return -2;

    while (off < total) {
        uint32_t n = total - off;
        if (n > NR_BLOCK_SIZE) n = NR_BLOCK_SIZE;
        memcpy((void *)mb->data, img + off, n);
        mb->h.seq = ++seq;
        mb->h.offset = off;
        mb->h.length = n;
        mb->h.crc32 = crc32_calc(img + off, n);
        mb->h.ack = seq;                       /* last store: submits the block */
        for (i = 0u; i < 1000000u; i++) {
            r = nr_service(c);
            if (r == 1) return 1;              /* DONE after the last block */
            if (r < 0) return -1;
            /* ack back to 0 is the device's own "block taken" token */
            if (mb->h.status == NR_ST_BLOCK_OK && mb->h.ack == 0u && mb->h.seq == seq) break;
        }
        if (i == 1000000u) return -3;
        off += n;
    }
    return -4;
}

static int verify_all(nr_ctx_t *c, uint32_t session, uint32_t *out_crc)
{
    volatile nr_mailbox_t *mb = c->mb;
    uint32_t i;
    int r;

    mb->h.session = session;
    mb->h.ack = NR_CMD_VERIFY;
    for (i = 0u; i < 10000000u; i++) {
        r = nr_service(c);
        if (r == 1) { *out_crc = mb->h.crc32; return 0; }
        if (r < 0) return -1;
    }
    return -1;
}

int main(void)
{
    const uint8_t *img = make_image();
    uint32_t crc;

    printf("[nor_restore state machine]\n");

    /* 1. Happy path over deliberately non-erased content. */
    model_reset();
    machine_reset(TOTAL);
    check(restore_all(&ctx, img, TOTAL, 0x1234u) == 1, "happy path did not reach DONE");
    check(memcmp(chip, img, TOTAL) == 0, "restored content differs from image");
    check(mailbox.h.status == NR_ST_DONE, "final status is not DONE");
    check(mailbox.h.error == NR_ERR_NONE, "DONE carries an error code");
    check(mailbox.h.crc32 == crc32_calc(img, TOTAL), "DONE CRC is not the full-image CRC");
    check(mailbox.h.offset == TOTAL && mailbox.h.length == 0u, "DONE header fields wrong");
    check(mailbox.h.seq == TOTAL / NR_BLOCK_SIZE + (TOTAL % NR_BLOCK_SIZE ? 1u : 0u),
          "DONE seq is not the block count");
    check(program_without_erase == 0u, "a page was programmed before its sector was erased");
    check(status_at_first_erase == NR_ST_BUSY, "first erase happened while READY was published");
    check(status_at_first_program == NR_ST_BUSY, "first program happened while READY was published");
    check(last_program_end == TOTAL, "last page did not end at the image end");
    check(erase_calls == SECTORS, "erase count is not one per sector");
    printf("  happy path: %u pages, %u sectors, CRC=%08X: OK\n",
           program_calls, erase_calls, (unsigned)mailbox.h.crc32);

    /* 2. After a power cut the machine restarts from a fresh boot (new context,
     *    chip content left as it was) and must converge on the same image. */
    machine_reset(TOTAL);
    check(restore_all(&ctx, img, TOTAL, 0x1235u) == 1, "second session refused after a fresh boot");
    check(memcmp(chip, img, TOTAL) == 0, "re-run changed the content");
    printf("  restart on a finished image: OK\n");

    /* 3. Wrong block CRC must be rejected before the chip is touched. */
    model_reset();
    machine_reset(TOTAL);
    mailbox.h.session = 0x77u;
    mailbox.h.ack = NR_CMD_RESTORE;
    nr_service(&ctx);
    memcpy((void *)mailbox.data, img, 4096u);
    mailbox.h.seq = 1u;
    mailbox.h.offset = 0u;
    mailbox.h.length = 4096u;
    mailbox.h.crc32 = crc32_calc(img, 4096u) ^ 1u;
    mailbox.h.ack = 1u;
    check(nr_service(&ctx) == -1, "bad block CRC was accepted");
    check(mailbox.h.error == NR_ERR_BLOCK_CRC, "wrong error code for a bad block CRC");
    check(erase_calls == 0u && program_calls == 0u, "NOR was touched despite a bad block CRC");
    printf("  bad block CRC rejected before any erase: OK\n");

    /* 4. Offset disagreement is a bad request, not a write. */
    model_reset();
    machine_reset(TOTAL);
    mailbox.h.session = 0x78u;
    mailbox.h.ack = NR_CMD_RESTORE;
    nr_service(&ctx);
    memcpy((void *)mailbox.data, img, 4096u);
    mailbox.h.seq = 1u;
    mailbox.h.offset = 4096u;                 /* first block must be 0 */
    mailbox.h.length = 4096u;
    mailbox.h.crc32 = crc32_calc(img, 4096u);
    mailbox.h.ack = 1u;
    check(nr_service(&ctx) == -1, "wrong offset was accepted");
    check(mailbox.h.error == NR_ERR_BAD_REQUEST, "wrong error code for a bad offset");
    check(erase_calls == 0u, "NOR was touched for a bad request");
    printf("  offset mismatch rejected: OK\n");

    /* 5. Erase failure is reported as ERASE and never as success. */
    model_reset();
    machine_reset(TOTAL);
    fail_erase_index = 3;
    check(restore_all(&ctx, img, TOTAL, 0x79u) == -1, "erase failure was not reported");
    check(mailbox.h.error == NR_ERR_ERASE, "wrong error code for an erase failure");
    printf("  erase failure -> ERROR/ERASE: OK\n");

    /* 6. Program failure is reported as PROGRAM. */
    model_reset();
    machine_reset(TOTAL);
    fail_program_index = 5;
    check(restore_all(&ctx, img, TOTAL, 0x7Au) == -1, "program failure was not reported");
    check(mailbox.h.error == NR_ERR_PROGRAM, "wrong error code for a program failure");
    printf("  program failure -> ERROR/PROGRAM: OK\n");

    /* 7. Read-back mismatch is reported as VERIFY and stops the block. */
    model_reset();
    machine_reset(TOTAL);
    corrupt_read_addr = 3 * (int)NR_PAGE_SIZE + 7;   /* inside the 4th page */
    check(restore_all(&ctx, img, TOTAL, 0x7Bu) == -1, "read-back mismatch was not reported");
    check(mailbox.h.error == NR_ERR_VERIFY, "wrong error code for a read-back mismatch");
    printf("  read-back mismatch -> ERROR/VERIFY: OK\n");

    /* 8. A duplicate ack must not re-program the block. */
    model_reset();
    machine_reset(TOTAL);
    mailbox.h.session = 0x7Cu;
    mailbox.h.ack = NR_CMD_RESTORE;
    nr_service(&ctx);
    memcpy((void *)mailbox.data, img, NR_BLOCK_SIZE);
    mailbox.h.seq = 1u;
    mailbox.h.offset = 0u;
    mailbox.h.length = NR_BLOCK_SIZE;
    mailbox.h.crc32 = crc32_calc(img, NR_BLOCK_SIZE);
    mailbox.h.ack = 1u;
    {
        uint32_t i;
        for (i = 0u; i < 1000000u; i++) {
            if (nr_service(&ctx) != 0) break;
            if (mailbox.h.status == NR_ST_BLOCK_OK && mailbox.h.ack == 0u) break;
        }
    }
    check(mailbox.h.status == NR_ST_BLOCK_OK, "block 1 never committed");
    {
        uint32_t programs_before = program_calls, erases_before = erase_calls;
        uint32_t i;
        for (i = 0u; i < 1000u; i++) nr_service(&ctx);
        check(program_calls == programs_before && erase_calls == erases_before,
              "a duplicate ack re-programmed the block");
    }
    printf("  duplicate ack ignored: OK\n");

    /* 9. Verify mode is read-only and reports the chip CRC. */
    model_reset();
    memset(chip, 0x5A, sizeof chip);          /* arbitrary chip content */
    machine_reset(TOTAL);
    check(verify_all(&ctx, 0x7Du, &crc) == 0, "verify mode failed");
    check(crc == crc32_calc(chip, TOTAL), "verify CRC is not the chip CRC");
    check(erase_calls == 0u && program_calls == 0u, "verify mode wrote to the chip");
    check(mailbox.h.status == NR_ST_DONE, "verify did not end in DONE");
    printf("  verify mode read-only, CRC=%08X: OK\n", (unsigned)crc);

    /* 10. A non-sector-aligned image is refused before any access. */
    model_reset();
    machine_reset(TOTAL - 1u);
    check(restore_all(&ctx, img, TOTAL - 1u, 0x7Eu) == -1, "misaligned total was accepted");
    check(mailbox.h.error == NR_ERR_INIT, "wrong error code for a misaligned total");
    check(erase_calls == 0u && program_calls == 0u, "misaligned total touched the chip");
    printf("  misaligned image size refused: OK\n");

    printf("ALL NOR_RESTORE STATE MACHINE TESTS PASSED\n");
    return 0;
}
