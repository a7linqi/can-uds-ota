# 正点原子探索者 STM32F407 板卡资料包

> 板卡：**正点原子 ALIENTEK 探索者 STM32F407**（底板 `ATK-DNF407 V3.3`）
> 整理时间：2026-11-03
> 所有"已确认"项均来自 SWD 实读、设备枚举或实物照片；未确认项明确标注，不猜测。

---

## 一、板卡关键参数（实测确认）

| 项目 | 参数 | 证据来源 |
|---|---|---|
| 板卡型号 | 正点原子 ALIENTEK 探索者 STM32F407 | 正面丝印 + 购买记录 |
| PCB 版本 | **ATK-DNF407 V3.3** | 背面丝印照片 |
| 主控 MCU | **STM32F407ZGT6**，Cortex-M4F，168 MHz | 容量 + 系列推断，丝印被屏幕遮挡 |
| 内部 Flash | **1 MiB**（`0x1FFF7A22` = `0x0400`） | SWD 实读 |
| 内部 RAM | 192 KiB（128 KiB SRAM + 64 KiB CCM） | 芯片规格 |
| 芯片 ID | `0x413`（DBGMCU_IDCODE = `0x10076413`） | SWD 实读 |
| 芯片修订 | Rev 2.0（REV_ID `0x1007`） | SWD 实读 |
| 主晶振 | HSE 8 MHz，PLL → 168 MHz | 配置 + 实测时钟 |
| 外部 NOR | **W25Q128 家族，16 MB**，SPI1 | JEDEC `EF 40 18` 实读 |
| EEPROM | **24C02，256 B**，I2C1 地址 `0x50` | 实读应答 + 快照 |
| 屏幕 | **ATK-MD0350 V2.4，3.5" 320×480**，16 位 8080 并口 | 屏幕标签 + 官方原理图 |
| 屏幕驱动 IC | **NT35310** | 读 `0xD4` → `0x5310` |
| 屏幕触摸 | 电阻式（XP / Y- / X- / Y+） | 原理图，未实测 |
| 以太网 PHY | YT8512C（RMII，V3 版） | V3 硬件手册 |
| 音频 Codec | ES8388（I2S，V3 版） | V3 硬件手册 |
| 调试接口 | SWD（PA13 = SWDIO，PA14 = SWCLK） | 实测连通 |

---

## 二、目录导航

```
board-docs/
├── README.md                        ← 本文件（总索引）
├── 01-板卡信息/
│   ├── BOARD_FACTS.md               ← 实测事实（最权威，含踩坑记录）
│   ├── BOARD_RECEIPT.md             ← 板卡接收记录（采购与验收过程）
│   ├── 参考BSP引脚.md                ← 参考 BSP 引脚核对（含 CAN/SPI/I2C/UART）
│   ├── PINOUT.md                    ← 引脚总表（MCU 侧 + 屏幕接口）
│   └── 资料下载指引.md               ← 官网 / 网盘 / 淘宝 / 论坛链接
├── 02-原理图/
│   └── ATK-MD0350_屏幕原理图_V2.4.pdf  ← 3.5 寸屏原理图（已验证 346681 B）
├── 03-LCD驱动/
│   ├── README.md                    ← 驱动说明与最小可用初始化序列
│   ├── main.c                       ← 探测 + 填充固件源码
│   ├── build.py                     ← 独立构建脚本
│   ├── link.ld                      ← 链接脚本（Boot 区 64 KiB）
│   └── stm32f4xx_hal_conf.h         ← 最小 HAL 配置（含 SRAM/FSMC 模块）
├── 04-验证记录/
│   ├── LCD验证报告.md                ← 完整验证过程与结论
│   └── lcd-probe-struct.bin         ← 探针结果原始 32 字节
└── 05-工具与命令/
    └── 常用命令.md                   ← STM32_Programmer_CLI 实战命令集
```

---

## 三、快速开始

### 烧录固件

```powershell
$cli = "D:\Keil\CubProgram\bin\STM32_Programmer_CLI.exe"
& $cli -c port=SWD index=0 mode=HOTPLUG freq=1000 `
       -d "固件.bin" 0x08000000 -v -rst
```

### 编译 LCD 驱动（示例）

```powershell
cd D:\Project\Can\stages\lcd-test
python build.py `
  --gcc "D:\Project\Can\toolchain\xpack-arm-none-eabi-gcc-15.2.1-1.1\bin\arm-none-eabi-gcc.exe" `
  --dfp "D:\Keil\Keil5\ARM\PACK\Keil\STM32F4xx_DFP\2.16.0"
```

### 读内存 / 读寄存器

```powershell
# 读内存（注意：第二个参数是字节数）
& $cli -c port=SWD index=0 mode=HOTPLUG freq=1000 -r32 0x20000058 32

# 读 PC
& $cli -c port=SWD index=0 mode=HOTPLUG freq=1000 -coreReg PC

# 导出内存到文件
& $cli -c port=SWD index=0 mode=HOTPLUG freq=1000 -u 0x20000058 0x20 out.bin
```

---

## 四、已知问题

### 1. BOOT0 启动异常（2026-11-03 记录）

- **现象**：复位后 `PC = 0x1FFF3xxx`（片内系统 Bootloader），不执行 Flash 固件。
- **触发**：BOOT0 引脚被拉高（一键下载电路或跳线）。
- **处理**：拔掉 `P21`（丝印 `BOOT`）跳线帽后，经一次复位可恢复（实测有效）。
- **应急绕过**：用调试器手动设 SP + PC 强跳（注意 PC 必须带 Thumb 位，即 `+1`）：
  ```powershell
  & $cli -c port=SWD index=0 mode=HOTPLUG freq=1000 `
         -coreReg MSP=0x20020000 -coreReg PC=0x080026A5 -run
  ```

### 2. 板载串口（CH340）未识别

- 主机枚举只有 6 个蓝牙串口，无 `USB-SERIAL CH340`。
- **影响**：看不到串口打印，只能靠 SWD 读 RAM 获取运行时信息。
- **处理**：需在 `USB_232` 口插线并安装 CH340 驱动。

### 3. 未确认项（不得猜测）

| 项目 | 状态 |
|---|---|
| MCU 完整丝印 | 被屏幕遮挡 |
| NOR 订货后缀（JV/FV/IQ/JQ） | 只确认到 W25Q128 家族 |
| EEPROM 页长（8/16/32 字节） | 只确认型号 24C02 |
| CAN 收发器型号 | V3 可能为 SIT1050，未实测 |
| CAN 终端电阻配置 | 未实测 |
| USB-CAN 适配器 | 主机枚举未见 |

---

## 五、资料获取（网络恢复后自行下载）

官方资料页（**V3 版本**）：
```
https://www.openedv.com/docs/boards/stm32/zdyz_stm32f407_explorerV3.html
```

百度网盘 A 盘（原理图/手册/源码）：提取码 `rqn5`

详见 `01-板卡信息/资料下载指引.md`。

---

## 六、这份资料的使用建议

1. **先看 `01-板卡信息/BOARD_FACTS.md`** —— 这是唯一经过实机验证的事实清单，其他文档都是它的展开。
2. **要点亮屏幕** → 直接看 `03-LCD驱动/README.md` 里的最小初始化序列和五个坑。
3. **要写自己的驱动** → 参考 `05-工具与命令/常用命令.md` 的调试手法。
4. **遇到"函数返回 OK 但硬件没反应"** → 先查时钟使能（`__HAL_RCC_xxx_CLK_ENABLE()`），这是 HAL 库最常见的坑。
