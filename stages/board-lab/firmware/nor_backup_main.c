/* NOR backup firmware: stream the full 16 MiB external NOR over a SWD RAM
 * mailbox using only the read commands 0x9F (during BSP init) and 0x03.
 * No WREN, erase, program, physical CAN, UDS or watchdog is ever started. */
#include "explorer_reference.h"
#include "nor_mailbox.h"
#include "nor_stream.h"
#include <string.h>

#if OTA_ENABLE_FLASHING || OTA_ENABLE_BOOT_HANDOFF || OTA_ENABLE_WATCHDOG
#error "NOR backup firmware must keep all write/handoff/watchdog gates off"
#endif
#if defined(OTA_EXPLORER_CAN_LOOPBACK) && OTA_EXPLORER_CAN_LOOPBACK != 1
#error "NOR backup must not start physical CAN"
#endif

/* Exported symbol; the host reads its address from the ELF symbol table. */
volatile nor_mailbox_t nor_mailbox __attribute__((aligned(4)));
static nor_stream_t stream;

int ota_firmware_service(void) { return 0; }
int ota_firmware_watchdog_service(void) { return 0; }
void ota_bootloader_can_rx(const can_frame_t *frame) { (void)frame; }

/* 0x03 read with 24-bit address; chip select is released on every path. */
static int nor_read_chunk(uint32_t addr, uint8_t *out, uint32_t n)
{
    uint8_t cmd[4];
    SPI_HandleTypeDef *spi = ota_user_spi();
    cmd[0] = 0x03u;
    cmd[1] = (uint8_t)(addr >> 16);
    cmd[2] = (uint8_t)(addr >> 8);
    cmd[3] = (uint8_t)addr;
    if (ota_user_nor_select(1)) return -1;
    if (HAL_SPI_Transmit(spi, cmd, 4u, 100u) != HAL_OK) { ota_user_nor_select(0); return -1; }
    if (HAL_SPI_Receive(spi, out, n, 4000u) != HAL_OK) { ota_user_nor_select(0); return -1; }
    return ota_user_nor_select(0); /* release CS even on success path error */
}

static uint32_t now_ms(void) { return ota_bsp_now_ms(); }

static void fail_before_stream(uint32_t err)
{
    nor_mailbox.magic = NOR_MAILBOX_MAGIC;
    nor_mailbox.version = NOR_MAILBOX_VERSION;
    nor_mailbox.session = 0u;
    nor_mailbox.seq = 0u;
    nor_mailbox.offset = 0u;
    nor_mailbox.length = 0u;
    nor_mailbox.error = err;
    nor_mailbox.crc32 = 0u;
    nor_mailbox.ack = 0u;
    nor_mailbox.status = MB_STATUS_ERROR;
    for (;;) ota_bsp_idle();
}

int main(void)
{
    ota_board_config_t config;
    int result;

    ota_arch_irq_enable();
    if (ota_bsp_init(&config) || !config.board_verified) fail_before_stream(MB_ERR_INIT);
    /* The reference BSP already read the JEDEC ID (0x9F) during init. */
    if (ota_explorer_probe.jedec_id != 0x00EF4018u) fail_before_stream(MB_ERR_JEDEC);

    nor_stream_init(&stream, &nor_mailbox, nor_read_chunk, now_ms);
    for (;;) {
        result = nor_stream_service(&stream, 30000u); /* 30 s ACK deadline per block */
        if (result < 0) { for (;;) ota_bsp_idle(); }   /* ERROR already published */
        if (result > 0) { for (;;) ota_bsp_idle(); }   /* DONE already published */
        ota_bsp_idle();
    }
}
