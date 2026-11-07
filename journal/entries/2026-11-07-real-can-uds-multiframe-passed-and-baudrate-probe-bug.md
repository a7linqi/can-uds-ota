# 真实 CAN 打通了：ISO-TP 多帧 + UDS 0x22 实机通过；此前 BUSOFF 全是判据自己的 bug

## 1. 一句话

真实 CAN 总线上完成 ISO-TP 多帧 VIN 读取（20 B 载荷、2 个连续帧），同时查明前几轮反复出现的 `BUSOFF 0x10` 来自自写探针把 `PCAN_BAUD_500K` 写成 `0x001C0000`（应为 `0x1C`）——被测的判据自己设错了波特率。

## 2. 背景

真实 CAN 台架验收是项目最后一块空白。板子烧 `explorer_ZG_diagnostics_READONLY.bin`（纯 CAN 只读诊断，500 kbit/s，`0x7E0` → `0x7E8`），上位机是 UTC-T01/`PCAN-USB`（VID `0C72:000C`，PEAK 驱动 4.4.0.16619 + `PCANBasic.dll` 4.6.2.753）。此前开机横幅已确认固件 NORMAL 500 kbit/s、`ota_bsp_can_start()` 成功、P5 跳线照片确认在 CAN 侧、5 V/3.3 V 供电实测正常。

## 3. 现象（全部实测）

**① 临时主动发帧探针（已回滚）**
- 临时在 `firmware/bootloader/main.c` 加每 500 ms 发一帧 `0x7E8`，数据携带运行时 `GPIOA->AFRH` 与 `CAN1->BTR`。重生成不带 `--uart` 的 MDK 工程、重新构建（44152 B）、SWD 烧写。
- `can_probe_listen.py` 8 s 收到 **18 帧** `0x7E8 data=70 97 09 00 05 00 1A 00`：
  - `AFRH=0x00099770` → **PA11/PA12 = AF9 (CAN1)**（此前 SWD 读到 AF10/OTG_FS 是复位瞬间的假象，实锤推翻）；
  - `BTR=0x001A0005` → BRP=5、TS1=10、TS2=1、**SILM=0、LBKM=0** → `CAN_MODE_NORMAL`、500 kbit/s。
- 板子 TX 路径（MCU → P5 → 收发器 → 接线 → 适配器）由此确认畅通。

**② 波特率 bug 定位**
- `can_errframe_probe.py` 里写死 `PCAN_BAUD_500K = 0x001C0000`；查 `can.interfaces.pcan.basic` 实际 `PCAN_BAUD_500K = 0x1C`（`c_ushort`）。`0x001C0000` 传给 `CAN_Initialize` 被截成 `0x0000`（无效波特率）→ 适配器与板子不同速 → 无 ACK → 适配器自己 bus-off。
- 改成 `B.PCAN_BAUD_500K.value` 后，同一脚本立刻变成：`CAN_Initialize=0x0`、`CAN_Write=0x0`、`READ 0x7E8 10 14 62 F1 90 4C 4F 54`、`CAN_GetStatus = 0x0`。**之前所有 `0x10` 全是这个脚本自己造成的。**

**③ 原版固件正式验收（已烧回原版，无探针）**
- `can_uds_acceptance.py`：4 轮（3× `0x22 F190` + 1× `0x22 0200`），每轮独立开通道；**2 次收到 UDS 正响应**，`status=passed`，证据 `stages/board-lab/evidence/can/uds_readonly_acceptance.json`。
- `can_uds_multiframe.py` 完整 ISO-TP 多帧：
  ```
  TX 0x7E0 03 22 F1 90 00 00 00 00
  RX 0x7E8 10 14 62 F1 90 4C 4F 54      (首帧, total=20 B)
  TX 0x7E0 30 00 00 00 00 00 00 00      (流控 BS=0 STmin=0)
  RX 0x7E8 21 41 30 30 30 30 30 30
  RX 0x7E8 22 30 30 30 30 30 30 31
  ```
  拼出 20 B：`62 F1 90 4C 4F 54 41 30 30 30 30 30 30 30 30 30 30 30 30 31`，即 `62 F1 90` + 17 位 VIN `LOTA0000000000001`。`status=passed`，证据 `uds_multiframe_evidence.json`。
- `0x22 0200` 回 `03 7F 22 72`（NRC 0x72）——**未通过，如实记录**，不并入结论。

## 4. 排查（留弯路）

这一轮最大的弯路：把 `can_errframe_probe.py` 的 `0x10` 当「物理层铁证」，据此写了多条 journal、让用户反复量电阻/电压/电流、核对 CANL 极性、拍整板照片找 P5、甚至准备重烧探针固件；结论一路从「缺 5 V」→「P5 没装」→「P5 已对、断点在复用」来回推翻。真正该先做的是**校验判据本身**：拿一个已知正确的值交叉核对常量。另一个弯路是把 SWD `mode=normal`（软件复位 + halt）瞬间的 `AFRH=AF10` 当成运行态配置；最终是用「让运行中的固件把寄存器值放进 CAN 帧发出来」才拿到可信数据。

## 5. 根因

- **主因（判据 bug）**：`PCAN_BAUD_500K` 常量写错（0x001C0000 vs 0x1C），导致所有基于该脚本的 BUSOFF 结论失真，并掩盖了链路其实已经可通的事实。
- **硬件侧**：P5 跳线照片显示两个黄色跳线帽竖直短接 `CAN_RX↔PA11`、`CAN_TX↔PA12`，本来就在 CAN 侧；官方《探索者 CAN 通讯实验》也要求这样接，且板子自带 120 Ω 终端电阻（解释用户实测 160 Ω）。用户按建议把帽子拔下重插一次，**接触改善可能是链路从「完全不通」到「通」的另一个因素，但未单独定量，标为推断**。
- 4 轮中 2 轮无响应，最可能是 ISO-TP 首帧后板子在等流控帧、后续请求被状态机忽略（补发流控后多帧一次通过），**不是**物理层不稳定；未单独验证，标为推断。

## 6. 修复

- `can_errframe_probe.py`：`PCAN_BAUD_500K = B.PCAN_BAUD_500K.value`（0x1C）。
- 临时探针固件**已完整回滚**：`firmware/bootloader/main.c` 探针残留计数 0；`mdk/explorer_readonly.uvprojx` 恢复 `--uart` 版（loopback/uart 四 target）；`build/gnu-explorer/explorer_ZG_diagnostics_READONLY/...bin` 恢复原哈希 `8a383388…`；`evidence/explorer-gnu-results.json` 重新生成；板子已烧回原版固件并校验通过。
- 新增证据：`stages/board-lab/evidence/can/boot_banner_20261107.txt`、`uds_readonly_acceptance.json`、`uds_multiframe_evidence.json`。
- 临时脚本在 `C:\Users\li14\Desktop\pdf-read-resume\`，未进项目源码。

## 7. 验证

已实测：开机横幅 511 B 与 168/42 MHz 时钟、驱动 `ProblemCode=0` / `CAN_Initialize=0x0`、5 V/3.3 V 电压、P5 照片、18 帧探针帧与 AFRH/BTR 运行态值、修正常量后 `CAN_GetStatus=0x0`、原版固件 4 轮 2 次 UDS 正响应、ISO-TP 首帧+流控+2 连续帧拼出 20 B VIN。**未实测**：真实 CAN 上的长时间稳定性、总线负载/错误帧统计、终端电阻调整到 60 Ω 前后的对比、`0x22 0200` 的 NRC 0x72 根因、真实 CAN 上的 `0x34/0x36/0x37/0x31/0x11` 完整 OTA。

## 8. 学到

**判据本身必须先被校验**：自写探针里的一个常量写错，会让整条排查链朝相反方向跑好几天，而且因为它「看起来比 UI 更底层」反而最不容易被怀疑。第二条：要读运行态外设寄存器，最干净的办法是让运行中的固件自己把值发出来，而不是让调试器复位后去抓快照。

## 9. 简历表述

可用：「在真实 CAN 总线（PCAN-USB + STM32F407，500 kbit/s）上打通 ISO-TP 多帧与 UDS 0x22 诊断，实机读回 17 位 VIN 并核对 20 B 载荷，请求/响应逐帧留证」。
**不可写**：「真实 CAN OTA 全链路通过」——只读固件 `OTA_ENABLE_FLASHING=0`，下载/安装服务未在真实 CAN 上验证。

## 10. 边界

- 打通的是**只读诊断链路**（物理层 + ISO-TP + UDS 0x22），不是完整 OTA。
- `0x22 0200` 返回 NRC `0x72`，未通过。
- 终端电阻用户实测 160 Ω（理想两端各 120 Ω 并联 ≈60 Ω）偏高，但**用户确认 UTC-T01 拨码已经在 `120R`**（不是 `0R`），我此前「拨在 0R」的推断作废。适配器原理图显示终端网络含 `R26 120R`、`R4 120R/NC`（NC = 预留/默认不贴装）、BL1551B 模拟开关（由拨码 `CTR` 控制），且 CANH/CANL 上串有多颗 `10R` 电阻；叠加带电测量与表笔/端子接触电阻，可以解释读数偏离理想值（**推断，未逐项定量**）。**关键结论**：链路在 160 Ω 下稳定复现（`status=passed`、底层 `CAN_GetStatus=0x0`，多次验收结果逐字一致），故该偏差不是阻塞项，短线 500 kbit/s 台架不必强求 60 Ω。
- P5 重插与链路恢复的因果、4 轮中 2 轮无响应的协议层解释，均为推断，未单独定量。
- 项目文档中「真实 CAN 未做 / USB-CAN 未到货」的旧表述按本条更新，但**不扩大为 OTA 已验证**。

## 后续更新（2026-11-07 晚，保留原文不改）

本条第 3、7、10 节列为「未通过 / 根因未实测」的 `0x22 0200` NRC `0x72`，已在新条目 `entries/2026-11-07-nrc-72-designed-gate-and-real-can-ota-assessment.md` 中定位为**只读构建的刻意门控**（`STORAGE_TRUSTED=0` → `storage_ready=0` → `eeprom_read` 恒返回 −1 → `metadata_load` 失败 → `append_did` 返回 −3 → NRC `0x72`），不是缺陷，也不再是待办。终端电阻 160 Ω 亦已确认拨码在 `120R` 且链路稳定。真实 CAN 完整 OTA 的工作量评估见 `stages/board-lab/REAL_CAN_OTA_ASSESSMENT.md`。
