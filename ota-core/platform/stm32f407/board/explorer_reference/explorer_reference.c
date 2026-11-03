/* Original project glue, not copied ATK example code.
 * Sources and physical-board limitations: docs/EXPLORER_REFERENCE.md.
 * No persistent storage writes, application handoff or watchdog enable. */
#include "explorer_reference.h"
#include "ota/crc32.h"
#include <string.h>

static CAN_HandleTypeDef can1;
static UART_HandleTypeDef uart1;
static I2C_HandleTypeDef i2c1;
static SPI_HandleTypeDef spi1;
static int initialized,uart_ready;
ota_explorer_probe_t ota_explorer_probe;

static int clock_init(void)
{
    RCC_OscInitTypeDef oscillator;
    RCC_ClkInitTypeDef clocks;
    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);
    memset(&oscillator,0,sizeof oscillator);
    oscillator.OscillatorType=RCC_OSCILLATORTYPE_HSE;
    oscillator.HSEState=RCC_HSE_ON;
    oscillator.PLL.PLLState=RCC_PLL_ON;
    oscillator.PLL.PLLSource=RCC_PLLSOURCE_HSE;
    oscillator.PLL.PLLM=8u; oscillator.PLL.PLLN=336u;
    oscillator.PLL.PLLP=RCC_PLLP_DIV2; oscillator.PLL.PLLQ=7u;
    if(HAL_RCC_OscConfig(&oscillator)!=HAL_OK) return -1;
    memset(&clocks,0,sizeof clocks);
    clocks.ClockType=RCC_CLOCKTYPE_SYSCLK|RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
    clocks.SYSCLKSource=RCC_SYSCLKSOURCE_PLLCLK;
    clocks.AHBCLKDivider=RCC_SYSCLK_DIV1;
    clocks.APB1CLKDivider=RCC_HCLK_DIV4; clocks.APB2CLKDivider=RCC_HCLK_DIV2;
    if(HAL_RCC_ClockConfig(&clocks,FLASH_LATENCY_5)!=HAL_OK) return -1;
    ota_explorer_probe.hclk_hz=HAL_RCC_GetHCLKFreq();
    ota_explorer_probe.pclk1_hz=HAL_RCC_GetPCLK1Freq();
    ota_explorer_probe.pclk2_hz=HAL_RCC_GetPCLK2Freq();
    return ota_explorer_probe.hclk_hz==OTA_EXPLORER_HCLK_HZ &&
        ota_explorer_probe.pclk1_hz==OTA_EXPLORER_PCLK1_HZ &&
        ota_explorer_probe.pclk2_hz==OTA_EXPLORER_PCLK2_HZ ? 0 : -1;
}

static void gpio_init(void)
{
    GPIO_InitTypeDef gpio;
    __HAL_RCC_GPIOA_CLK_ENABLE(); __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOE_CLK_ENABLE(); __HAL_RCC_GPIOF_CLK_ENABLE(); __HAL_RCC_GPIOG_CLK_ENABLE();
    /* Select inactive levels BEFORE changing modes. Keep the shared NRF SPI
     * device deselected, and leave SWD PA13/PA14 untouched. */
    HAL_GPIO_WritePin(GPIOF,GPIO_PIN_9|GPIO_PIN_10,GPIO_PIN_SET);
    HAL_GPIO_WritePin(GPIOB,GPIO_PIN_14,GPIO_PIN_SET);
    HAL_GPIO_WritePin(GPIOG,GPIO_PIN_7,GPIO_PIN_SET);
    HAL_GPIO_WritePin(GPIOG,GPIO_PIN_6,GPIO_PIN_RESET);
    memset(&gpio,0,sizeof gpio);gpio.Mode=GPIO_MODE_OUTPUT_PP;
    gpio.Pull=GPIO_NOPULL;gpio.Speed=GPIO_SPEED_FREQ_LOW;
    gpio.Pin=GPIO_PIN_9|GPIO_PIN_10;HAL_GPIO_Init(GPIOF,&gpio);
    gpio.Pin=GPIO_PIN_14;HAL_GPIO_Init(GPIOB,&gpio);
    gpio.Pin=GPIO_PIN_6|GPIO_PIN_7;HAL_GPIO_Init(GPIOG,&gpio);
    gpio.Pin=GPIO_PIN_4;gpio.Mode=GPIO_MODE_INPUT;gpio.Pull=GPIO_PULLUP;
    HAL_GPIO_Init(GPIOE,&gpio); /* KEY0, active low */
    gpio.Mode=GPIO_MODE_AF_PP;gpio.Pull=GPIO_PULLUP;gpio.Speed=GPIO_SPEED_FREQ_VERY_HIGH;
    gpio.Pin=GPIO_PIN_9|GPIO_PIN_10;gpio.Alternate=GPIO_AF7_USART1;
    HAL_GPIO_Init(GPIOA,&gpio);
    gpio.Pin=GPIO_PIN_11|GPIO_PIN_12;gpio.Alternate=GPIO_AF9_CAN1;
    HAL_GPIO_Init(GPIOA,&gpio);
    /* PB3/PB4 are JTAG/SWO pins on reset. Use a SWD debugger. */
    DBGMCU->CR&=~DBGMCU_CR_TRACE_IOEN;
    gpio.Pin=GPIO_PIN_3|GPIO_PIN_4|GPIO_PIN_5;gpio.Alternate=GPIO_AF5_SPI1;
    gpio.Pull=GPIO_NOPULL;HAL_GPIO_Init(GPIOB,&gpio);
    gpio.Pin=GPIO_PIN_8|GPIO_PIN_9;gpio.Alternate=GPIO_AF4_I2C1;
    gpio.Mode=GPIO_MODE_AF_OD;gpio.Pull=GPIO_NOPULL;
    HAL_GPIO_Init(GPIOB,&gpio); /* External pull-ups are in both reference diagrams. */
}

static int peripherals_init(void)
{
    __HAL_RCC_USART1_CLK_ENABLE();__HAL_RCC_CAN1_CLK_ENABLE();
    __HAL_RCC_SPI1_CLK_ENABLE();__HAL_RCC_I2C1_CLK_ENABLE();
    uart1.Instance=USART1;uart1.Init.BaudRate=115200u;
    uart1.Init.WordLength=UART_WORDLENGTH_8B;uart1.Init.StopBits=UART_STOPBITS_1;
    uart1.Init.Parity=UART_PARITY_NONE;uart1.Init.Mode=UART_MODE_TX_RX;
    uart1.Init.HwFlowCtl=UART_HWCONTROL_NONE;uart1.Init.OverSampling=UART_OVERSAMPLING_16;
    if(HAL_UART_Init(&uart1)!=HAL_OK) return -1;
    uart_ready=1;
    can1.Instance=CAN1;can1.Init.Prescaler=OTA_EXPLORER_CAN_PRESCALER;
    can1.Init.Mode=OTA_EXPLORER_CAN_LOOPBACK?CAN_MODE_SILENT_LOOPBACK:CAN_MODE_NORMAL;
    can1.Init.SyncJumpWidth=CAN_SJW_1TQ;can1.Init.TimeSeg1=CAN_BS1_11TQ;
    can1.Init.TimeSeg2=CAN_BS2_2TQ;can1.Init.TimeTriggeredMode=DISABLE;
    can1.Init.AutoBusOff=ENABLE;can1.Init.AutoWakeUp=DISABLE;
    can1.Init.AutoRetransmission=ENABLE;can1.Init.ReceiveFifoLocked=ENABLE;
    can1.Init.TransmitFifoPriority=ENABLE;
    if(HAL_CAN_Init(&can1)!=HAL_OK) return -1;
    HAL_NVIC_SetPriority(CAN1_RX0_IRQn,5u,0u);
    HAL_NVIC_ClearPendingIRQ(CAN1_RX0_IRQn);HAL_NVIC_EnableIRQ(CAN1_RX0_IRQn);
    spi1.Instance=SPI1;spi1.Init.Mode=SPI_MODE_MASTER;
    spi1.Init.Direction=SPI_DIRECTION_2LINES;spi1.Init.DataSize=SPI_DATASIZE_8BIT;
    spi1.Init.CLKPolarity=SPI_POLARITY_LOW;spi1.Init.CLKPhase=SPI_PHASE_1EDGE;
    spi1.Init.NSS=SPI_NSS_SOFT;spi1.Init.BaudRatePrescaler=SPI_BAUDRATEPRESCALER_16;
    spi1.Init.FirstBit=SPI_FIRSTBIT_MSB;spi1.Init.TIMode=SPI_TIMODE_DISABLE;
    spi1.Init.CRCCalculation=SPI_CRCCALCULATION_DISABLE;spi1.Init.CRCPolynomial=7u;
    if(HAL_SPI_Init(&spi1)!=HAL_OK) return -1; /* 84 MHz / 16 = 5.25 MHz. */
    i2c1.Instance=I2C1;i2c1.Init.ClockSpeed=100000u;i2c1.Init.DutyCycle=I2C_DUTYCYCLE_2;
    i2c1.Init.OwnAddress1=0u;i2c1.Init.AddressingMode=I2C_ADDRESSINGMODE_7BIT;
    i2c1.Init.DualAddressMode=I2C_DUALADDRESS_DISABLE;i2c1.Init.OwnAddress2=0u;
    i2c1.Init.GeneralCallMode=I2C_GENERALCALL_DISABLE;i2c1.Init.NoStretchMode=I2C_NOSTRETCH_DISABLE;
    return HAL_I2C_Init(&i2c1)==HAL_OK?0:-1;
}

static void read_storage_identity(void)
{
    uint8_t command[4]={0x9Fu,0xFFu,0xFFu,0xFFu},result[4]={0u,0u,0u,0u};
    HAL_StatusTypeDef status;
    /* RDID is the only NOR opcode emitted by this BSP. No WREN, reset,
     * status-register write, page program or erase command is sent. */
    HAL_GPIO_WritePin(GPIOB,GPIO_PIN_14,GPIO_PIN_RESET);
    status=HAL_SPI_TransmitReceive(&spi1,command,result,sizeof command,20u);
    HAL_GPIO_WritePin(GPIOB,GPIO_PIN_14,GPIO_PIN_SET);
    ota_explorer_probe.jedec_status=status==HAL_OK?0:-1;
    if(status==HAL_OK) {
        uint32_t id=((uint32_t)result[1]<<16)|((uint32_t)result[2]<<8)|result[3];
        ota_explorer_probe.jedec_id=id;
        if(!id || id==0xFFFFFFu) ota_explorer_probe.jedec_status=-1;
    }
    memset(ota_explorer_probe.eeprom,0xFF,sizeof ota_explorer_probe.eeprom);
    status=HAL_I2C_Mem_Read(&i2c1,0x50u<<1,0u,I2C_MEMADD_SIZE_8BIT,
                           ota_explorer_probe.eeprom,sizeof ota_explorer_probe.eeprom,100u);
    ota_explorer_probe.eeprom_status=status==HAL_OK?0:-1;
    if(status==HAL_OK) ota_explorer_probe.eeprom_crc32=crc32_calc(
        ota_explorer_probe.eeprom,sizeof ota_explorer_probe.eeprom);
}

static void log_hex(const char *label,uint32_t value)
{
    static const char hex[]="0123456789ABCDEF";
    char line[64];size_t n=strlen(label);uint32_t i;
    if(n>sizeof line-12u) return;
    memcpy(line,label,n);line[n++]='=';line[n++]='0';line[n++]='x';
    for(i=0u;i<8u;i++) line[n++]=hex[(value>>(28u-4u*i))&15u];
    line[n]=0;ota_user_log(line);
}

int ota_user_generated_init(void)
{
    initialized=0;uart_ready=0;memset(&ota_explorer_probe,0,sizeof ota_explorer_probe);
    if(!OTA_EXPLORER_REFERENCE_PINS) return -1;
    /* IDs check consistency, not authenticity or package pin count. */
    ota_explorer_probe.device_id=HAL_GetDEVID();
    ota_explorer_probe.revision_id=HAL_GetREVID();
    ota_explorer_probe.flash_bytes=(uint32_t)(*(volatile uint16_t *)FLASHSIZE_BASE)*1024u;
    if(ota_explorer_probe.device_id!=0x413u || ota_explorer_probe.flash_bytes!=FLASH_TOTAL_SIZE)
        return -1;
    if(HAL_Init()!=HAL_OK || clock_init()) return -1;
    gpio_init();if(peripherals_init()) return -1;
    initialized=1;read_storage_identity();
    ota_user_log("ATK Explorer reference pins; storage writes disabled");
    ota_user_log(OTA_EXPLORER_CAN_LOOPBACK?"CAN: silent internal loopback":"CAN: normal physical 500 kbit/s");
    log_hex("device_id",ota_explorer_probe.device_id);
    log_hex("flash_bytes",ota_explorer_probe.flash_bytes);
    log_hex("hclk_hz",ota_explorer_probe.hclk_hz);
    log_hex("pclk1_hz",ota_explorer_probe.pclk1_hz);
    log_hex("jedec_id",ota_explorer_probe.jedec_id);
    ota_user_log(ota_explorer_probe.jedec_status?"NOR RDID failed":"NOR RDID read; fitted chip profile unconfirmed");
    ota_user_log(ota_explorer_probe.eeprom_status?"EEPROM read failed":"EEPROM 256-byte read complete");
    log_hex("eeprom_crc32",ota_explorer_probe.eeprom_crc32);
    return 0;
}
void ota_user_config(ota_board_config_t *config)
{
    memset(config,0,sizeof *config);config->board_verified=(uint8_t)initialized;
    /* Even successful ID/EEPROM reads cannot authorize the update engine. */
    config->storage_verified=0u;
}
CAN_HandleTypeDef *ota_user_can(void) { return &can1; }
I2C_HandleTypeDef *ota_user_i2c(void) { return &i2c1; }
SPI_HandleTypeDef *ota_user_spi(void) { return &spi1; }
uint8_t ota_user_eeprom_address7(void) { return 0x50u; }
uint32_t ota_user_flash_voltage_range(void) { return FLASH_VOLTAGE_RANGE_3; }
int ota_user_nor_select(int active)
{
    if(!initialized) return -1;
    HAL_GPIO_WritePin(GPIOB,GPIO_PIN_14,active?GPIO_PIN_RESET:GPIO_PIN_SET);return 0;
}
void ota_user_log(const char *message)
{
    static const uint8_t end[2]={'\r','\n'};
    size_t length;
    if(!uart_ready || !message) return;
    length=strlen(message);if(length>192u) length=192u;
    (void)HAL_UART_Transmit(&uart1,(uint8_t *)message,(uint16_t)length,30u);
    (void)HAL_UART_Transmit(&uart1,(uint8_t *)end,sizeof end,5u);
}
void ota_user_heartbeat(void) { if(initialized) HAL_GPIO_TogglePin(GPIOF,GPIO_PIN_9); }
int ota_user_force_loader(void) { return 1; }
int ota_user_app_healthy(void) { return 0; }
void ota_user_can_rx(const can_frame_t *frame) { ota_bootloader_can_rx(frame); }
int ota_user_quiesce_peripherals(void)
{
    HAL_NVIC_DisableIRQ(CAN1_RX0_IRQn);HAL_NVIC_ClearPendingIRQ(CAN1_RX0_IRQn);
    if(ota_user_nor_select(0) || HAL_SPI_DeInit(&spi1)!=HAL_OK ||
       HAL_I2C_DeInit(&i2c1)!=HAL_OK || HAL_UART_DeInit(&uart1)!=HAL_OK) return -1;
    uart_ready=0;return 0;
}
/* An explicit supported chip driver and measured timing are a later stage. */
int ota_user_nor_read(uint32_t a,uint8_t *p,uint32_t n)
{ (void)a;(void)p;(void)n;return -1; }
int ota_user_nor_erase_4k(uint32_t a) { (void)a;return -1; }
int ota_user_nor_page_program(uint32_t a,const uint8_t *p,uint32_t n)
{ (void)a;(void)p;(void)n;return -1; }
