#ifndef OTA_SPI_NOR_H
#define OTA_SPI_NOR_H
#include <stdint.h>

/* Conservative W25Q128JV-IQ/JQ profile, 1-1-1 SPI, 24-bit addresses.
 * JEDEC EF4018 is necessary but does not identify every silicon revision.
 * Confirm the actual part/voltage/datasheet before wiring this into a BSP. */
#define NOR_JEDEC_ID       0xEF4018u
#define NOR_CAPACITY_BYTES 0x1000000u
#define NOR_PAGE_BYTES     256u
#define NOR_SECTOR_BYTES   4096u
#define NOR_OK             0
#define NOR_ERR_ARGUMENT  -1
#define NOR_ERR_IO        -2
#define NOR_ERR_ID        -3
#define NOR_ERR_TIMEOUT   -4
#define NOR_ERR_PROTECTED -5
#define NOR_ERR_WEL       -6
#define NOR_ERR_VERIFY    -7
#define NOR_ERR_SERVICE   -8
#define NOR_ERR_ERASE_REQUIRED -9

typedef struct {
    int (*select)(void *user,int active); /* active=1: /CS low; 0: /CS high */
    /* Clock n bytes, transmit FF when tx==NULL, discard RX when rx==NULL.
     * Bound each call by timeout_ms. No overlapping SPI users are allowed. */
    int (*transfer)(void *user,const uint8_t *tx,uint8_t *rx,uint32_t n,uint32_t timeout_ms);
    uint32_t (*now_ms)(void *user);
    int (*wait_ms)(void *user,uint32_t ms);
    int (*service)(void *user); /* optional, CAN/pending only; must not use SPI */
    void *user;
} nor_spi_bus_t;
typedef struct {
    uint32_t io_ms,program_ms,erase_ms,probe_ms;
} nor_spi_limits_t;
typedef struct {
    nor_spi_bus_t bus;
    nor_spi_limits_t limits;
    uint32_t jedec_id,capacity_bytes;
    uint8_t ready,configured;
} nor_spi_t;

int nor_spi_init(nor_spi_t *d,const nor_spi_bus_t *bus,const nor_spi_limits_t *limits);
int nor_spi_probe(nor_spi_t *d); /* reads only; no reset/unlock/status-register write */
int nor_spi_read(nor_spi_t *d,uint32_t address,uint8_t *out,uint32_t n);
int nor_spi_program_page(nor_spi_t *d,uint32_t address,const uint8_t *data,uint32_t n);
int nor_spi_erase_sector(nor_spi_t *d,uint32_t address);
#endif
