# 板卡实物事实（board-lab 维护）

更新时间：2026-11-03。所有"已确认"均来自 SWD 实测、设备枚举或实物照片，不猜测；看不到的保持 unknown。

## 已确认（有实测证据）

| 项 | 值 | 证据 |
|---|---|---|
| 板卡型号 | 正点原子 ALIENTEK 探索者 STM32F407 | 正面丝印 + 购买记录 |
| PCB 修订号 | **ATK-DNF407 V3.3** | 背面丝印，2026-11-03 实物照片 |
| Device ID / Revision | `0x413` / `0x1007`（Rev 2.0） | SWD 读 `0xE0042000` |
| 内部 Flash | 1 MiB（`0x1FFF7A22`=`0x0400`） | SWD 读，多次复测一致 |
| 封装推断 | ZGT6（1 MiB LQFP144） | 仅容量+系列推断，**丝印未读，不据此定封装** |
| 时钟 | HCLK 168 M / PCLK1 42 M / PCLK2 84 M | 回环固件读回，符合 8 MHz HSE + PLL |
| NOR JEDEC ID | `EF 40 18`（Winbond, SPI NOR, 128 Mbit=16 MiB） | 回环固件 `0x9F` 读回 |
| EEPROM | 读状态 0（应答正常），256 B 快照已存 | 回环固件 I2C 读回 |
| 内部 CAN 静默回环 | stage=3 / passed=16 | 回环固件读回 |
| 目标参考电压 | 3.24 V | ST-Link 连接读数 |
| **LCD 模块** | **ATK-MD0350 V2.4，3.5" 320×480，16 位 8080 并口** | 屏幕标签 `ALIENTEK 3.5' TFTLCD` + 官方原理图 |
| **LCD 驱动 IC** | **NT35310**（读 `0xD4` 返回 `0x5310`） | 2026-11-03 实读 |
| **LCD 显示能力** | **完好，可全屏填充颜色** | 2026-11-03 实机全红验证 |
| LCD 触摸 | 电阻式（XP/Y-/X-/Y+，`ATK-MD0350` 原理图） | 原理图，未实测 |

## LCD 引脚与驱动（2026-11-03 实机验证通过）

| 信号 | 引脚 | 备注 |
|---|---|---|
| CS | PG12（FSMC_NE4） | 低有效，FSMC 自动控制 |
| RS | PF12（FSMC_A6） | 16 位模式地址偏移 `0x80`（命令=基址，数据=基址+0x80） |
| WR | PD5（FSMC_NWE） | |
| RD | PD4（FSMC_NOE） | |
| D0..D15 | PD14 PD15 PD0 PD1 PE7..PE15 PD8 PD9 PD10 | FSMC 标准映射 |
| BL（背光） | **PB15**，高电平点亮 | 实测确认，**不是 PB0** |
| RST | 接板级复位网络 | 无需软件控制 |

### 最小可用初始化序列（已验证可显示）

```
0xC0 <- 00 8B 8B      Power Control
0xC1 <- 00
0xC2 <- 17 02
0xC5 <- 00 40 80      VCOM
0x36 <- 00            MADCTL
0x3A <- 55            16 bit/pixel (RGB565)
0x11                  Sleep Out
  (需要约 120 ms 稳定；用全屏填充 320x480 充当延时)
0x2A/0x2B + 0x2C      设置窗口并写 GRAM
0x29                  Display On
```

### 五个坑（实测踩过）

1. **FSMC 时钟必须手动使能**：`HAL_SRAM_Init()` 把时钟交给空弱函数 `HAL_SRAM_MspInit()`，不调用 `__HAL_RCC_FSMC_CLK_ENABLE()` 时 BCR4 恒为 `0x00000000`，所有写入静默丢弃，但函数仍返回 `HAL_OK`。
2. **NT35310 必须先配电源**：只发 `0x11`/`0x29` 画面仍全白，加上 `0xC0/C1/C2/C5` 才出图像。
3. **`0x11` 后需约 120 ms 才能 `0x29`**：提前发 Display On 不生效。
4. **`0x01` 软件复会清屏**：调试重启时会导致"刚刚有画面、现在没了"。
5. **Cortex-M 跳转地址 bit0 必须为 1**：`-coreReg PC` 写偶数地址会进 `Default_Handler` 死循环。

## BOOT0 启动问题（2026-11-03，未解决）

- 现象：芯片复位后 `PC = 0x1FFF3xxx`（片内系统 Bootloader），不执行 `0x08000000` 的 Flash 固件。
- 已尝试：拔掉 `P21`（`BOOT` 丝印）跳线帽 —— 无效，仍进 Bootloader。
- 怀疑：正点原子"一键下载电路"（CH340 的 RTS 经三极管控制 BOOT0）把 BOOT0 拉高；也可能 `P21` 并非 BOOT0。
- **临时绕过**（可运行 Flash 固件）：
  ```
  STM32_Programmer_CLI -c port=SWD index=0 mode=HOTPLUG freq=1000 \
    -coreReg MSP=0x20020000 -coreReg PC=<Reset_Handler+1> -run
  ```
- **影响**：正常复位无法自动启动 Flash 固件，NOR 全片备份与后续升级实验都依赖这一点，必须先解决。

## 工具事实（实测）

- `-r32 <addr> <N>`：**N 是字节数**，不是 32 位字数。
- 每次 CLI 连接都会执行 Software reset（Hot Plug 模式）。
- `-u <addr> <size> <file>`：把目标内存导出为文件，适合读取结构体。
- `-coreReg <REG>=<value>`：写核心寄存器（注意是 `=` 形式）。

## unknown（待实物核对，不得猜测）

- MCU 完整丝印（屏幕遮挡）
- NOR 完整订货后缀（JV/FV/IQ/JQ，决定写驱动参数）
- EEPROM 完整型号与页长（24C02 的页长可能是 8/16/32）
- 供电入口与整板供电路径
- CAN 收发器型号、RX/TX 引脚跳线、终端电阻
- 是否有 USB-CAN 适配器（枚举未见）
- 板载日志串口（CH340 未在主机枚举，`has_ch340_uart=false`）

## 主机环境

- 电脑：DESKTOP-QG05G44，Windows 11 家庭版中文版
- ST-Link：`USB\VID_0483&PID_3748\I`，FW `V2J47S7`，唯一探针（index=0）
- 串口：仅 6 个蓝牙串口（COM4/5/6/7/10/11），无 USB 串口
- CubeProgrammer CLI：`D:\Keil\CubProgram\bin\STM32_Programmer_CLI.exe`（v2.23.0）

## 说明

- EEPROM 256 B 快照前 16 字节为 `00 01 02 … 0F`，仅记录为"非本工程元数据格式"，不据此推断原厂来源。
- NOR JEDEC `EF4018` 只确定到 W25Q128 家族，不据此开放擦写或选定写驱动。
- LCD 全红验证用的是独立目录 `stages/lcd-test/` 的探测固件，未改动 `ota-core/` 与 `board-lab/` 的构建输入。
