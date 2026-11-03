# LCD 驱动说明（NT35310 / ATK-MD0350 3.5"）

> 这是从零写的独立驱动，**不依赖正点原子例程**，只用 ST HAL + FSMC 打通 3.5 寸屏。
> 2026-11-03 实机验证：全屏填充红色成功。

---

## 一、文件清单

| 文件 | 作用 |
|---|---|
| `main.c` | 探测 + 初始化 + 填充固件 |
| `build.py` | 独立构建脚本（不污染其他工程） |
| `link.ld` | 链接脚本（Boot 区 64 KiB，RAM 128 KiB） |
| `stm32f4xx_hal_conf.h` | 最小 HAL 配置（**必须包含 `HAL_SRAM_MODULE_ENABLED`**） |

---

## 二、硬件连接

| 信号 | 引脚 | FSMC 功能 |
|---|---|---|
| CS | PG12 | FSMC_NE4 |
| RS | PF12 | FSMC_A6 |
| WR | PD5 | FSMC_NWE |
| RD | PD4 | FSMC_NOE |
| D0..D15 | PD14 PD15 PD0 PD1 PE7..PE15 PD8 PD9 PD10 | FSMC_D0..D15 |
| BL | **PB15** | 高电平点亮 |
| RST | 板级复位网络 | 无需软件控制 |

**FSMC 地址（16 位模式）**：

```c
#define LCD_BASE   0x6C000000u                 /* Bank1, NE4 */
#define LCD_REG    (*(volatile uint16_t *)LCD_BASE)            /* A6 = 0 → 命令 */
#define LCD_RAM    (*(volatile uint16_t *)(LCD_BASE + 0x80u))  /* A6 = 1 → 数据 */
```

---

## 三、最小可用初始化序列（已验证）

```c
/* 1. 电源控制 —— 缺了这段画面全白 */
LCD_REG = 0xC0; LCD_RAM = 0x00; LCD_RAM = 0x8B; LCD_RAM = 0x8B;
LCD_REG = 0xC1; LCD_RAM = 0x00;
LCD_REG = 0xC2; LCD_RAM = 0x17; LCD_RAM = 0x02;
LCD_REG = 0xC5; LCD_RAM = 0x00; LCD_RAM = 0x40; LCD_RAM = 0x80;

/* 2. 接口配置 */
LCD_REG = 0x36; LCD_RAM = 0x00;    /* MADCTL：默认方向 */
LCD_REG = 0x3A; LCD_RAM = 0x55;    /* 16 bit/pixel，RGB565 */

/* 3. 唤醒 */
LCD_REG = 0x11;                    /* Sleep Out */

/* 4. 等待约 120 ms（可用全屏填充代替，见下） */

/* 5. 打开显示 */
LCD_REG = 0x29;                    /* Display On */
```

**填充矩形的写法**：

```c
static void lcd_fill_area(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1,
                          uint16_t colour)
{
    uint32_t n = (uint32_t)(x1 - x0 + 1u) * (uint32_t)(y1 - y0 + 1u);

    LCD_REG = 0x2A;                 /* 列地址 */
    LCD_RAM = (uint16_t)(x0 >> 8); LCD_RAM = (uint16_t)(x0 & 0xFFu);
    LCD_RAM = (uint16_t)(x1 >> 8); LCD_RAM = (uint16_t)(x1 & 0xFFu);
    LCD_REG = 0x2B;                 /* 行地址 */
    LCD_RAM = (uint16_t)(y0 >> 8); LCD_RAM = (uint16_t)(y0 & 0xFFu);
    LCD_RAM = (uint16_t)(y1 >> 8); LCD_RAM = (uint16_t)(y1 & 0xFFu);
    LCD_REG = 0x2C;                 /* 写 GRAM */
    while (n-- != 0u) LCD_RAM = colour;
}
```

全屏（320×480）= 153600 次写，在 16 MHz HSI 下约 40–50 ms，**刚好充当 `0x11` → `0x29` 所需的稳定延时**。

---

## 四、五个坑（全部实机踩过）

### 坑 1：FSMC 时钟必须手动使能 ⭐ 最隐蔽

`HAL_SRAM_Init()` 把时钟使能交给 `HAL_SRAM_MspInit()`，而它是**空的弱函数**。

不调用 `__HAL_RCC_FSMC_CLK_ENABLE()` 的后果：

- `HAL_SRAM_Init()` **返回 `HAL_OK`**
- 但 `FSMC_BCR4` 恒为 `0x00000000`
- **所有寄存器写入被静默丢弃**，读 ID 全部返回 0

```c
static int fsmc_init(void)
{
    __HAL_RCC_FSMC_CLK_ENABLE();   /* ← 漏了这句，一切白搭 */
    ...
}
```

**排查口诀**：函数返回 OK 但硬件没反应，先查时钟。

### 坑 2：NT35310 必须先配电源

只发 `0x11` + `0x29`，背光亮但画面全白。必须补 `0xC0/C1/C2/C5` 才有图像。

### 坑 3：`0x11` 后需约 120 ms 才能 `0x29`

提前发 Display On 不生效。可用全屏填充自然充当延时（见上）。

### 坑 4：`0x01` 软件复位会清屏

调试时每次重启都发 `0x01`，就会看到"刚刚有画面、重启后又没了"。

**如果只是想保持显示，不要发 `0x01`。**

### 坑 5：Cortex-M 跳转地址 bit0 必须为 1

用调试器写 PC 时，`0x080026A4` 会进 `Default_Handler` 死循环，必须写 `0x080026A5`（Thumb 位）。

---

## 五、构建与烧录

```powershell
# 构建
cd D:\Project\Can\stages\lcd-test
python build.py `
  --gcc "D:\Project\Can\toolchain\xpack-arm-none-eabi-gcc-15.2.1-1.1\bin\arm-none-eabi-gcc.exe" `
  --dfp "D:\Keil\Keil5\ARM\PACK\Keil\STM32F4xx_DFP\2.16.0"

# 产物
# build\lcd_probe_READONLY\lcd_probe_READONLY.bin   （约 2 KB）

# 烧录
$cli = "D:\Keil\CubProgram\bin\STM32_Programmer_CLI.exe"
& $cli -c port=SWD index=0 mode=HOTPLUG freq=1000 `
       -d "build\lcd_probe_READONLY\lcd_probe_READONLY.bin" 0x08000000 -v -rst
```

**注意**：`build.py` 会强制把 `OTA_ENABLE_FLASHING/BOOT_HANDOFF/WATCHDOG` 三个宏设为 0，防止误开写入路径。

---

## 六、运行结果怎么看

固件把结果写进全局结构 `g_lcd_probe`（地址从 ELF 用 `nm` 取，**不要硬编码**）：

```c
typedef struct {
    uint32_t magic;        /* 0x5044434C = 'LCDP' */
    uint32_t id_04;        /* 读 0x04 的结果 */
    uint32_t id_d3;        /* 读 0xD3 的结果 */
    uint32_t id_d4;        /* 读 0xD4 的结果 → 0x5310 = NT35310 */
    uint32_t sram_init;    /* 0 = FSMC 初始化成功 */
    uint32_t backlight;    /* 1 = 背光已驱动 */
    uint32_t hclk_hz;      /* 实际 HCLK */
    uint32_t status;       /* 循环计数 / 进度标记 */
} lcd_probe_t;
```

读取命令：

```powershell
$addr = (nm 输出的 g_lcd_probe 地址)
& $cli -c port=SWD index=0 mode=HOTPLUG freq=1000 `
       -coreReg MSP=0x20020000 -coreReg PC=$reset_thumb -run `
       -u $addr 0x20 out.bin
```

**判读**：

| 字段 | 期望 | 含义 |
|---|---|---|
| `magic` | `0x5044434C` | 固件跑起来了 |
| `id_d4` | `0x00015310` | NT35310 响应了 |
| `sram_init` | `0` | FSMC 初始化成功 |
| `backlight` | `1` | 背光已开 |

---

## 七、当前限制

- 只实现了"显示纯色"，**没有字库、没有显示字符/图形 API**。
- **没有做触摸**（电阻触摸需要接 ADC 或触摸控制器）。
- 颜色格式只支持 RGB565。
- 时序参数基于 16 MHz HSI（`DataSetupTime = 3`）；若改用 168 MHz，需要重新计算并加大该值。

如果需要完整功能（字符、图片、触摸），建议直接使用正点原子例程里的 `lcd.c`（A 盘资料）。
