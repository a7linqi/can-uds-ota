/* NOR restore firmware (bench tool, write-capable by design).
 *
 * It restores the external 16 MiB NOR from a golden image pushed over the SWD
 * RAM mailbox, erasing one 4 KiB sector at a time, programming one 256 B page
 * at a time and reading every page back before the block is acknowledged.
 *
 * This image is deliberately NOT the product bootloader: it does not link the
 * OTA update engine, it never jumps to an application, and it keeps all OTA
 * write gates off. Its single job is the restore drill that must pass before
 * the first real erase of the NOR.
 */
#include "explorer_reference.h"
#include "nor_restore.h"
#include "w25q_bench.h"
#include <string.h>

#if OTA_ENABLE_FLASHING || OTA_ENABLE_BOOT_HANDOFF || OTA_ENABLE_WATCHDOG
#error "The restore bench image must keep all OTA write/handoff/watchdog gates off"
#endif
#if defined(OTA_EXPLORER_CAN_LOOPBACK) && OTA_EXPLORER_CAN_LOOPBACK != 1
#error "The restore bench image must not start physical CAN"
#endif

/* Exported symbol; the host reads its address from the ELF symbol table. */
volatile nr_mailbox_t nor_restore_mailbox __attribute__((aligned(8)));

static nr_ctx_t ctx;
static w25q_t nor;

int ota_firmware_service(void) { return 0; }
int ota_firmware_watchdog_service(void) { return 0; }
void ota_bootloader_can_rx(const can_frame_t *frame) { (void)frame; }

static int bench_xfer(void *user, const uint8_t *tx, uint8_t *rx, uint32_t n)
{
    SPI_HandleTypeDef *spi = ota_user_spi();
    HAL_StatusTypeDef status;

    (void)user;
    if (n == 0u) return 0;
    if (tx && rx) {
        status = HAL_SPI_TransmitReceive(spi, (uint8_t *)tx, rx, (uint16_t)n, 100u);
    } else if (tx) {
        status = HAL_SPI_Transmit(spi, (uint8_t *)tx, (uint16_t)n, 100u);
    } else if (rx) {
        status = HAL_SPI_Receive(spi, rx, (uint16_t)n, 1000u);
    } else {
        return 0;
    }
    return status == HAL_OK ? 0 : -1;
}

static int bench_cs(void *user, int active)
{
    (void)user;
    return ota_user_nor_select(active) == 0 ? 0 : -1;
}

static void bench_delay_ms(void *user, uint32_t ms)
{
    uint32_t start = ota_bsp_now_ms();
    (void)user;
    while ((uint32_t)(ota_bsp_now_ms() - start) < ms) ota_bsp_idle();
}

static const w25q_io_t bench_io = { bench_xfer, bench_cs, bench_delay_ms, 0 };

static int nr_erase(void *user, uint32_t addr)
{
    (void)user;
    return w25q_erase_4k(&nor, addr);
}

static int nr_program(void *user, uint32_t addr, const uint8_t *src, uint32_t n)
{
    (void)user;
    return w25q_page_program(&nor, addr, src, n);
}

static int nr_read(void *user, uint32_t addr, uint8_t *out, uint32_t n)
{
    (void)user;
    return w25q_read(&nor, addr, out, n);
}

static void fail_before_restore(uint32_t err)
{
    nor_restore_mailbox.h.magic = NR_MAGIC;
    nor_restore_mailbox.h.version = NR_VERSION;
    nor_restore_mailbox.h.session = 0u;
    nor_restore_mailbox.h.seq = 0u;
    nor_restore_mailbox.h.offset = 0u;
    nor_restore_mailbox.h.length = 0u;
    nor_restore_mailbox.h.crc32 = 0u;
    nor_restore_mailbox.h.ack = 0u;
    nor_restore_mailbox.h.error = err;
    nor_restore_mailbox.h.status = NR_ST_ERROR;
    for (;;) ota_bsp_idle();
}

int main(void)
{
    ota_board_config_t config;
    nr_io_t io;
    uint32_t jedec = 0u;
    int result;

    ota_arch_irq_enable();
    if (ota_bsp_init(&config) || !config.board_verified) fail_before_restore(NR_ERR_INIT);
    if (w25q_open(&nor, &bench_io, NR_TOTAL_SIZE, 2000u)) fail_before_restore(NR_ERR_INIT);
    if (w25q_read_jedec(&nor, &jedec)) fail_before_restore(NR_ERR_JEDEC);
    /* W25Q128 family only: the golden image is 16 MiB and the geometry is
     * hard-wired, so a different chip must stop here rather than half-write. */
    if (jedec != 0x00EF4018u) fail_before_restore(NR_ERR_JEDEC);
    if (jedec != ota_explorer_probe.jedec_id) fail_before_restore(NR_ERR_JEDEC);

    io.erase = nr_erase;
    io.program = nr_program;
    io.read = nr_read;
    io.ctx = 0;
    nr_init(&ctx, &nor_restore_mailbox, &io, NR_TOTAL_SIZE);
    for (;;) {
        result = nr_service(&ctx);
        if (result != 0) { for (;;) ota_bsp_idle(); }  /* DONE or ERROR published */
        ota_bsp_idle();
    }
}
