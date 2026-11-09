# 真实 CAN 台架排障实录：现象、根因与解决方式（2026-11-07；2026-11-09 补第 7 节）

> 2026-11-09 补记：首次执行真实 CAN 完整 OTA 时链路未建立，A/B 对照排除固件（第 7 节）；**当晚打开板子电源开关后链路恢复，完整 OTA 已实机通过**（第 8 节）。证据：`stages/board-lab/evidence/can/bringup_20261109_ab.json`、`real_can_ota_summary.json`。

> 配套阅读：`CAN台架-接线与验证.md` 第 8 节（验收记录）、`stages/board-lab/REAL_CAN_OTA_ASSESSMENT.md`（后续完整 OTA 评估）、`journal/entries/2026-11-07-*.md`（六条逐轮记录）。
> 本文把这一轮遇到的**每一个坑、当时为什么判断错、最后怎么解决**集中到一处，便于复现与面试复述。

## 0. 结论：通了，范围要写准

**通了。** 范围是 **真实 CAN 物理层 + ISO-TP 多帧 + UDS `0x22` 只读诊断**（含 Seed/Key 解锁与门控遥测）。
**没有通**的是真实 CAN 上的完整 OTA（`0x34/0x36/0x37/0x31/0x11` → 安装），那仍只在 UART 台架实机验证过。→ **2026-11-09 22:44 该项已补上**，见第 8 节。

**最重要的一句话**：硬件、驱动、固件其实一直具备通信条件；让链路「看起来不通」的是**自写探针把 `PCAN_BAUD_500K` 写成 `0x001C0000`**（正确值 `0x1C`），把适配器设成了无效波特率，于是它自己 bus-off，制造了整轮假的 `BUSOFF 0x10`。改成 `B.PCAN_BAUD_500K.value` 后，同一脚本立刻 `CAN_GetStatus = 0x0` 并收到 `0x7E8` 响应。

## 1. 怎么通的：链路成立的四个条件（全部实测）

| 条件 | 实测值 | 判据 |
|---|---|---|
| ① 物理层 | CANH/CANL/GND 共地；P5 两个跳帽竖直短接 `CAN_RX↔PA11`、`CAN_TX↔PA12`；板子 `ATK MODULE=5 V`、`OLED/CAMERA=3.3 V`；CANH/CANL ≈2.3–2.4 V 隐性 | 官方《探索者 CAN 通讯实验》要求这样接；整板照片确认 |
| ② 适配器 | UTC-T01 / `PCAN-USB`（VID `0C72:000C`），PEAK 驱动 4.4.0.16619 + `PCANBasic.dll` 4.6.2.753，`PCAN_USBBUS1` **500 kbit/s** | `ProblemCode=0`、`CAN_Initialize=0x0`、`CAN_GetValue(HARDWARE_NAME)="PCAN-USB"` |
| ③ 板子固件 | `explorer_ZG_diagnostics_READONLY.bin`；横幅 `CAN: normal physical 500 kbit/s`、`loader diagnostics: request 7E0, response 7E8`；hclk 168 MHz / pclk1 42 MHz → 42 MHz÷6÷14 = 500 kbit/s | 511 B 开机横幅；运行态 `BTR=0x001A0005`（BRP=5/TS1=10/TS2=1，SILM=0、LBKM=0） |
| ④ 总线上有第二个节点在 ACK | 板子 CAN1 `CAN_MODE_NORMAL` 且 `HAL_CAN_Start` 成功 → 收到正确帧自动在 ACK 槽拉显性 | 探针阶段适配器 8 s 收到板子主动发出的 18 帧 `0x7E8` |

**关键机制**：CAN 是多节点总线，任何节点收到 CRC 正确的帧都会由硬件自动 ACK，与固件是否回 UDS 无关。适配器报 bus-off = 它发的帧**没有任何节点应答**。`0x7E0`/`0x7E8` 只是请求/响应 ID，不是「链路」本身。

## 2. 逐帧证据：怎么确认它真的通了

```
TX 0x7E0 03 22 F1 90 00 00 00 00       UDS ReadDataByIdentifier VIN
RX 0x7E8 10 14 62 F1 90 4C 4F 54     ISO-TP 首帧，总长 20 B
TX 0x7E0 30 00 00 00 00 00 00 00       流控 BS=0 / STmin=0
RX 0x7E8 21 41 30 30 30 30 30 30     连续帧 1
RX 0x7E8 22 30 30 30 30 30 30 31     连续帧 2
```

拼出 20 B：`62 F1 90` + 17 位 VIN `LOTA0000000000001`。四轮只读验收 `status=passed`。
补发流控前，收不到连续帧、后续请求也会被 ISO-TP 状态机忽略——**这是协议层行为，不是物理层丢包**（推断，未单独定量）。

证据：`stages/board-lab/evidence/can/uds_readonly_acceptance.json`、`uds_multiframe_evidence.json`、`boot_banner_20261107.txt`、`nrc_72_gate_evidence.json`。

## 3. 问题与解决方式总表

### A. 判据与工具坑（最致命，误导了整轮排查）

| # | 现象 | 当时的误判 | 根因 | 解决方式 |
|---|---|---|---|---|
| A1 | 发帧后稳定 `CAN_Read/CAN_GetStatus = 0x10` | 「物理层没有节点，接线/供电/跳线有问题」 | `can_errframe_probe.py` 里 `PCAN_BAUD_500K = 0x001C0000`；PCANBasic 实际是 `0x1C`（`c_ushort`），传进 `CAN_Initialize` 被截成 `0x0000`（无效波特率），适配器与 500k 板子不同速 | 改为 `B.PCAN_BAUD_500K.value`；修正后 `CAN_GetStatus=0x0` 且收到 `0x7E8` |
| A2 | 全 11 个速率扫出 `CAN_GetStatus = 0x0000`（看着像「BUSOFF 消失」） | 差点写成「物理层改善」 | 每个速率只发 1 帧、等 0.6 s、没开 `PCAN_ALLOW_ERROR_FRAMES`，TEC 来不及累积 | 该脚本标为不可单独作判据；正式判据统一用 A1 脚本的方式（发帧后、开错误帧、从底层读） |
| A3 | `bus.state=ACTIVE`、PcanView `Status: OK` | 当成「有 ACK」 | `bus.state` 是缓存属性；`Status OK` 只描述本机控制器初始化成功；`CAN_Initialize` 会清错误标志；python-can 默认开 `PCAN_BUSOFF_AUTORESET` | 只看「发帧之后」从 `CAN_Read` / `CAN_GetStatus` 读到的状态 |
| A4 | SWD `mode=normal` 读出 CAN1 全是复位默认值、`mode=hotplug` 反复 `Unable to get core ID` | 当成「运行态 CAN1 没初始化」 | `mode=normal` 会软件复位并 halt 内核，读到的是复位瞬间快照 | 用临时探针固件让**运行中的固件**把 `AFRH`/`BTR` 放进 CAN 帧发出来，才是运行态证据（见 D2） |
| A5 | 读 `0x40002000` 全是 0 | 解释为「复位值」 | 地址错了：`0x40002000` 是 TIM14，bxCAN1 在 **`0x40006400`** | 纠正基址；复位默认值是 `MCR=0x00010002`、`BTR=0x01230000` |
| A6 | `can_readonly.py` 跑的是过时客户端 | 未察觉 | `CORE_TOOLS` 指向旧快照 `D:\Project\Can\ota-core\tools`（缺 `i2c_bus_clear()`） | 改指活库 `D:\Project\Can-learning\ota-core\tools` |

**教训（写进 journal）**：判据本身必须先被校验。一个比 UI 更「底层」的自写探针最不容易被怀疑，一旦常量写错，整条排查链会朝相反方向跑好几天。

### B. 上位机与驱动

| # | 现象 | 根因 | 解决方式 |
|---|---|---|---|
| B1 | 装驱动前 TXD/RXD 灯闪，装完反而不亮 | 灯只在有 CAN 帧活动时闪；上位机未连接或总线无帧时全灭是正常表现；设备还曾瞬时 `Present=False` | 用 PnP 状态 + `CAN_Initialize` 判断，不用灯判断 |
| B2 | 设备管理器里找不到 COM 口，以为没装好 | `PCAN-USB` 是 **CAN-Hardware 类**设备，不是串口；CH340（`USB_232`）才是 COM | 按 Class 过滤，别和串口混为一谈 |
| B3 | 开着 PcanView 时我的脚本 `CAN_Initialize = 0x1400`（ILLHW） | 该兼容适配器单客户端，PcanView 占着 `PCAN_USBBUS1` | 跑脚本前关闭 PcanView |
| B4 | PcanView 默认停在 **200 kbit/s**（BTR0=C1/BTR1=3E），板子是 500 kbit/s | 上位机遗留配置，与目标固件不同 | 连接对话框把 `Bit Rate` 改 **500**（BTR0=00/BTR1=1C） |
| B5 | 视频里「发一条、回一条」，我们只有一个适配器却没回包 | 手册第 12 页原文「**两个板子互相通信**的发送接收的数据」：演示用两块 USB-CAN（`182h` ↔ `123h`）；PcanView 只显示总线上流过的帧，不自己回复 | 我们这边的「第二个节点」是 STM32 板子，它必须真的在总线上 ACK |
| B6 | 怀疑驱动没装好 | 实测：`Status=OK` / `ProblemCode=0`、WHQL 签名 `pcan_usb.inf` 4.4.0.16619、`PCAN_USB.sys` 同版本、`PCANBasic.dll` 4.6.2.753（System32+SysWOW64）、`CAN_Initialize=0x0` | 驱动完全排除；4.4.0 与 4.6.2 是 PEAK 两条独立版本线，初始化成功即证明兼容 |

### C. 电气测量误读

| # | 现象 | 根因 | 解决方式 |
|---|---|---|---|
| C1 | CANH–CANL 回路「电流都是 0」，以为没接好 | 隐性时 CANH≈CANL≈2.5 V，差分≈0，120 Ω 上无压降，**电流本来就近 0**；只有显性位有约 30 mA 瞬态，万用表抓不到 | 用电压档判断，别用回路电流判断；电流档也别并联到电源 |
| C2 | 量到 CANH/CANL ≈2.3–2.4 V | 曾反推「板子收发器有 5 V」——**不成立**，该电平可能完全来自适配器侧；板子收发器即使没电呈高阻，读数一样 | 静态电平是所有节点共同作用的结果，不能反向归因到某一个节点 |
| C3 | CANH–CANL 实测 160 Ω，理想是 60 Ω | 用户确认拨码已在 `120R`；适配器原理图终端网络含 `R26 120R`、`R4 120R/NC`（预留位）、BL1551B 模拟开关（拨码 `CTR` 控制）、CANH/CANL 上多颗 `10R` 串阻，叠加带电测量/接触电阻 | **链路在 160 Ω 下稳定通过验收，偏差非阻塞项**，短线 500 kbit/s 不必强求 60 Ω（成因是推断，未逐项定量） |
| C4 | 电阻读数不可信 | 带电测电阻会被收发器有源电路干扰 | 双方完全断电 → 表笔短接归零 → 电阻档直接压在 CANH/CANL 端子金属上 |
| C5 | 曾怀疑板子缺 5 V | 用户电压档实测 `ATK MODULE=5 V`、`OLED/CAMERA=3.3 V` | 供电排除，旧推断已推翻 |

### D. 调试器与寄存器

| # | 现象 | 根因 | 解决方式 |
|---|---|---|---|
| D1 | `MCR=0x00010002`（SLEEP=1）、`BTR=0x01230000`、`IER=0`、两次间隔 3 s 读数一样 | 软件复位 + halt 瞬间的 bxCAN 复位默认值，不是运行态 | 见 A4/D2 |
| D2 | `AFRH=0x000AA000` 被读成 PA11/PA12 = **AF10(OTG_FS/USB)**，差点认定「引脚分给 USB」 | 同样是复位/旧状态残留 | 探针帧带回运行态 `AFRH=0x00099770` → **PA11/PA12 = AF9(CAN1)**，`BTR=0x001A0005`（SILM=0、LBKM=0），实锤推翻 |
| D3 | `VTOR = 0x00000000` | 复位默认值，不能区分主 Flash / 系统 Bootloader | 不拿它判断运行态；用开机横幅 |

### E. 协议层

| # | 现象 | 根因 | 解决方式 |
|---|---|---|---|
| E1 | 4 轮只读只有 2 轮收到响应 | ISO-TP 首帧后板子在等流控帧，后续 `0x22` 被状态机忽略（推断，未单独定量） | 补 `30 00 00` 流控后一次收到两个连续帧 |
| E2 | `0x22 0200` 稳定回 `7F 22 72` | **只读构建的设计门控**：`STORAGE_TRUSTED=0` → `storage_verified=0` → `storage_ready=0` → `eeprom_read()` 恒返回 −1 → `metadata_load()` 失败 → `append_did()` 返回 −3 → NRC `0x72` | 不是缺陷，不要修；可写变体设 `STORAGE_TRUSTED=1` 就正常 |
| E3 | `34 …` 回 `7F 34 22` | `programming_allowed = OTA_ENABLE_FLASHING && storage_ready = 0`，对 `0x34/0x36/0x37/0x31` 统一回 NRC `0x22` | 按设计关闭；完整 OTA 需重新构建可写固件 |
| E4 | `10 02` → `50 02 00 32 01 F4` | P2=50 ms、**P2*/S3=5000 ms** | 未来完整 OTA 下载 20–40 s 期间必须发 `3E 00` 保活，否则会话与解锁一起被清 |

### F. 构建与工程

| # | 现象 | 根因 | 解决方式 |
|---|---|---|---|
| F1 | 重新构建的镜像哈希没变，探针没编进去 | 当前 `mdk/explorer_readonly.uvprojx` 是 `--uart` 版（loopback/uart 四个 target），**没有 diagnostics target** | 用不带 `--uart` 的 `generate_explorer_mdk.py` 重新生成工程再构建 |
| F2 | 只读固件无法打开写入门控 | `build_explorer_gnu.py` 有 `assert flag+"=0"`，强制 `OTA_ENABLE_FLASHING/BOOT_HANDOFF/WATCHDOG=0`；板级还要 `UPDATE_CAPABLE=1`、`STORAGE_TRUSTED=1`、`HANDOFF_CAPABLE=1` | 用 `build-temp/build_stage_d.py` 的 `loader`/`app` 变体（详见 OTA 评估文档） |
| F3 | 重新构建改动了历史构建记录 | 构建脚本会重写 `ota-core/evidence/*-gnu.txt` | `git checkout --` 恢复；探针源码/工程/产物全部回滚，探针残留计数 0 |

### G. 文档与仓库

| # | 现象 | 根因 | 解决方式 |
|---|---|---|---|
| G1 | 新建证据不出现在 `git status`，文档引用会断 | `.gitignore` 忽略了整个 `stages/board-lab/evidence/` 目录；Git 不允许在父目录被忽略时重新包含子文件 | 父规则改为 `evidence/*`，再放行 `!evidence/can/` 与 `!evidence/can/*.json|*.txt`；`*.bin` 固件备份仍忽略 |
| G2 | P5 跳线到底该不该动 | 整板照片：两个黄色跳帽**竖直**短接 `CAN_RX↔PA11`、`CAN_TX↔PA12`，本来就在 CAN 侧；官方教程也要求这样接 | 不挪；重插一次可能改善接触（推断，未单独定量） |
| G3 | 一度以为板子没在跑诊断固件 | 511 B 开机横幅：`CAN: normal physical 500 kbit/s`、`loader diagnostics: request 7E0, response 7E8`、NOR/EEPROM 探针健康 | 固件一直在跑；`ota_bsp_can_start()` 成功才会打印 `loader diagnostics` |

## 4. 被推翻的旧判断（单独留档，避免以后重犯）

| 旧判断 | 推翻它的实测 | 现在的结论 |
|---|---|---|
| 板子缺 5 V | 电压档实测 5 V / 3.3 V | 供电正常 |
| P5 没装 / 插在 USB 侧 | 整板照片 | 已在 CAN 侧，保持原样 |
| 板子没在跑诊断固件 | 511 B 开机横幅 | 跑得好好的 |
| PA11/PA12 被配成 USB(AF10) | 运行态探针帧 `AFRH=0x00099770` | AF9 / CAN1 |
| `BUSOFF 0x10` = 物理层断 | 修正波特率后 `CAN_GetStatus=0x0` | 判据自己设错了波特率 |
| `0x22 0200` NRC 0x72 是失败待查 | 源码链 + 实机门控对照 | `STORAGE_TRUSTED=0` 的设计门控 |
| 160 Ω 必须调成 60 Ω 才能通 | 160 Ω 下验收多次逐字一致 | 偏差非阻塞项 |

## 5. 可复现命令 / 脚本索引

| 脚本 | 用途 |
|---|---|
| `stages/board-lab/host/can_uds_acceptance.py` | 四轮只读 `0x22` 验收，落 `uds_readonly_acceptance.json` |
| `stages/board-lab/host/can_uds_multiframe.py` | ISO-TP 首帧 → 流控 → 连续帧，拼 VIN，落 `uds_multiframe_evidence.json` |
| `stages/board-lab/host/can_gate_probe.py` | 门控实证：`10 02` / `27` Seed-Key / 11 B 多帧 `34` / `22 0200` |
| `C:\Users\li14\Desktop\pdf-read-resume\can_errframe_probe.py` | 底层状态判据（**已修正** `PCAN_BAUD_500K`） |
| `C:\Users\li14\Desktop\pdf-read-resume\can_boot_log.py` | 读 COM3 开机横幅（CH340 DTR/RTS 接 NRST，须置 False） |
| `C:\Users\li14\Desktop\pdf-read-resume\can_probe_listen.py` | 监听探针固件主动发出的 `0x7E8` |

底层判据正确写法：`PCAN_BAUD_500K = B.PCAN_BAUD_500K.value  # 0x1C, NOT 0x001C0000`。

## 6. 能写什么、不能写什么

**可以写进简历/面试**：
- 「在真实 CAN 总线（PCAN-USB + STM32F407，500 kbit/s）上打通物理层、ISO-TP 多帧与 UDS `0x22` 诊断，实机读回 17 位 VIN 并核对 20 B 载荷，请求/响应逐帧留证。」
- 「用底层控制器状态与发帧后状态读取定位物理层故障；发现并修正自写探针的波特率常量错误，区分上位机连接状态与总线 ACK 证据。」
- 「在真实 CAN 上验证 Seed/Key 解锁与只读构建下状态 DID 的门控行为。」

**不能写**：
- 「真实 CAN OTA 全链路通过」——只读固件 `OTA_ENABLE_FLASHING=0`，`0x34…0x37` → 安装未在真实 CAN 上验证。
- 「终端电阻已调到标准 60 Ω」——实测 160 Ω，偏差非阻塞但未复测。
- 「真实 CAN 断电恢复通过」——只在 UART 台架做过三种切点。
- 任何把 30–50 s 下载时序当实测值的表述（那是 UART 台架外推）。

## 7. 2026-11-09 补记：首次跑完整 OTA 时链路没建起来（`status=blocked`）

| 现象 | 当时的误判 | 实测 / 根因 | 解决方式 |
|---|---|---|---|
| 用户说「三根线都接了」，`PCAN-USB`/ST-Link OK，但 `status` 超时、50 帧 0 回帧、45 s 窗口 9 次 0 响应 | 「接线应该没问题，多半是刚烧的可写 loader 有问题」 | CH340 三个实例全 `Present=False`，COM4/5/6/7/10/11 全是蓝牙虚拟口；ST-Link 目标电压 **2.87–2.88 V**（11-07 通过时 3.24 V） | 用 A/B 对照定位，不靠猜；下次接线以「设备管理器出现真实 CH340 + 电压档量 5 V ↔ GND ≈ 5 V」为准入条件 |
| 新可写 loader 是不是烧成了 UART 诊断？ | 差点直接重建 | 二进制里 `CAN: normal physical 500 kbit/s`、`loader diagnostics: request 7E0, response 7E8` 都在，UART 字符串缺席 → 构建正确 | 先查镜像字符串，比重新构建便宜 |
| `CAN_GetStatus = 0x0`、有回显帧、无错误帧 | 一度说成「说明总线上有节点在 ACK」 | **当场更正**：这台兼容适配器不上报 ACK 错误，0x0 只说明控制器自身没报错，`bus.state=ACTIVE` 同理 | 不拿状态当链路证据；用「烧回当时通过的镜像」+「主动发帧探针」两个 A/B 判据 |
| 烧回 11-07 打通过的镜像（`8a383388…`，全片擦除+校验+复位）后仍 0 回帧 | — | 固件、构建变体、门控、PC 客户端、驱动、波特率常量（0x1C 正确）**全部排除** | A/B 必须比对产物 sha256，不能只回滚源码 |
| 主动发帧探针 0 帧（11-07 同一手法 8 s 收 18 帧） | — | 板子这一刻没有作为 CAN 节点工作；`g_probe_stage` 读 `0x1FFF2A4D`（非 1–7）再次证明 SWD 热读快照不可信 | 探针只作判据、不作结论；完整回滚源码/工程/产物/板子 |
| 跑完发现 `nrc_72_gate_evidence.json`、`uds_readonly_acceptance.json` 被改成 `failed` | — | **失败态探针覆写了已提交的通过证据** | `git checkout` 还原；本轮失败结果另存 `bringup_20261109_ab.json`（`status=blocked`）。以后实验态脚本先改输出路径 |

**最可能的推断（未实测，别写成结论）**：`USB_232` 未真正接好 → 板子 5 V 轨未建立 → CAN 收发器不工作；2.87 V 符合弱/寄生供电。3.3 V 域有电**不能**反推 5 V 有电（11-07 已因同类归因错误翻过车）。

**新增可复现脚本**（`stages/board-lab/host/`）：`can_ack_hard_probe.py`（50 帧 + 错误帧/回显 + 底层状态，用正确的 `PCAN_BAUD_500K.value`）、`can_watch_window.py`（45 s 被动监听 + 周期请求，区分「板子没启动」与「稍后开始应答」）。

**证据边界（第 7 节写于当时）**：真实 CAN 完整 OTA（`0x34…0x37` → 安装）**当时仍未验证**；今晚更晚些时候已在同一次接线中补上，见第 8 节。本表保留当时的判断不改写。

## 8. 2026-11-09 收口：打开电源开关后完整 OTA 通过

| 现象 | 当时的误判 | 实测 / 根因 | 解决方式 |
|---|---|---|---|
| 第 7 节那次「三根线都接了」但链路零回帧、2.87 V、CH340 不在线 | 推断为 `USB_232` 接线或 5 V 轨问题 | **根因就是板子电源开关没开**。打开后 CH340 立刻枚举成 COM3、ST-Link 目标电压 3.24 V、横幅与只读验收全部恢复，`status=passed` | 以后接线自检第一条就量 **ST-Link 目标电压**：3.24 V = 正常，2.87 V = 供电不足/寄生供电。比量电阻、猜跳线都快 |
| `uds_tool.py status` 显示 `active_version=23 / min_version=23`，想用现成的 v14 包 | 「v14 是准备好的包」 | `0x37` 要求 `version > min_version && > active_version`（`uds.c:429`），v14 会被 `7F 37 31` 拒；14 s 擦除白费 | 先 `status` 再选包；用 `make_image.py --version 24 --sign` 现场生成 |
| `uds_tool.py inspect` 输出 `signature_verified: false` | 「包没签好，得重新打包」 | 该字段在 `inspect_package()` 里是**硬编码 False**，工具根本不验签；用纯 Python Ed25519 独立验 v24/v14 都是 `OK`，MCU 侧自己验签 | 反直觉的工具输出先读实现，别急着改产物 |
| `0x11` 之后 CAN 上 `status` 超时 | 「安装失败了」 | `firmware/app/main.c:22` 的 `ota_bootloader_can_rx` 是**空实现**：App 只保留 UART 诊断用于交棒，CAN 上无人应答是设计行为 | 改 `--backend uart --serial-port COM3` 读状态 DID；该后端会主动把 DTR/RTS 拉低，不会复位板子 |
| SWD `hotplug` 读不回 App 区（`Unable to get core ID`） | — | App 运行时调试器附着失败（历史坑复现） | 改 `mode=normal` 复位一次读取；不影响已写入的 Flash 内容 |

**实测结果**：v24 签名包 44920 B，`0x10 02`→Seed/Key→`0x34`（擦除 14.197 s）→**89 个 `0x36` 块**（3.757 s）→`0x37`（0.321 s）→`0x31`（0.269 s）→`0x11`，**18.552 s、零负响应、296 个事件**；复位后 `loader: handing off to the app at 08010000` → App 健康检查横幅；SWD 读回 44824 B sha256 `47f604c1…` 与 payload **逐字节相同**，96 B 凭证头与签名均一致（`ec40408a…`）；终态 `IDLE/application/v24/boot_attempts=0/seq 260`。全程**没发 `3E 00`**，实测证实 11-09 白天「同步 job 不计时、无需保活」的源码结论。

证据：`stages/board-lab/evidence/can/real_can_ota_summary.json`、`can-ota-01.jsonl`、`status-before-can-ota.json`、`status-final-can-ota.json`、`installed-app.bin`、`installed-ticket.bin`、`boot_banner_20261109_can_ota.txt`；执行清单 `stages/board-lab/REAL_CAN_OTA_RUNBOOK.md` 第 12 节；journal `2026-11-09-real-can-full-ota-passed.md`（可写简历 = Y）。

**仍未验证**：真实 CAN 上的断电恢复、总线负载/错误帧、长时间稳定性、从运行中 App 经 CAN 发起 OTA、OTP 单调计数器、WRP/RDP2。
