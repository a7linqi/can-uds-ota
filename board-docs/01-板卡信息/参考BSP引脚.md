# 探索者参考引脚 BSP 与只读上板入口

当前交付：v0.12。新增原始 C 板级代码、CAN 回环入口、只读诊断入口和可重新生成的 Keil 工程。未连接实物，不能把编译结果或模型输出记为上板通过。

## 版本与资料依据

用户最初判断“应该是 V2”；随后提供的照片外观更接近官方 V3 产品图。2026-10-31 补充卖家旗舰店购买凭证（书面证据）确定探索者 **V3 + STM32F407ZGT6（1 MiB）+ 3.5 寸屏** SKU，详见 [板卡接收记录](BOARD_RECEIPT.md)。屏幕遮住主芯片及部分板面，PCB 修订号、MCU 完整丝印及 NOR 型号仍需上电读芯片确认；实物批次保持待核验。

本次已取得并阅读多份厂商文档的公开镜像（含 V3 主原理图与 IO 引脚分配表），核对了相关图页：

| 文档 | 可复核来源 | 本次阅读范围 |
|---|---|---|
| `Explorer STM32F4_V2.2_SCH.pdf`，821450 字节 | [公开镜像](https://github.com/Meta-Team/Datasheets/blob/master/STM32F4/STM32F4%20%E6%8E%A2%E7%B4%A2%E8%80%85/Explorer%20STM32F4_V2.2_SCH.pdf)；文件名、体积与官方资料目录一致 | PDF 第 2、3 页：MCU、晶振、引脚、CAN/USB、EEPROM、NOR；内部图框修订号写 V2.0，不能仅凭外部文件名推定所有页为 V2.2 |
| `探索者V3 硬件参考手册_V1.0.pdf`，4231289 字节 | [公开镜像](https://github.com/Limitlloss/STM32F407_Explore_Files/blob/main/%E6%8E%A2%E7%B4%A2%E8%80%85V3%20%E7%A1%AC%E4%BB%B6%E5%8F%82%E8%80%83%E6%89%8B%E5%86%8C_V1.0.pdf)；文件名、体积与官方资料目录一致 | IO 表、硬件变更表、MCU、CAN、EEPROM、NOR、LED 与按键章节 |
| `EXPLORER_V3.4.pdf`，3189618 字节 | [公开镜像](https://github.com/Limitlloss/STM32F407_Explore_Files/blob/main/3%EF%BC%8C%E5%8E%9F%E7%90%86%E5%9B%BE/EXPLORER_V3.4.pdf)；文件名、体积与官方资料目录一致 | V3 主原理图，内部修订号 V3.4，已核对关键图页 |
| `探索者V3 IO引脚分配表.xlsx`，22058 字节 | [公开镜像](https://github.com/Limitlloss/STM32F407_Explore_Files/blob/main/3%EF%BC%8C%E5%8E%9F%E7%90%86%E5%9B%BE/%E6%8E%A2%E7%B4%A2%E8%80%85V3%20IO%E5%BC%95%E8%84%9A%E5%88%86%E9%85%8D%E8%A1%A8.xlsx)；文件名、体积与官方资料目录一致 | 全部 IO 分配，已逐项核对参考 BSP 引脚，见 `v3_pinout_check` |
| `ATK-MD0350 V2.4.pdf`，346681 字节 | [公开镜像](https://github.com/Limitlloss/STM32F407_Explore_Files/blob/main/3%EF%BC%8C%E5%8E%9F%E7%90%86%E5%9B%BE/ATK-MD0350%20V2.4.pdf)；文件名、体积与官方资料目录一致 | 3.5 寸 320×480 MCU 并口屏原理图，对应购买 SKU |

镜像不是厂商下载服务器，文件名和体积一致不能证明逐字节相同。已记录实际 SHA256 到 `evidence/official-resources.json`。本工程不打包厂商文档或整套例程；板级代码为本项目编写，ST HAL 使用本机安装包。

## 本 BSP 使用的参考连接

| 功能 | 参考引脚/配置 | 核对依据 |
|---|---|---|
| 外部高速晶振 | HSE 8 MHz | V2 MCU 图、V3 MCU 图 |
| 红色 LED / 绿色 LED | PF9 / PF10，低电平点亮 | V2 LED 图与 MCU 图；V3 IO 表、LED 章节 |
| 日志串口 | USART1，PA9 TX / PA10 RX；115200、8N1 | 两版 MCU/串口连接 |
| CAN | CAN1，PA11 RX / PA12 TX，AF9 | 两版 MCU 图及 CAN/USB 图 |
| EEPROM | I²C1，PB8 SCL / PB9 SDA，AF4；7 位地址 0x50 | 两版图中 A0/A1/A2 接地；不能把 8 位 0xA0 再当成 7 位地址 |
| SPI NOR | SPI1，PB3 SCK / PB4 MISO / PB5 MOSI，AF5；PB14 为低有效 CS | 两版 MCU、FLASH 图 |
| 共用 SPI 的无线接口 | PG7 为 CS，保持高；PG6 为 CE，保持低 | 无线接口与 MCU 图 |
| KEY0 | PE4，低有效，内部上拉 | 两版按键与 MCU 图 |

上表全部引脚已用 **探索者V3 IO 引脚分配表**（官方资料盘数据，经 GitHub 镜像）逐项核对一致，结论记录在 `evidence/official-resources.json` 的 `v3_pinout_check`。

旧版图（V2）中 MCU 标注为 ZET6；V3 手册与 V3 IO 表均明确为 **STM32F407ZGT6**。软件提供 ZG/ZE 两种容量目标，运行时读 Flash 容量并与目标核对，容量不匹配时在初始化阶段停止。Device ID 一致性检查不能证明完整型号或真伪。

V3 的 I²C1（PB8/PB9）总线不止接 24C02，还接 **ST480MC 触摸控制器**和 **ES8388 音频 Codec**。读 EEPROM 时需确认 7 位地址 0x50 不与这两者冲突，且不要在未初始化时误触这两个从设备。V3 的以太网 PHY 为 **YT8512C（RMII）**、音频为 **ES8388（I2S）**，均与 V2 的 LAN8720 / WM8978 不同。

V3 手册的变更表明确列出 NOR 可换为 NM25Q128/BY25Q128、CAN 收发器可换为 SIT1050。现有 W25Q128JV 驱动不能因为容量相同就直接获得兼容声明。这份 BSP 只发送 NOR `0x9F` 读 ID，不接入擦除、编程或状态寄存器写入。

## 接线与时钟

- CAN/USB 选择接口：旧版图是 **P11**，V3 手册是 **P5**。按对应原理图引脚编号，将 **1–3、2–4** 分别短接到 CAN 侧；不要凭照片朝向猜排针编号。PA11/PA12 此时不能同时接 USB 数据通信。
- 日志走板载 USB 转串口连接 USART1，不走 MCU 的 USB D+/D−。对应串口跳线也需要按实物核对。
- 图纸上的 CAN 接口已有 120 Ω 终端。与 USB-CAN 组成两端总线时，按两端各一个 120 Ω 配置；断电测量 CANH/CANL 约 60 Ω 可辅助核对。CANH、CANL、参考地按实际接口连接。
- 调试使用 SWD。PB3/PB4 在复位后兼有 JTAG/SWO 功能，本 BSP 将其配置为 SPI，保留 PA13/PA14 的 SWD。

软件配置：PLL M=8、N=336、P=2、Q=7，HCLK=168 MHz，PCLK1=42 MHz，PCLK2=84 MHz。CAN 分频 6，BS1=11 TQ，BS2=2 TQ，SJW=1 TQ：`42 MHz / (6 × (1 + 11 + 2)) = 500 kbit/s`，采样点约 85.7%。SPI 为 mode 0、8 bit、MSB first，84 MHz / 16 = 5.25 MHz；I²C 为 100 kHz。

这些是根据 8 MHz 晶振建立的配置值，尚未通过实物仪器测量时钟、位宽或总线错误率。

## 生成与选择 Keil 目标

本机已安装的 ST STM32F4xx_DFP 2.16.0 与 CMSIS 5.0.1 用于编译核对。其他电脑请用自身的实际路径重新生成，工程不会复制第三方 HAL 源码。

~~~powershell
python tools/generate_explorer_mdk.py --dfp "你的STM32F4xx_DFP/2.16.0目录" --cmsis "你的CMSIS/5.0.1/CMSIS/Include目录"
~~~

打开 `mdk/explorer_readonly.uvprojx`，按实际容量选择：

第一次下载测试固件前，先用调试器读取并保存原有内部 Flash。这里的“只读”指测试固件运行期间不擦写升级存储；下载测试固件本身会改写它占用的内部 Flash。

| 目标 | 入口与行为 |
|---|---|
| `explorer_ZG_loopback_READONLY` / `explorer_ZE_loopback_READONLY` | 静默内部回环。发 16 帧并通过真实接收队列逐帧比较，成功后输出结果和 LED 心跳；不驱动外部 CAN 总线 |
| `explorer_ZG_diagnostics_READONLY` / `explorer_ZE_diagnostics_READONLY` | 正常 CAN 模式，使用已有 Bootloader 诊断主循环；支持 PC 读取版本及更新状态，刷写不可用 |

ZG 对应 1 MiB，ZE 对应 512 KiB。两个入口均从 0x08000000 开始，链接区限制为 64 KiB；当前不是 App 固件，不应打包到 0x08010000 安装。

这四个目标只有一份 main、一份 ST startup、一份 ST SystemInit 和一套 HAL 中断回调；不链接 `board_unconfigured.c` 或 `system_minimal.c`。已有默认 Bootloader/App 工程保持未配置 BSP，不能把两个工程的板级文件混在一起。

本环境已用 64 位 GNU Arm GCC 完成四个只读目标的汇编、链接、ELF/BIN 与地址检查。Keil ARMASM/armlink 仍受 Windows DLL 重定位错误影响，因此完整 GNU 构建不等同于 Keil 工程已成功链接；需要 Keil AXF 时，在可用的 Keil 环境继续验证。两种构建方式均不代表实物已运行通过。

## GNU 完整固件与复现

已验证工具链为 xPack GNU Arm Embedded GCC 15.2.1，使用 ST pack 中的 GCC startup、同一份 C 源码、独立的 GNU 链接脚本及 Newlib 初始化接口。没有修改系统全局设置。

~~~powershell
python tools/build_explorer_gnu.py --gcc "你的arm-none-eabi-gcc路径" --dfp "你的STM32F4xx_DFP/2.16.0目录" --cmsis "你的CMSIS/5.0.1/CMSIS/Include目录"
~~~

| 只读目标 | BIN 长度（字节） | 初始 SP | Reset 向量 |
|---|---:|---|---|
| ZG 回环 | 12736 | 0x20020000 | 0x08002765 |
| ZG 诊断 | 22248 | 0x20020000 | 0x08004C51 |
| ZE 回环 | 12740 | 0x20020000 | 0x08002769 |
| ZE 诊断 | 22256 | 0x20020000 | 0x08004C59 |

这组数值对应本轮源代码与工具链。构建脚本检查 ELF32 ARM 格式、入口与 Reset 向量一致、Thumb 位、向量表起点、所有加载段、BIN 内容、64 KiB Flash 边界及主 SRAM 范围；链接脚本为栈保留 4 KiB。它没有测量真实运行时栈峰值。

工作目录输出在 `build/gnu-explorer/`。通过完整验证并使用 `tools/package_project.py --include-firmware` 打包后，交付包的 `firmware-builds/` 含各目标 ELF、BIN、MAP。打包器核对当前源代码哈希和固件哈希，拒绝混入旧构建结果。BIN 下载地址为 **0x08000000**；先确认芯片容量、保存原有内部 Flash，再选择对应回环目标。

本项目 C 源码按 `-Wall -Wextra -Werror` 检查。未修改的 ST HAL 在 GCC 下每目标有 4 条未使用形参告警（Flash 的 Banks、休眠函数的 Regulator），已保留在日志中；链接阶段启用了告警即失败。第三方声明随交付包提供。

## 上板后看什么

1. 初始化成功后，串口打印 MCU ID、Flash 容量、配置时钟、NOR JEDEC ID、EEPROM 读取状态及 CRC32。芯片 ID 无效、全 FF/全 00、读超时都保留为失败，不自动重置/解锁器件。
2. `ota_explorer_probe` 保存探测结果及 256 字节 EEPROM 读回。读回位于 RAM，尚未导出为备份文件；不要把一次读取等同于已完成持久备份。
3. 回环入口成功时，`ota_explorer_loopback_stage=3`、`ota_explorer_loopback_passed=16`；错误阶段 E1～E6 分别对应初始化、CAN 启动、发送、邮箱等待、接收等待或内容不符。内部回环成功不能证明收发器、跳线和外部总线正确。
4. 再运行只读诊断入口，接 USB-CAN，按 [PC 工具说明](PC_TOOL.md)显式指定真实接口和通道，执行 `info`、`status`。此阶段 `storage_verified=0` 是预期状态，即使读取到 NOR ID 和 EEPROM 也不会开放下载/安装。

三项更新门控全部为 0；在参考 BSP 下打开任意一项会编译失败。回环入口若被配为物理 CAN 模式也会编译失败，避免将测试帧发到外部总线。

后续工作：核对实际 PCB/MCU/NOR 标记 → 保存原有内部 Flash、NOR、EEPROM 备份 → 按实际器件补存储驱动与时限 → 验证刷写、安装恢复及断电行为。签名、实机吞吐和断电数据仍未完成。
