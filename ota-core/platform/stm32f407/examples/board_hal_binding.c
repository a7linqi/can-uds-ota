/* Reference binding, excluded from default Keil stub targets.
 * Syntax checked against ST STM32F4xx_DFP 2.16.0 HAL headers.
 * Requires board_hal_user.h hooks and the board's generated HAL project.
 * SPI chip protocol remains a user NOR-driver hook, not an assumed W25Q. */
#include "board_hal_user.h"
#include "ota_stm32.h"
#include <string.h>
static IWDG_HandleTypeDef watchdog;
static int watchdog_started;
int ota_bsp_watchdog_start(void)
{
    watchdog.Instance=IWDG; watchdog.Init.Prescaler=IWDG_PRESCALER_256;
    watchdog.Init.Reload=4095u;
    if(HAL_IWDG_Init(&watchdog)!=HAL_OK) return -1;
    watchdog_started=1; return 0;
}
int ota_bsp_watchdog_reload(void)
{ return watchdog_started && HAL_IWDG_Refresh(&watchdog)==HAL_OK?0:-1; }
int ota_bsp_init(ota_board_config_t *c)
{
    memset(c,0,sizeof *c);
    if(ota_user_generated_init()) return -1;
    ota_user_config(c);
    c->detected_flash_bytes=(uint32_t)(*(volatile uint16_t *)FLASHSIZE_BASE)*1024u;
    return c->board_verified ? 0 : -1;
}
uint32_t ota_bsp_now_ms(void) { return HAL_GetTick(); }
void ota_bsp_idle(void) { ota_arch_wait(); }
void ota_bsp_log(const char *s) { ota_user_log(s); }
void ota_bsp_heartbeat(void) { ota_user_heartbeat(); }
int ota_bsp_force_loader(void) { return ota_user_force_loader(); }
int ota_bsp_app_healthy(void) { return ota_user_app_healthy(); }
void ota_bsp_reset(void) { ota_arch_reset(); }
int ota_bsp_can_start(void)
{
    CAN_HandleTypeDef *h=ota_user_can();
    CAN_FilterTypeDef f;
    if(!h || h->Instance!=CAN1) return -1;
    memset(&f,0,sizeof f); f.FilterBank=0u; f.FilterMode=CAN_FILTERMODE_IDMASK;
    f.FilterScale=CAN_FILTERSCALE_32BIT; f.FilterIdHigh=0x7E0u<<5;
    f.FilterMaskIdHigh=0x7FFu<<5; f.FilterMaskIdLow=6u; /* standard data frames */
    f.FilterFIFOAssignment=CAN_RX_FIFO0; f.FilterActivation=ENABLE;
    f.SlaveStartFilterBank=14u;
    if(HAL_CAN_ConfigFilter(h,&f)!=HAL_OK || HAL_CAN_Start(h)!=HAL_OK) return -1;
    return HAL_CAN_ActivateNotification(h,CAN_IT_RX_FIFO0_MSG_PENDING)==HAL_OK?0:-1;
}
int ota_bsp_can_try_tx(const can_frame_t *f)
{
    CAN_HandleTypeDef *h=ota_user_can();
    CAN_TxHeaderTypeDef t;
    uint32_t mailbox;
    if(!h || HAL_CAN_GetState(h)!=HAL_CAN_STATE_LISTENING) return -1;
    if(!HAL_CAN_GetTxMailboxesFreeLevel(h)) return 1;
    memset(&t,0,sizeof t);
    t.IDE=f->extended?CAN_ID_EXT:CAN_ID_STD; t.RTR=CAN_RTR_DATA; t.DLC=f->dlc;
    t.StdId=f->id; t.ExtId=f->id; t.TransmitGlobalTime=DISABLE;
    return HAL_CAN_AddTxMessage(h,&t,(uint8_t *)f->data,&mailbox)==HAL_OK?0:-1;
}
int ota_bsp_can_tx_idle(void)
{
    CAN_HandleTypeDef *h=ota_user_can();
    return h && !HAL_CAN_IsTxMessagePending(h,CAN_TX_MAILBOX0|CAN_TX_MAILBOX1|CAN_TX_MAILBOX2);
}
void ota_hal_can_fifo0_callback(CAN_HandleTypeDef *h)
{
    if(!h || h!=ota_user_can()) return;
    while(HAL_CAN_GetRxFifoFillLevel(h,CAN_RX_FIFO0)) {
        CAN_RxHeaderTypeDef r;
        can_frame_t f;
        memset(&f,0,sizeof f);
        if(HAL_CAN_GetRxMessage(h,CAN_RX_FIFO0,&r,f.data)!=HAL_OK) break;
        if(r.RTR!=CAN_RTR_DATA || r.DLC>8u) continue;
        f.id=r.IDE==CAN_ID_EXT?r.ExtId:r.StdId;
        f.extended=r.IDE==CAN_ID_EXT; f.dlc=(uint8_t)r.DLC;
        ota_user_can_rx(&f); /* enqueue only */
    }
}
int ota_bsp_iflash_read(uint32_t a,uint8_t *out,uint32_t n)
{
    if(a<FLASH_BASE_ADDR || a-FLASH_BASE_ADDR>FLASH_TOTAL_SIZE ||
       n>FLASH_TOTAL_SIZE-(a-FLASH_BASE_ADDR)) return -1;
    memcpy(out,(const void *)a,n); return 0;
}
int ota_bsp_iflash_unlock(void) { return HAL_FLASH_Unlock()==HAL_OK?0:-1; }
int ota_bsp_iflash_lock(void) { return HAL_FLASH_Lock()==HAL_OK?0:-1; }
int ota_bsp_iflash_erase_sector(uint32_t s)
{
    FLASH_EraseInitTypeDef e;
    uint32_t failed,voltage=ota_user_flash_voltage_range();
    if(s<4u || s>(FLASH_TOTAL_SIZE==0x100000u?11u:7u) ||
       voltage>FLASH_VOLTAGE_RANGE_4) return -1; /* unsigned range starts at zero */
    memset(&e,0,sizeof e); e.TypeErase=FLASH_TYPEERASE_SECTORS;
    e.Sector=s; e.NbSectors=1u; e.VoltageRange=voltage;
    return HAL_FLASHEx_Erase(&e,&failed)==HAL_OK?0:-1;
}
int ota_bsp_iflash_program_word(uint32_t a,uint32_t value)
{
    if(a<APP_BASE_ADDR || a>FLASH_BASE_ADDR+FLASH_TOTAL_SIZE-4u || (a&3u)) return -1;
    return HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD,a,value)==HAL_OK?0:-1;
}
int ota_bsp_nor_read(uint32_t a,uint8_t *out,uint32_t n)
{ return ota_user_nor_read(a,out,n); }
int ota_bsp_nor_erase(uint32_t a,uint32_t n)
{ return n==4096u?ota_user_nor_erase_4k(a):-1; }
int ota_bsp_nor_page_program(uint32_t a,const uint8_t *p,uint32_t n)
{ return ota_user_nor_page_program(a,p,n); }
static uint16_t ee_address(void)
{
    uint8_t a=ota_user_eeprom_address7();
    return (a>=0x50u && a<=0x57u)?(uint16_t)(a<<1):0u;
}
int ota_bsp_eeprom_read(uint32_t a,uint8_t *out,uint32_t n)
{
    I2C_HandleTypeDef *h=ota_user_i2c();
    uint16_t device=ee_address();
    if(!h || !device || a>256u || n>256u-a) return -1;
    return HAL_I2C_Mem_Read(h,device,(uint16_t)a,I2C_MEMADD_SIZE_8BIT,out,(uint16_t)n,100u)==HAL_OK?0:-1;
}
int ota_bsp_eeprom_page_write(uint32_t a,const uint8_t *p,uint32_t n)
{
    I2C_HandleTypeDef *h=ota_user_i2c();
    uint16_t device=ee_address();
    uint32_t start;
    if(!h || !device || !n || a>256u || n>256u-a || n>8u-a%8u) return -1;
    if(HAL_I2C_Mem_Write(h,device,(uint16_t)a,I2C_MEMADD_SIZE_8BIT,(uint8_t *)p,(uint16_t)n,100u)!=HAL_OK)
        return -1;
    start=HAL_GetTick();
    do {
        if(ota_firmware_service()) return -1;
        if(HAL_I2C_IsDeviceReady(h,device,1u,5u)==HAL_OK) return 0;
        HAL_Delay(1u);
    } while((uint32_t)(HAL_GetTick()-start)<20u);
    return -1;
}
int ota_bsp_quiesce(void)
{
    CAN_HandleTypeDef *h=ota_user_can();
    /* User must stop all board-specific DMA/IRQ sources first. */
    if(ota_user_quiesce_peripherals()) return -1;
    if(h && HAL_CAN_GetState(h)==HAL_CAN_STATE_LISTENING) {
        if(HAL_CAN_DeactivateNotification(h,CAN_IT_RX_FIFO0_MSG_PENDING)!=HAL_OK) return -1;
        if(HAL_CAN_Stop(h)!=HAL_OK) return -1;
    }
    /* Real CubeMX SystemInit/SystemCoreClock/VTOR setup is required. */
    return HAL_RCC_DeInit()==HAL_OK?0:-1;
}
