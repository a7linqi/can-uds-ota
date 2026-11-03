/* OPTIONAL replacement for the three ota_user_nor_* hooks.
 * Keep out of the unconfigured project until the actual chip is confirmed.
 * Requires SPI master, 8-bit, 2 lines, software NSS, mode 0/3, <=10 MHz. */
#include "board_hal_user.h"
#include "ota/spi_nor.h"
#include <string.h>
static nor_spi_t chip;
static int spi_configuration_ok(SPI_HandleTypeDef *h)
{
    uint32_t peripheral,divider;
    if(!h || h->Init.Mode!=SPI_MODE_MASTER || h->Init.Direction!=SPI_DIRECTION_2LINES ||
       h->Init.DataSize!=SPI_DATASIZE_8BIT || h->Init.FirstBit!=SPI_FIRSTBIT_MSB ||
       h->Init.NSS!=SPI_NSS_SOFT || h->Init.TIMode!=SPI_TIMODE_DISABLE ||
       h->Init.CRCCalculation!=SPI_CRCCALCULATION_DISABLE) return 0;
    if(!((h->Init.CLKPolarity==SPI_POLARITY_LOW && h->Init.CLKPhase==SPI_PHASE_1EDGE) ||
         (h->Init.CLKPolarity==SPI_POLARITY_HIGH && h->Init.CLKPhase==SPI_PHASE_2EDGE))) return 0;
    if(h->Init.BaudRatePrescaler&~SPI_CR1_BR) return 0;
    if(h->Instance==SPI1) peripheral=HAL_RCC_GetPCLK2Freq();
    else if(h->Instance==SPI2 || h->Instance==SPI3) peripheral=HAL_RCC_GetPCLK1Freq();
    else return 0;
    divider=2u<<((h->Init.BaudRatePrescaler&SPI_CR1_BR)>>3);
    return peripheral && peripheral/divider<=10000000u;
}
static int select_chip(void *user,int active)
{ (void)user; return ota_user_nor_select(active); }
static uint32_t now_ms(void *user) { (void)user; return HAL_GetTick(); }
static int wait_ms(void *user,uint32_t ms) { (void)user; HAL_Delay(ms); return 0; }
static int service(void *user) { (void)user; return ota_firmware_service(); }
static int transfer(void *user,const uint8_t *tx,uint8_t *rx,uint32_t n,uint32_t timeout)
{
    SPI_HandleTypeDef *h=ota_user_spi();
    uint8_t dummy[32];
    uint32_t start=HAL_GetTick();
    (void)user;
    if(!h || (!tx && !rx) || !n || !timeout) return -1;
    memset(dummy,0xFF,sizeof dummy);
    while(n) {
        uint32_t elapsed=HAL_GetTick()-start;
        uint16_t k=(uint16_t)(n>sizeof dummy?sizeof dummy:n);
        HAL_StatusTypeDef result;
        if(elapsed>=timeout) return -1;
        if(rx) result=HAL_SPI_TransmitReceive(h,(uint8_t *)(tx?tx:dummy),rx,k,timeout-elapsed);
        else result=HAL_SPI_Transmit(h,(uint8_t *)tx,k,timeout-elapsed);
        if(result!=HAL_OK) return -1;
        if(tx) tx+=k;
        if(rx) rx+=k;
        n-=k;
    }
    return 0;
}
int ota_hal_nor_probe(void)
{
    nor_spi_bus_t bus;
    memset(&chip,0,sizeof chip);
    if(!spi_configuration_ok(ota_user_spi())) return -1;
    memset(&bus,0,sizeof bus); bus.select=select_chip; bus.transfer=transfer;
    bus.now_ms=now_ms; bus.wait_ms=wait_ms; bus.service=service;
    if(nor_spi_init(&chip,&bus,0)) return -1;
    return nor_spi_probe(&chip);
}
uint32_t ota_hal_nor_capacity(void) { return chip.ready?chip.capacity_bytes:0u; }
int ota_user_nor_read(uint32_t address,uint8_t *out,uint32_t n)
{ return nor_spi_read(&chip,address,out,n); }
int ota_user_nor_erase_4k(uint32_t address)
{ return nor_spi_erase_sector(&chip,address); }
int ota_user_nor_page_program(uint32_t address,const uint8_t *data,uint32_t n)
{ return nor_spi_program_page(&chip,address,data,n); }
