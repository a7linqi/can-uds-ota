# CAN/UDS 固件更新项目：跨工具交接摘要

交接文档更新时间：2026-11-01。工程基线：v0.12；本次只更新交接说明，没有修改实现或重新生成固件。

这是此前项目对话的交接摘要，**不是逐字聊天导出**。它保留项目目标、关键决策、已验证结果和待办。同级目录中的 `ota-core/` 是工程、测试证据及四套只读固件，可以离线阅读，无需打开 ChatGPT 或 TRAE 分享链接。后续具体任务以用户的新指令为准。

## 给下一套 harness 的接手要求（2026-11-01）

用户后续会用另一套 harness 推进工程，并希望自己能读懂、解释和修改代码。以下是接手建议，具体任务以用户最新指令为准。

### 先建立共同基线

- 在同一台电脑打开 `D:\Project\Can`，先读本文件，再按第 6 节阅读工程入口、当前状态、BSP 说明与验证记录。源码和可复现证据优先于旧聊天中的进度百分比；遇到冲突应指出并核对。
- 当前核心、主机验证和四套 GNU 只读固件构建已经完成；实板升级集成、实机验收及升级包签名尚未完成。不要从早期 Python demo 或 236 项检查版本重新开始。
- 修改前保留可回退基线；已有 `deliverables/ota-core-v0.12.zip`。若使用 Git，可在分支上推进；同一份工作目录同一时间只由一个 harness 修改，另一套用于只读审查。不要覆盖用户或其他工具尚未交接的改动。
- 不要用“软件 90%”替代交付清单。对每个模块分别说明：实现状态、模型验证状态、目标构建状态、实机验证状态。

### 保留已确定的设计

- C 核心、固定 App 执行地址 `0x08010000`、外部 NOR 新旧镜像、EEPROM 双副本元数据；不要未经讨论改回内部 A/B 双槽或整体重写。
- 保留“备份有效后才能擦除旧 App”“安装中断可重做”“仅运行中的 App 健康检查确认试运行”等不变量。任何变动都必须说明恢复路径并配相应故障验证。
- 原计划 L4 包含升级包签名；安全启动、完整 OTA、DoIP 和 FreeRTOS 迁移不自动加入当前交付范围。当前 MCU 实现使用协作式主循环。

### 下一步做一个可以验收的闭环

| 顺序 | 推进内容 | 可以提交的完成证据 |
|---|---|---|
| 1 | 核对实际板卡、芯片容量和接线；首次下载前备份原内部 Flash；运行只读回环固件 | 实际芯片/板卡记录、备份文件、串口日志与回环结果 |
| 2 | USB-CAN 读取版本和升级状态 | 实际接口配置、请求/响应记录 |
| 3 | 按实装 NOR/EEPROM 参数接通存储接口，集成 Bootloader 和可执行的 V1/V2 App | 正常下载、安装、App 启动及健康确认的真板记录 |
| 4 | 分阶段断电、未确认启动恢复、看门狗和时序验收 | 每次注入位置、恢复结果、失败分析、实测耗时 |
| 5 | 完成升级包签名校验与回归 | 有效包接受、篡改/错误签名/版本策略拒绝的验证；签名的软件工作可提前进行 |

当前 `explorer_reference.c` 的 NOR 读写接口仍返回失败，`storage_verified=0`；参考 BSP 在编译期阻止开启刷写、跳转、看门狗。要实现真实芯片绑定和相应验证，不能仅改宏或把这些检查删掉来宣称刷写完成。现有主机 demo 的合成 payload 不可作为可执行 App 烧录。

板卡文档证据与实物验证分开：现有购买凭证记录指向 V3 + ZGT6，实装 NOR、EEPROM 参数及具体 PCB 修订仍需核对。不能凭购买年份选择 BSP，也不能把 W25Q 驱动直接套到所有 128 Mbit NOR。硬件事实缺失时，继续源码学习或不依赖实装型号的软件工作，并明确缺少哪条实物信息。

### 每轮怎么改、怎么验收

- 一轮聚焦一个能验收的问题。先检查现有实现、解释改动原因，再做最小必要修改；不要为了增加代码量扩大功能。
- 改协议、存储或恢复逻辑时，针对边界和失败路径运行有意义的回归；必要时覆盖 ZG/ZE 两种容量。仅改文档不需要重跑全量 C 测试。不要重复相同且仍有效的检查来制造进度。
- 更新实现后，受影响的旧证据不自动适用于新源码。重新生成所需验证和构建证据，交付固件时核对其源码及产物哈希。
- 每轮交付说明五件事：改了哪些文件及原因；执行了哪些命令；实际结果；仍未验证的部分；下一步的具体入口。同步更新 `ota-core/docs/WORK_STATUS.md`，需要跨工具交接时更新本文件。
- 区分断言数、用例数、模型断电场景和实机断电次数。未执行的验证写“未执行”，不合并成一个夸大的总测试数；编译/链接成功不等于板上运行成功。

### 同时帮助用户掌握工程

继续使用 `ota-core/docs/CODE_LEARNING_PLAN.md`。每轮代码变更附一小段讲解：入口在哪里、谁调用、哪些状态改变、失败后如何恢复，以及对应哪个测试。允许用户先预测再对照源码；重点练习独立画出升级流程、追踪状态变化、修改一个小功能并验证。不要一次倾倒整个工程，也不要仅给用户面试话术。

第一轮接手的具体任务：读取上述基线，核对已有验证与当前源码是否匹配，指出最近一个可验收步骤并推进当前条件允许的工作。不要把等待板卡当成所有软件和学习工作的阻塞，也不要跳过未知硬件条件直接启用写入。

## 1. 项目要解决什么问题

用户学习和求职方向为嵌入式软件，希望通过一个以 C 和真实 MCU 为主体的项目，形成能独立解释、能复现、能在面试中讲清楚的工程经历。当前项目名称：

**基于 CAN/UDS 的车载诊断与可靠固件更新平台**。

核心目标是“升级中断后仍能恢复”以及“有可核对的验证证据”，包括下载异常、坏包拒绝、版本策略、安装中断、试运行确认与旧版本恢复。项目不是量产车载软件，也没有完成整条 OTA 链路。

最初做过 Python UDS 仿真（25 条 pytest），随后转为 C 核心，最早 L1 有 236 项检查。现在的实现已远超这个早期版本。Python 主要负责 PC 工具、自动化和台架；核心协议、升级状态机及 MCU 板级代码是 C。当前 MCU 入口为裸机主循环，尚未接入 FreeRTOS。

## 2. 已确定的架构

| 项目 | 当前设计 |
|---|---|
| MCU | STM32F407，分别支持 ZG 1 MiB 与 ZE 512 KiB 的内部 Flash 布局 |
| Bootloader | 内部 Flash 前 64 KiB，起点 0x08000000 |
| App | 固定执行地址 0x08010000，安装的内容为原始 App payload |
| 外部 SPI NOR | 下载镜像区 0～1 MiB，旧镜像备份区 1～2 MiB；仅分配前 2 MiB |
| 元数据 | 24C02 型 EEPROM 的 256 字节空间，64 字节记录放在偏移 0 和 128；模型按 8 字节页拆写 |
| 镜像格式 | IMG2，32 字节小端头，包含版本、长度、payload CRC32、执行地址及头 CRC32 |
| 通信 | 经典 CAN、ISO-TP、UDS；诊断请求 ID 0x7E0，响应 ID 0x7E8 |
| 两个恢复基础 | 安装可重做；元数据双副本、序号、CRC 和最后提交标记 |

关键过程：下载写入外部镜像 → 校验 → 先备份并验证旧 App → 持久化 INSTALLING → 擦写固定地址 App → TRIAL → App 自己确认健康。试运行最多三次，未确认则恢复旧镜像；恢复或安装中断后可以从头重做。

没有采用内部 Flash 双槽 A/B。此前考虑过该方案，但为了避免两个 App 链接地址/搬迁问题，已经定为外部新旧镜像加固定 App 地址。接手时不要把旧的内部双槽模型当成当前架构。

当前已有 CRC32 完整性与版本策略，并新增 **Ed25519 升级包签名校验（L4）**，详见 `ota-core/docs/SIGNATURE.md`。仍没有完整安全启动、没有硬件防篡改版本计数器。演示 Seed/Key 与演示签名 seed 都不代表生产密码学安全；不要把这些能力写成已完成。

## 3. 硬件信息与仍然不确定的部分

用户买了二手正点原子探索者 F407，卖家描述为 2023 年购买。用户最初推测 V2；随后到货记录中的旗舰店购买凭证指向 **V3 + STM32F407ZGT6 + 3.5 英寸屏**，详见 `ota-core/docs/BOARD_RECEIPT.md`。这属于文档证据；**实物 PCB 修订版、完整 MCU 丝印/实际 Flash 容量、NOR 型号与 EEPROM 参数仍待上板核对**。

购买年份不足以确定 V2/V3。已阅读厂商 V2 原理图和 V3 硬件手册的公开镜像，并核对关键图页；文件名、体积与官方目录一致，但尚未证明镜像与官方文件逐字节一致。来源和 SHA256 记录在 `ota-core/evidence/official-resources.json`。厂商 PDF 和整套例程没有随包分发。

参考文档中两版共用的主要连接已做成独立 BSP：

| 功能 | 参考连接 |
|---|---|
| HSE | 8 MHz；配置 HCLK 168 MHz、PCLK1 42 MHz、PCLK2 84 MHz |
| LED | PF9、PF10，低有效 |
| USART1 | PA9 TX、PA10 RX，115200、8N1 |
| CAN1 | PA11 RX、PA12 TX，AF9；500 kbit/s 配置值 |
| I²C1 | PB8 SCL、PB9 SDA，AF4；EEPROM 7 位地址 0x50 |
| SPI1 | PB3 SCK、PB4 MISO、PB5 MOSI，AF5；PB14 为 NOR CS |
| 共用 SPI 的无线接口 | PG7 CS 保持高，PG6 CE 保持低 |

CAN 位时序为分频 6、BS1=11 TQ、BS2=2 TQ、SJW=1 TQ，按 42 MHz PCLK1 算出 500 kbit/s。SPI mode 0，84 MHz/16=5.25 MHz；I²C 100 kHz。以上是配置，不是物理测量结果。

CAN/USB 选择跳线在旧版图中叫 P11，在 V3 手册中叫 P5，不能混用板面编号。具体连接和 SWD 注意事项见 `ota-core/docs/EXPLORER_REFERENCE.md`。

V3 手册明确允许 NM25Q128/BY25Q128 等 NOR 替换。现有可选写入驱动只声明支持 W25Q128JV-IQ/JQ，不能仅凭相同容量或 JEDEC ID 就认定其他型号兼容。

## 4. v0.12 具体交付了什么

- ISO-TP 分帧、流控、超时及异常处理；UDS 会话、读取与固件下载相关服务。
- 固定 App 地址、外部镜像/备份、事务元数据、安装重做、试运行确认和回滚决策。
- PC ISO-TP/UDS 客户端，仿真后端，以及需要显式指定接口/通道的可选 python-can 后端。
- 只读状态 DID 0x0200；提交应答丢失后可通过状态匹配再显式安装。
- 丢失 TransferData 应答时有限重发相同块；退出下载或复位应答不确定时停止自动重试，避免重复提交/消耗试运行次数。
- STM32 适配层、CAN 环形队列、接收中断只入队、主循环处理诊断、延迟复位和 App 跳转接口。
- 协作式 0x78 response-pending 和可选看门狗策略；其真实硬件时限仍待测量。
- 新增探索者参考引脚 BSP、只读硬件探测、静默内部回环入口和正常 CAN 只读诊断入口。
- 四套 ZG/ZE 只读目标的完整 GNU ELF、BIN、MAP。

参考 BSP 在启动时检查 MCU 家族 ID 和实际内部 Flash 容量，然后读取 NOR ID 与 256 字节 EEPROM。即使读取成功，`storage_verified` 仍为 0，不会开放下载/安装；EEPROM 数据仅存于 RAM，尚不等于已保存备份文件。

三个门控 `OTA_ENABLE_FLASHING`、`OTA_ENABLE_BOOT_HANDOFF`、`OTA_ENABLE_WATCHDOG` 在只读参考 BSP 下必须为 0，打开任意一个会编译失败。回环入口也禁止配成物理 CAN 发送模式。

## 5. 已有验证证据

下表口径不同，不应加在一起当成统一“测试用例数”，也不应写成实机测试数据。

| 项目 | 结果 |
|---|---|
| C 核心 | ZG/ZE 每种容量各 3050 项断言，0 失败 |
| 存储故障注入 | 每种容量各 420 个模型场景，不是物理断电次数 |
| PC 客户端 | 45 项 unittest，0 失败 |
| 实际 python-can 4.6.1 virtual 后端 | 5 项测试，连接实际 C ECU 子进程，未访问 USB-CAN |
| W25Q SPI 命令模型 | 720 项断言，含 31 个传输失败注入点，0 失败 |
| STM32 适配器模型 | 每种容量默认门控 177 项、启用门控 243 项断言，0 失败 |
| App 入口与虚拟看门狗 | 每种容量 35 项断言、8 个场景，未验证真实 IWDG |
| 新回环入口及实际 CAN 接收队列 | 51 项断言、14 个模型场景，0 失败；未运行物理控制器 |
| 新参考 BSP 的 ARMClang 编译 | 四个目标，每目标 35 个 C 对象，包括安装的 ST HAL |
| 镜像打包 | 14 项 Python 测试，另有 C 端接受/拒绝互操作检查 |
| Ed25519 签名校验（L4） | RFC 8032 测试向量、Python 签名→C 验证交叉、篡改/无签名拒绝；含 ed25519 的四目标 GNU 链接与 ARMClang 编译均通过 |
| GNU 完整只读目标 | 四个目标均完成编译、汇编和链接，检查了 ELF、向量表、SP、Reset、BIN 和地址范围 |

主报告：`ota-core/evidence/verification.json`。2026-11-01 交接核对时为 `status=passed`，99 个已记录源码文件哈希与工作区匹配；这是复核既有证据，没有重跑测试。其中 `target_linked=true` 指四个 GNU 只读目标，`keil_target_linked=false`，`hardware_verified=false`，`signature_implemented=false`。报告的具体耗时与运行命令以当前 JSON 为准。

本轮完整只读镜像：

| 目标 | BIN 字节数 | Flash 起点 |
|---|---:|---|
| explorer_ZG_loopback_READONLY | 12736 | 0x08000000 |
| explorer_ZG_diagnostics_READONLY | 22248 | 0x08000000 |
| explorer_ZE_loopback_READONLY | 12740 | 0x08000000 |
| explorer_ZE_diagnostics_READONLY | 22256 | 0x08000000 |

这些是独立的只读上板固件，链接区域为内部前 64 KiB。它们不是 0x08010000 的 App 升级 payload。完整产物在 `ota-core/firmware-builds/`；正常工作目录的构建产物在 `build/gnu-explorer/`。

## 6. 接手时按这个顺序阅读

1. `ota-core/README.md`：项目总入口。
2. `ota-core/docs/WORK_STATUS.md`：最新工程检查点，含历史上下文。
3. `ota-core/docs/EXPLORER_REFERENCE.md`：当前只读 BSP、跳线、构建方式与限制。
4. `ota-core/evidence/verification.json`：真实验证结果及源代码哈希。
5. `ota-core/docs/STORAGE_MODEL.md`：持久化和恢复模型。
6. `ota-core/docs/BOARD_RECEIPT.md`、`docs/HARDWARE_ACCEPTANCE.md`：实物核对和验收记录。

关键代码位置：

- `src/core/`：ISO-TP、UDS、镜像、元数据、Bootloader、看门狗策略。
- `src/driver/spi_nor.c`：仅声明支持 W25Q128JV-IQ/JQ 的可选驱动。
- `platform/stm32f407/src/`：平台适配器。
- `platform/stm32f407/board/explorer_reference/`：共用参考引脚 BSP 和中断。
- `firmware/explorer_loopback/main.c`：静默内部回环入口。
- `firmware/bootloader/main.c`：只读诊断目标复用的入口；生产升级目标仍需实际存储绑定。
- `firmware/app/main.c`：App 试运行健康确认入口。
- `tools/uds_tool.py`：PC 命令行工具。
- `tools/generate_explorer_mdk.py`、`tools/build_explorer_gnu.py`：参考工程生成和 GNU 完整构建。

## 7. 本地环境与离线范围

原电脑工程目录：`D:\Project\Can\ota-core`。同一电脑上的其他工具可直接打开该目录，无需搬代码。跨电脑则解压本交接包。

原电脑可用工具：

- Python 3.14。
- 主机 C 验证使用 TinyCC 的 `-run` 原生内存执行方式，路径为 `D:\Project\Can\toolchain\tccbox-2025.10.27.data\purelib\tccbox\tcc_dist\tcc.exe`。
- ARMClang：`D:\Keil\Keil5\ARM\ARMCLANG\bin`。
- ST pack：`D:\Keil\Keil5\ARM\PACK\Keil\STM32F4xx_DFP\2.16.0`。
- CMSIS Include：`D:\Keil\Keil5\ARM\PACK\ARM\CMSIS\5.0.1\CMSIS\Include`。
- GNU Arm GCC：`D:\Project\Can\toolchain\xpack-arm-none-eabi-gcc-15.2.1-1.1\bin\arm-none-eabi-gcc.exe`。
- 可选 python-can 依赖隔离放在原工作目录 `build/pcdeps`，没有全局安装。

原环境的 Keil ARMASM/armlink 因 Windows DLL 重定位错误无法启动；64 位 GNU Arm GCC 已可用于完整链接。不要把 GNU 成功解释成 Keil 的 AXF 已成功生成。

**本包可以离线阅读、审查源码、查看已有结果和携带固件。重新构建需要目标电脑已有相应编译器、ST/CMSIS 包和 Python 依赖，这些工具及完整厂商包不随交接包分发。** 复制到不同路径后，需要重新生成 Keil 工程的外部 pack 路径。

生成参考 Keil 工程：

```powershell
python tools/generate_explorer_mdk.py --dfp "实际ST-pack目录" --cmsis "实际CMSIS-Include目录"
```

执行验证的参数入口：

```powershell
python verify_project.py --cc "主机C编译器路径" --arm-bin "ARMClang-bin目录" --dfp "ST-pack目录" --cmsis "CMSIS-Include目录" --gcc "arm-none-eabi-gcc路径"
```

真实 python-can virtual 后端检查需另加 `--python-can-virtual`，隔离依赖时再加 `--python-can-path`。运行这些验证不需要也不应自动打开真实 CAN 接口。

重新交付带固件的源码包：

```powershell
python tools/package_project.py --include-firmware --output "工程目录外的输出zip路径"
```

源码改变后需更新验证证据，旧报告不能证明新代码正确。打包器会检查源码和固件哈希。

## 8. 下一步与已知边界

当前没有真机运行、物理 CAN、真实擦写、真实 IWDG、物理断电、刷写耗时或总线负载数据。升级包签名校验（Ed25519，L4）已实现并通过主机验证；完整 OTA 仍未实现。

接下来的实物步骤：

1. 核对 PCB、MCU 完整型号和实际 Flash 容量，记录实际 NOR/EEPROM 型号。
2. 首次下载测试固件前，先备份原有内部 Flash。运行时“只读”不意味着下载测试固件不会改写其占用的内部 Flash。
3. 按容量选择 ZG/ZE 静默内部回环固件，观察串口、LED 和 16 帧比较结果。
4. 对照实际版本核对 CAN 跳线、终端及接线，运行只读诊断固件，用 USB-CAN 读取 UDS 版本和状态。
5. 保存原有 NOR/EEPROM 数据，依据实际芯片数据手册实现并测量存储驱动，再验证完整升级与恢复。
6. 之后才回填实测简历数字，以及推进签名和其他扩展。

用户希望持续推进、少重复确认，重视 C 实现和真实硬件。软件可先审查、构建和验证；涉及当前未知的物理器件参数时，应明确未知项，不能用猜测把状态写成已验证。

## 9. 包的来源

工程来自已交付的 `ota-core-v0.12.zip`：2126029 字节，SHA256：

`b65d64e289ffe12aa52e2fc0551a156bb77e35022e7b0ec13b7026b922767fba`

本交接包在原工程文件之外增加本文件，没有修改原工程源代码或已有验证记录。上述 SHA256 属于原工程 ZIP，不是外层交接 ZIP。


## 当前学习入口（2026-10-31）

用户当前希望通过阅读源码理解工程。已按实际 v0.12 代码整理 [源码学习计划](ota-core/docs/CODE_LEARNING_PLAN.md)，含 14 个主线单元、强化专题和第一课的阅读入口与自测。后续具体任务以用户新指令为准。
