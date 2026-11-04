/* Host test for the bench W25Q driver (w25q_bench.c) against a byte-level SPI
 * slave model: real opcode/CS framing, WIP busy polling, WEL cleared by the
 * chip after every erase/program, program that can only clear bits.
 */
#include "w25q_bench.h"
#include "ota/crc32.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CAP (64u * 1024u)
#define JEDEC 0x00EF4018u

static uint8_t mem[CAP];
static uint8_t sr1;
static int busy_polls;
static int stuck_busy;
static int pull_wel;
static int erase_count, program_count, wren_count;
static uint32_t delay_calls;

typedef struct {
    uint8_t op;
    int naddr;
    uint32_t addr;
    int idx;
} slave_t;
static slave_t sl;

static void die(const char *m) { printf("FAIL: %s\n", m); exit(1); }
static void check(int c, const char *m) { if (!c) die(m); }

static int slave_cs(void *u, int active)
{
    (void)u;
    if (active) {
        sl.op = 0u; sl.naddr = 0; sl.addr = 0; sl.idx = 0;
    } else {
        if (sl.op == 0x20u) {
            memset(mem + (sl.addr & ~(W25Q_SECTOR_SIZE - 1u)), 0xFF, W25Q_SECTOR_SIZE);
            erase_count++;
            sr1 |= 0x01u; sr1 &= (uint8_t)~0x02u;
            busy_polls = 3;
        } else if (sl.op == 0x02u) {
            program_count++;
            sr1 |= 0x01u; sr1 &= (uint8_t)~0x02u;
            busy_polls = 2;
        }
        sl.op = 0u;
    }
    return 0;
}

static int slave_xfer(void *u, const uint8_t *tx, uint8_t *rx, uint32_t n)
{
    static const uint8_t id[3] = { 0xEFu, 0x40u, 0x18u };
    uint32_t i;
    (void)u;
    for (i = 0u; i < n; i++) {
        uint8_t t = tx ? tx[i] : 0xFFu;
        uint8_t r = 0xFFu;
        if (sl.op == 0u) {
            sl.op = t; sl.addr = 0u; sl.naddr = 0; sl.idx = 0;
            if (t == 0x06u) { wren_count++; if (!pull_wel) sr1 |= 0x02u; }
        } else if (sl.op == 0x03u || sl.op == 0x02u || sl.op == 0x20u) {
            if (sl.naddr < 3) {
                sl.addr = (sl.addr << 8) | t;
                sl.naddr++;
            } else if (sl.op == 0x03u) {
                r = mem[sl.addr % CAP];
                sl.addr++;
            } else if (sl.op == 0x02u) {
                mem[sl.addr % CAP] &= t;      /* program can only clear bits */
                sl.addr++;
            }
        } else if (sl.op == 0x05u) {
            if ((sr1 & 0x01u) && !stuck_busy) {
                if (busy_polls > 0) busy_polls--;
                if (busy_polls == 0) sr1 &= (uint8_t)~0x01u;
            }
            r = sr1;
        } else if (sl.op == 0x9Fu) {
            r = (sl.idx < 3) ? id[sl.idx] : 0xFFu;
            sl.idx++;
        }
        if (rx) rx[i] = r;
    }
    return 0;
}

static void slave_delay(void *u, uint32_t ms)
{
    (void)u;
    delay_calls += ms;
}

static const w25q_io_t io = { slave_xfer, slave_cs, slave_delay, 0 };
static w25q_t dev;

static void model_reset(void)
{
    memset(mem, 0x00, sizeof mem);
    sr1 = 0u;
    busy_polls = 0;
    stuck_busy = 0;
    pull_wel = 0;
    erase_count = program_count = wren_count = 0;
    delay_calls = 0u;
    sl.op = 0u;
    check(w25q_open(&dev, &io, CAP, 50u) == 0, "open failed");
}

int main(void)
{
    uint8_t buf[512];
    uint32_t id = 0u, crc;
    int err;

    printf("[w25q_bench driver]\n");

    check(w25q_open(&dev, &io, 0u, 10u) == -1, "zero capacity accepted");
    check(w25q_open(&dev, &io, CAP + 1u, 10u) == -1, "unaligned capacity accepted");
    check(w25q_open(&dev, &io, 0x01000000u + 1u, 10u) == -1, "oversized capacity accepted");

    /* Ident */
    model_reset();
    check(w25q_read_jedec(&dev, &id) == 0, "read id failed");
    check(id == JEDEC, "wrong JEDEC id");
    check(dev.jedec_id == JEDEC, "jedec not cached");
    printf("  JEDEC %06X: OK\n", (unsigned)id);

    /* Erase alignment and range */
    check(w25q_erase_4k(&dev, 1u) == -1, "misaligned erase accepted");
    check(w25q_erase_4k(&dev, CAP) == -1, "erase past the end accepted");
    check(erase_count == 0, "a rejected erase reached the chip");

    /* Erase clears the whole 4 KiB sector and clears WEL afterwards */
    memset(mem, 0xA5, sizeof mem);
    check(w25q_erase_4k(&dev, 4096u) == 0, "erase failed");
    check(erase_count == 1, "erase not issued exactly once");
    check(mem[4096] == 0xFFu && mem[8191] == 0xFFu, "erase did not clear the sector");
    check(mem[4095] == 0xA5u && mem[8192] == 0xA5u, "erase touched a neighbouring sector");
    check((sr1 & 0x02u) == 0u, "model should have cleared WEL");
    printf("  erase 4 KiB sector, WEL re-armed per op: OK\n");

    /* Program alignment, page crossing, size and range */
    check(w25q_page_program(&dev, 16u, mem, 16u) == -1, "misaligned program accepted");
    check(w25q_page_program(&dev, 0u, mem, 257u) == -1, "oversized program accepted");
    check(w25q_page_program(&dev, 0u, mem, 0u) == -1, "zero-length program accepted");
    check(w25q_page_program(&dev, W25Q_PAGE_SIZE - 8u, mem, 16u) == -1, "page crossing accepted");
    check(w25q_page_program(&dev, CAP - 8u, mem, 8u) == -1, "program past the end accepted");
    check(program_count == 0, "a rejected program reached the chip");

    /* Program + read-back through the page boundary rules */
    for (id = 0u; id < 256u; id++) buf[id] = (uint8_t)(255u - id);
    check(w25q_page_program(&dev, 4096u, buf, 256u) == 0, "page program failed");
    check(w25q_page_program(&dev, 4352u, buf, 100u) == 0, "partial page program failed");
    check(program_count == 2, "program count wrong");
    check(w25q_read(&dev, 4096u, buf, 256u) == 0, "read after program failed");
    check(buf[0] == 0xFFu && buf[255] == 0x00u, "programmed content wrong");
    check(w25q_read(&dev, 4352u, buf, 1u) == 0 && buf[0] == 0xFFu, "partial page tail wrong");
    printf("  page program with page-boundary rules: OK\n");

    /* A missing WEL must stop the operation before it is issued. */
    model_reset();
    pull_wel = 1;
    check(w25q_erase_4k(&dev, 0u) == -1, "erase proceeded without WEL");
    check(erase_count == 0, "erase reached the chip without WEL");
    check(w25q_page_program(&dev, 0u, buf, 16u) == -1, "program proceeded without WEL");
    check(program_count == 0, "program reached the chip without WEL");
    check(wren_count >= 2, "WREN was not attempted");
    printf("  missing WEL fails closed before any write: OK\n");

    /* Bounded busy wait */
    model_reset();
    check(w25q_erase_4k(&dev, 0u) == 0, "erase with busy polls failed");
    check(delay_calls > 0u, "driver never waited for WIP");
    model_reset();
    stuck_busy = 1;
    check(w25q_erase_4k(&dev, 0u) == -1, "stuck WIP did not time out");
    check(delay_calls >= 50u, "timeout used fewer milliseconds than configured");
    printf("  WIP polling and timeout: OK\n");

    /* Range checks and streaming CRC */
    model_reset();
    memset(mem, 0x00, sizeof mem);
    check(w25q_page_program(&dev, 0u, buf, 32u) == 0, "program failed");
    check(w25q_read(&dev, CAP - 1u, buf, 2u) == -1, "read past the end accepted");
    check(w25q_read(&dev, 0u, buf, 0u) == -1, "zero-length read accepted");
    err = 0;
    crc = w25q_crc32_range(&dev, 0u, 1024u, buf, 64u, &err);
    check(err == 0, "crc range reported an error");
    check(crc == crc32_calc(mem, 1024u), "crc range mismatch");
    err = 0;
    crc = w25q_crc32_range(&dev, CAP - 16u, 32u, buf, 64u, &err);
    check(err < 0, "out-of-range crc sweep did not fail");
    printf("  range checks and streaming CRC: OK\n");

    printf("ALL W25Q DRIVER TESTS PASSED\n");
    return 0;
}
