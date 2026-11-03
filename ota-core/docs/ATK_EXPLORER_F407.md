# 正点原子探索者 F407：官方资料与移植顺序

核对日期：2026-10-31。适用项目：基于 CAN/UDS 的车载诊断与可靠固件更新平台。

2023 年购买不能直接确定板卡版本；但卖家旗舰店购买凭证（书面证据）已确定探索者 V3 + STM32F407ZGT6（1 MiB），见 [板卡接收记录](BOARD_RECEIPT.md)。官方 V3 资料帖发表于 **2022-11-30**，旧版资料也仍有独立入口。实物 PCB 版本、主芯片完整丝印和 NOR 型号仍需到板后核验，再选对应原理图和例程。

## 官方资料入口

| 板卡 | 官方资料页 | 官方资料盘 | 提取码 |
|---|---|---|---|
| 探索者 STM32F407 V2，当前列为停产产品 | [V2 资料页](https://www.openedv.com/docs/old-products/Old%20information/zdyz_stm32f407_explorer.html) | [V2 A 盘](https://pan.baidu.com/s/18FAortpn_sdvZocav-JFpQ) | hhot |
| 探索者 STM32F407 V3 | [V3 资料页](https://www.openedv.com/docs/boards/stm32/zdyz_stm32f407_explorerV3.html) | [V3 A 盘](https://pan.baidu.com/s/1C5Yx-lwa_Dmn8UwgSz8YTw) | rqn5 |

入口来源为[正点原子官方资料中心](https://www.openedv.com/docs/index.html)。另有 [V2 官方资料帖](https://www.openedv.com/thread-308949-1-1.html)和 [V3 官方资料帖](https://www.openedv.com/thread-340198-1-1.html)。V3 帖子的时间只能证明当时已有 V3 资料，不能证明某一块二手板的版本。

已读取官方资料页、资料帖及其资料盘文件目录。官方资料盘下载接口返回 `errno=-20`。后续已从公开镜像读到同名同体积的 V2 原理图和 V3 硬件手册，核对相关图页并完成共用参考引脚 BSP，见 [实际移植入口与来源说明](EXPLORER_REFERENCE.md)。目录文件名本身不构成引脚依据；镜像与官方文件的逐字节一致性尚未证明，实物修订版和 NOR 型号也仍待核对。默认 Bootloader/App BSP 继续保持未配置状态。

## 优先取哪些文件

| 版本 | 目录中已确认的文件或目录 | 在本工程中的用途 |
|---|---|---|
| V2 | `3，ALIENTEK探索者STM32F4开发板原理图/Explorer STM32F4_V2.2_SCH.pdf` | 核对 PCB 修订版、CAN 收发器、跳线、晶振和存储连接 |
| V2 | 同目录的 `探索者IO引脚分配表.xlsx` | 与原理图逐项核对 CAN、SPI、I²C、LED、串口和按键 |
| V2 | `STM32F4开发指南-HAL库版本_V1.2.pdf`、`4，程序源码` | 提取本板的时钟初始化及外设例程；先查看源码目录内部版本 |
| V3 | `3，原理图.zip` | 解压后按实物修订版选择原理图；压缩包内部文件本次未读取 |
| V3 | `探索者V3 硬件参考手册_V1.0.pdf` | 核对接口、跳线、板载器件及资源冲突 |
| V3 | `STM32F407 探索者开发指南V1.3.pdf` | 查看硬件说明和外设实验 |
| V3 | `4，程序源码/2，标准例程-HAL库版本.zip` | 作为板级初始化参考，选择 LED、串口、CAN、EEPROM、SPI Flash 实验 |

只需先取对应版本的原理图、硬件手册和 HAL 基础例程。教程编号可能随资料版本变化，按实验名称寻找。厂商资料保留原有许可，本工程包只记录入口和所需文件名。

## 与现有工程怎么接

继续使用固定 App 地址 + 外部新旧镜像 + EEPROM 事务元数据的方案。当前软件核心和台架已经完成，下一里程碑是**板上只读诊断打通**，验收目标是 LED 心跳、串口日志、CAN 回环及 PC 读取版本。

| 阶段 | 实际工作 | 验收证据 |
|---|---|---|
| 识别实物 | 记录板名/PCB 版本、MCU 完整丝印、Flash 容量、NOR 与 EEPROM 型号 | 填写 [板卡接收记录](BOARD_RECEIPT.md)，附清晰照片或文字 |
| 最小启动 | 用对应官方 LED/串口例程确认供电、时钟、下载器连接 | 可观察的心跳和串口启动记录 |
| CAN 回环 | 先验证控制器内部收发，再验证中断进入和软件队列 | 实际发送、接收和错误计数；内部回环不能证明外部接线正确 |
| 物理 CAN | 根据实际时钟计算 500 kbit/s 位时序，接 USB-CAN 验证收发 | 收发帧、位时序、终端配置及错误计数 |
| 只读存储 | 读 NOR JEDEC ID、EEPROM 内容，先导出原有数据 | 器件丝印、读回文件和校验值；此阶段不运行厂商擦写测试 |
| 只读 UDS | 接入现有 ISO-TP/UDS，读取版本及 DID 0x0200 | PC 请求/响应日志；存储未就绪时保留其真实状态 |
| 存储写入与恢复 | 核对器件命令、容量、分区、擦写时间后开放刷写，再验证安装和断电恢复 | 按 [硬件验收顺序](HARDWARE_ACCEPTANCE.md)填写实测数据 |

移植连接点：

- `platform/stm32f407/examples/board_hal_user.h`：实际板卡需要提供的 HAL 句柄、CS、日志、按键、健康检查等接口。
- `platform/stm32f407/examples/board_hal_binding.c`：已有 CAN、内部 Flash、EEPROM 与 IWDG 的 HAL 参考绑定。
- `platform/stm32f407/examples/w25q128_hal_user.c`：可选 NOR 绑定。现有芯片驱动只声明 W25Q128JV-IQ/JQ 支持；不能仅凭相同 JEDEC ID 就认定老板上的其他型号兼容。
- `platform/stm32f407/board/board_unconfigured.c`：当前 Keil 默认 BSP。实际绑定完成后替换，不能同时链接两套 BSP。
- `firmware/bootloader/main.c`：先初始化诊断对象再启用 CAN 接收；中断回调只入队，主循环处理 UDS。

本阶段不必先引入 FreeRTOS。先把同一份 C 核心在板上跑通，随后再按需要划分诊断、存储和应用任务。以太网/DoIP 可在 CAN 刷写与恢复完成后扩展。

## 需要实物决定的项目

MCU 完整型号不能从“F407 系列”推定。ZG 与 ZE 的内部 Flash 分别为 1 MiB 和 512 KiB，工程已有两套布局。读出的 Device ID 与 Flash 容量用于一致性检查，不能单独作为真伪或完整型号证明。

参考文档已核对 CAN、LED、晶振、SPI 与 EEPROM 的共用连接，并写入单独的参考 BSP。实物跳线、PCB 修订版、完整器件型号、EEPROM 页大小和实际擦写时限仍需要到板后核对；读取 ID 不会自动开放升级存储。

第一次开放擦写前，备份板上原有 NOR 与 EEPROM 数据：厂商出厂程序可能使用它们保存字库、资源或设置。随后再划定本项目使用的外部前 2 MiB 和 EEPROM 元数据区域。

已有测试结果仍属于主机仿真、适配器模型和 ARM C 对象编译。尚无真机刷写耗时、物理断电次数或完整目标链接结果；这些数字在完成对应步骤后回填。
