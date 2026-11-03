/*
 * LCD probe firmware for ATK-DNF407 V3.3 + ALIENTEK 3.5" TFTLCD.
 *
 * Purpose: prove the panel and its controller are alive WITHOUT knowing the
 * controller part number in advance.
 *   1. drive the backlight (PB15 primary; PB0 also driven, docs disagree)
 *   2. configure FSMC Bank1 NE4 as a 16-bit 8080 parallel bus
 *   3. issue the three common "read display ID" commands
 *   4. park every result in g_lcd_probe for an SWD readback
 *
 * Wiring (16-bit 8080, RST is tied to the board reset net):
 *   CS = PG12 (FSMC_NE4)   RS/A6 = PF12   NWE = PD5   NOE = PD4
 *   D0..D15 = PD14 PD15 PD0 PD1 PE7..PE15 PD8 PD9 PD10
 *
 * This image is read-only with respect to every external device: it only
 * writes LCD command/data registers and reads back. No NOR/EEPROM access.
 */
#include "stm32f4xx_hal.h"
#include <string.h>

#define LCD_BASE   0x6C000000u                 /* FSMC Bank1, NE4 */
#define LCD_REG    (*(volatile uint16_t *)LCD_BASE)
#define LCD_RAM    (*(volatile uint16_t *)(LCD_BASE + 0x80u))  /* A6 = RS */

#define PROBE_MAGIC 0x5044434Cu                /* 'LCDP' */

typedef struct {
    uint32_t magic;        /* 0x5044434C once the probe ran            */
    uint32_t id_04;        /* Read Display Identification (0x04)       */
    uint32_t id_d3;        /* Read ID4 (0xD3), ILI-style controllers   */
    uint32_t id_d4;        /* Read ID  (0xD4), NT-style controllers    */
    uint32_t sram_init;    /* 0 = HAL_SRAM_Init returned HAL_OK        */
    uint32_t backlight;    /* 1 = backlight pins driven high           */
    uint32_t hclk_hz;      /* expected 168000000                       */
    uint32_t status;       /* 0 = probe completed                      */
} lcd_probe_t;

lcd_probe_t g_lcd_probe;

static SRAM_HandleTypeDef hsram;

/*
 * HAL_Init() arms the 1 kHz SysTick interrupt. This image does not link
 * stm32f4xx_it.c, so without this handler the interrupt would fall through to
 * the startup file's Default_Handler (an infinite loop) and main() would never
 * get to run. Keeping the tick is required for HAL timing helpers.
 */
void SysTick_Handler(void)
{
    HAL_IncTick();
}

static void clock_init(void)
{
    RCC_OscInitTypeDef osc;
    RCC_ClkInitTypeDef clk;
    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);
    memset(&osc, 0, sizeof osc);
    osc.OscillatorType = RCC_OSCILLATORTYPE_HSE;
    osc.HSEState = RCC_HSE_ON;
    osc.PLL.PLLState = RCC_PLL_ON;
    osc.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    osc.PLL.PLLM = 8u; osc.PLL.PLLN = 336u;
    osc.PLL.PLLP = RCC_PLLP_DIV2; osc.PLL.PLLQ = 7u;
    (void)HAL_RCC_OscConfig(&osc);
    memset(&clk, 0, sizeof clk);
    clk.ClockType = RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_HCLK |
                    RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    clk.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    clk.AHBCLKDivider = RCC_SYSCLK_DIV1;
    clk.APB1CLKDivider = RCC_HCLK_DIV4;
    clk.APB2CLKDivider = RCC_HCLK_DIV2;
    (void)HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_5);
}

/* PF9 = red LED, used as a "the probe is running" heartbeat. */
static void led_init(void)
{
    GPIO_InitTypeDef g;
    __HAL_RCC_GPIOF_CLK_ENABLE();
    HAL_GPIO_WritePin(GPIOF, GPIO_PIN_9, GPIO_PIN_SET);   /* active low: off */
    memset(&g, 0, sizeof g);
    g.Pin = GPIO_PIN_9;
    g.Mode = GPIO_MODE_OUTPUT_PP;
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOF, &g);
}

static void backlight_init(void)
{
    GPIO_InitTypeDef g;
    __HAL_RCC_GPIOB_CLK_ENABLE();
    /* Drive before switching to output to avoid a low glitch. */
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_15 | GPIO_PIN_0, GPIO_PIN_SET);
    memset(&g, 0, sizeof g);
    g.Pin = GPIO_PIN_15 | GPIO_PIN_0;
    g.Mode = GPIO_MODE_OUTPUT_PP;
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOB, &g);
}

static void fsmc_gpio_init(void)
{
    GPIO_InitTypeDef g;
    __HAL_RCC_GPIOD_CLK_ENABLE();
    __HAL_RCC_GPIOE_CLK_ENABLE();
    __HAL_RCC_GPIOF_CLK_ENABLE();
    __HAL_RCC_GPIOG_CLK_ENABLE();

    memset(&g, 0, sizeof g);
    g.Mode = GPIO_MODE_AF_PP;
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    g.Alternate = GPIO_AF12_FSMC;

    /* D2 D3 D13 D14 D15 on port D */
    g.Pin = GPIO_PIN_0 | GPIO_PIN_1 | GPIO_PIN_8 | GPIO_PIN_9 |
            GPIO_PIN_10 | GPIO_PIN_14 | GPIO_PIN_15;
    HAL_GPIO_Init(GPIOD, &g);
    /* D4..D12 on port E */
    g.Pin = GPIO_PIN_7 | GPIO_PIN_8 | GPIO_PIN_9 | GPIO_PIN_10 |
            GPIO_PIN_11 | GPIO_PIN_12 | GPIO_PIN_13 | GPIO_PIN_14 | GPIO_PIN_15;
    HAL_GPIO_Init(GPIOE, &g);
    /* NOE / NWE */
    g.Pin = GPIO_PIN_4 | GPIO_PIN_5;
    HAL_GPIO_Init(GPIOD, &g);
    /* NE4 chip select */
    g.Pin = GPIO_PIN_12;
    HAL_GPIO_Init(GPIOG, &g);
    /* A6 = register select */
    g.Pin = GPIO_PIN_12;
    HAL_GPIO_Init(GPIOF, &g);
}

static int fsmc_init(void)
{
    FMC_NORSRAM_TimingTypeDef timing;   /* FSMC_* behind the FMC_* aliases */

    /* HAL_SRAM_Init() only calls HAL_SRAM_MspInit() (a weak, empty stub in
     * this image) to gate the peripheral clock. Without the clock the bank
     * registers stay at zero, BCR4 reads 0x00000000 and every LCD access is
     * silently dropped - which is exactly why the IDs came back as zeros. */
    __HAL_RCC_FSMC_CLK_ENABLE();

    hsram.Instance = FMC_NORSRAM_DEVICE;
    hsram.Extended = FMC_NORSRAM_EXTENDED_DEVICE;
    hsram.Init.NSBank = FSMC_NORSRAM_BANK4;
    hsram.Init.DataAddressMux = FSMC_DATA_ADDRESS_MUX_DISABLE;
    hsram.Init.MemoryType = FSMC_MEMORY_TYPE_SRAM;
    hsram.Init.MemoryDataWidth = FSMC_NORSRAM_MEM_BUS_WIDTH_16;
    hsram.Init.BurstAccessMode = FSMC_BURST_ACCESS_MODE_DISABLE;
    hsram.Init.WaitSignalPolarity = FSMC_WAIT_SIGNAL_POLARITY_LOW;
    hsram.Init.WrapMode = FSMC_WRAP_MODE_DISABLE;
    hsram.Init.WaitSignalActive = FSMC_WAIT_TIMING_BEFORE_WS;
    hsram.Init.WriteOperation = FSMC_WRITE_OPERATION_ENABLE;
    hsram.Init.WaitSignal = FSMC_WAIT_SIGNAL_DISABLE;
    hsram.Init.ExtendedMode = FSMC_EXTENDED_MODE_DISABLE;
    hsram.Init.AsynchronousWait = FSMC_ASYNCHRONOUS_WAIT_DISABLE;
    hsram.Init.WriteBurst = FSMC_WRITE_BURST_DISABLE;
    hsram.Init.ContinuousClock = FSMC_CONTINUOUS_CLOCK_SYNC_ONLY;
    /* STM32F407's FSMC has no WFDIS bit (that is FMC/F429+); 0 is the only
     * meaningful value here, which is what FSMC_WRITE_FIFO_ENABLE expands to. */
    hsram.Init.WriteFifo = FSMC_WRITE_FIFO_ENABLE;
    hsram.Init.PageSize = FSMC_PAGE_SIZE_NONE;

    memset(&timing, 0, sizeof timing);
    timing.AddressSetupTime = 1;
    timing.AddressHoldTime = 0;
    /* Runs from the 16 MHz HSI (no HSE wait), so the cycle time is 62.5 ns.
     * 3 cycles ~ 188 ns is a sane 8080 read pulse; the 15 used at 168 MHz would
     * stretch to almost 1 us here and can lose the controller's response. */
    timing.DataSetupTime = 3;
    timing.BusTurnAroundDuration = 0;
    timing.CLKDivision = 0;
    timing.DataLatency = 0;
    timing.AccessMode = FSMC_ACCESS_MODE_A;

    return HAL_SRAM_Init(&hsram, &timing, &timing) == HAL_OK ? 0 : -1;
}

/* Issue a "read ID" command and collect three payload bytes (24 bit). */
static uint32_t read_id(uint16_t command)
{
    uint32_t value;
    LCD_REG = command;
    (void)LCD_RAM;                       /* dummy read */
    value  = (uint32_t)LCD_RAM << 16;
    value |= (uint32_t)LCD_RAM << 8;
    value |= (uint32_t)LCD_RAM;
    return value;
}

/* Fill an axis-aligned rectangle with one RGB565 colour. */
static void lcd_fill_area(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1,
                          uint16_t colour)
{
    uint32_t n = (uint32_t)(x1 - x0 + 1u) * (uint32_t)(y1 - y0 + 1u);

    LCD_REG = 0x2Au;                     /* column address set */
    LCD_RAM = (uint16_t)(x0 >> 8); LCD_RAM = (uint16_t)(x0 & 0xFFu);
    LCD_RAM = (uint16_t)(x1 >> 8); LCD_RAM = (uint16_t)(x1 & 0xFFu);
    LCD_REG = 0x2Bu;                     /* page address set */
    LCD_RAM = (uint16_t)(y0 >> 8); LCD_RAM = (uint16_t)(y0 & 0xFFu);
    LCD_RAM = (uint16_t)(y1 >> 8); LCD_RAM = (uint16_t)(y1 & 0xFFu);
    LCD_REG = 0x2Cu;                     /* memory write */
    while (n-- != 0u) {
        LCD_RAM = colour;
    }
}

int main(void)
{
    volatile uint32_t i;

    /* BOOT0 high maps the vector table to 0x00000000 (the system bootloader),
     * which makes interrupts fetch the wrong handlers when this image is
     * entered via a debugger jump. Relocate VTOR to the image base so the
     * probe also runs when started with an explicit MSP/PC write. */
    SCB->VTOR = 0x08000000u;

    /* First statement: proves main() was actually reached. */
    g_lcd_probe.magic = PROBE_MAGIC;

    /* Deliberately NO HAL_Init/clock_init: waiting for the 8 MHz HSE to lock
     * takes up to a second, and every debugger read halts the core, so the
     * probe would never finish inside one halt window. Everything below runs
     * from the 16 MHz HSI and completes in microseconds; the FSMC timings are
     * conservative enough that the slower HCLK only adds margin. */
    led_init();
    backlight_init();
    g_lcd_probe.backlight = 1u;

    fsmc_gpio_init();
    g_lcd_probe.sram_init = (uint32_t)fsmc_init();

    if (g_lcd_probe.sram_init == 0u) {
        /* Deliberately NO software reset (0x01): it blanks the panel every
         * time the debugger reconnects, which is exactly why the red patch
         * disappeared after the first successful run. */
        g_lcd_probe.status = 0x10u;
        LCD_REG = 0x11u;                       /* sleep out */
        g_lcd_probe.status = 0x11u;
        LCD_REG = 0x3Au; LCD_RAM = 0x55u;      /* 16 bit/pixel, RGB565 */
        g_lcd_probe.status = 0x12u;
        LCD_REG = 0x36u; LCD_RAM = 0x00u;      /* MADCTL: default orientation */
        g_lcd_probe.status = 0x13u;
        LCD_REG = 0x29u;                       /* display on */
        g_lcd_probe.status = 0x14u;
    }
    g_lcd_probe.hclk_hz = HAL_RCC_GetHCLKFreq();

    /* Full re-init + full-screen repaint on every iteration.
     *
     * The panel showed a blank screen even though the GRAM writes went
     * through, which means the display was never actually switched on: the
     * NT35310 needs its power-control registers programmed first, and
     * Sleep Out (0x11) needs ~120 ms before Display On (0x29) is honoured.
     * Filling all 320x480 pixels takes tens of milliseconds and doubles as
     * that settling delay, so the sequence below is self-timing. Repeating it
     * makes the whole thing resilient to the debugger halting the core. */
    for (;;) {
        if (g_lcd_probe.sram_init == 0u) {
            /* Power control */
            LCD_REG = 0xC0u; LCD_RAM = 0x00u; LCD_RAM = 0x8Bu; LCD_RAM = 0x8Bu;
            LCD_REG = 0xC1u; LCD_RAM = 0x00u;
            LCD_REG = 0xC2u; LCD_RAM = 0x17u; LCD_RAM = 0x02u;
            LCD_REG = 0xC5u; LCD_RAM = 0x00u; LCD_RAM = 0x40u; LCD_RAM = 0x80u;
            /* Interface */
            LCD_REG = 0x36u; LCD_RAM = 0x00u;      /* MADCTL: default */
            LCD_REG = 0x3Au; LCD_RAM = 0x55u;      /* RGB565 */
            LCD_REG = 0x11u;                       /* sleep out */
            lcd_fill_area(0u, 0u, 319u, 479u, 0xF800u);  /* whole panel red */
            LCD_REG = 0x29u;                       /* display on */
            g_lcd_probe.status++;
        }
        HAL_GPIO_TogglePin(GPIOF, GPIO_PIN_9);
        for (i = 0; i < 20000u; i++) { }
    }
}
