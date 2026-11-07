# CAN 台架：接线与验证（2026-11-07 只读诊断已实机跑通，见第 8 节）

- 适用：正点原子探索者 V3（STM32F407ZGT6，CAN1）
- 目标：让上位机通过**真实 CAN 总线**跑通 `0x10/0x22/0x27/0x3E`，为 `0x34/0x36/0x37` 铺路

## 1. 先确认三件事，缺一不可

| 条件 | 说明 |
|---|---|
| **固件必须是 normal 模式** | 三种镜像里只有两种是 normal：`explorer_ZG_uart_READONLY`（板上现在这颗，日志 `CAN: normal physical 500 kbit/s`）和 `explorer_ZG_diagnostics_READONLY`（纯 CAN，无 UART 日志）。**`explorer_ZG_loopback_READONLY` 是 silent internal loopback，接物理总线永远收不到外部帧**，别拿错 |
| **总线两端要有 120 Ω** | 只有两个节点时，两端各一个（板端若已有跳线帽就不必外加） |
| **P5 跳线切到 CAN** | V3 手册的 `P5`（旧版 `P11`）是 CAN/USB 选择，需按手册短接 `1–3`、`2–4`；切错时 PA11/PA12 根本没接到收发器 |
| **适配器拨码在 `120R`** | UTC-T01 顶部三档拨码：`120R` = 正常通信且接通 120 Ω 终端电阻（两设备通信必需有一端接）、`0R` = 正常通信但断开终端电阻、`BOOT` = 仅刷固件（此时设备变 DFU，`PCAN-USB` 会从设备管理器消失） |

面板三个灯 `PWR` / `TXD` / `RXD`：`PWR` 上电常亮，`TXD` / `RXD` 只在有 CAN 收发时闪。厂商手册写「点击 OK 之后，板子发送接受的指示灯会亮起来，证明连接板子成功」——**没人连它时 TXD/RXD 不亮是正常表现**，不是驱动把灯关了；但若帧发出后控制器 BUSOFF，灯同样不亮，要用底层状态区分（见 ②）。

> 结论：**UART 台架镜像不用重烧**就能上物理 CAN —— 它只是同时开了 USART1 日志口。
> 想要"纯 CAN、串口不出字"的版本，用 `python tools/generate_explorer_mdk.py --dfp ... --cmsis ...`
>（不带 `--uart`）生成 `explorer_ZG_diagnostics_READONLY`，再由 `build_explorer_gnu.py` 构建。

## 2. 接线

| USB-CAN | 板子 | 备注 |
|---|---|---|
| CAN_H | CAN_H | 双绞，尽量短 |
| CAN_L | CAN_L | 与 CAN_H 成对 |
| GND | GND | **必须共地**，否则 ACK/错误帧乱跳 |
| — | **不接 VCC** | 板子自己供电，避免两边 5 V 打架 |

板端引脚：`CAN1_RX = PA11`、`CAN1_TX = PA12`（AF9）；收发器在板内，出口是接线端子。

## 3. 参数（固件已定死，别在主机侧乱调）

| 项 | 值 | 来源 |
|---|---|---|
| 波特率 | **500 kbit/s** | APB1 42 MHz ÷ 6 ÷ (1+11+2) = 500 000 |
| 采样点 | (1+11)/14 = **85.7%** | `BS1=11TQ, BS2=2TQ, SJW=1TQ` |
| 帧格式 | 标准数据帧（11 位 ID） | 滤波器 `0x7FF<<5` |
| 请求 ID | **0x7E0** | 主机 → ECU |
| 响应 ID | **0x7E8** | ECU → 主机 |
| 滤波器 | bank0，ID=0x7E0，掩码 0x7FF | 只收请求 ID，响应由主机侧过滤 0x7E8 |

## 4. 上位机

```powershell
pip install python-can
```

`--interface` 与 `--channel` 取决于你买的适配器：

| 适配器 | `--interface` | `--channel` 示例 |
|---|---|---|
| CANable / 各类 slcan 固件（虚拟串口） | `slcan` | `COM7` |
| PEAK PCAN-USB | `pcan` | `PCAN_USBBUS1` |
| Kvaser | `kvaser` | `0` |
| 周立功 | `zlg` | `0` |

## 5. 三级验证，从物理层往上排

**① 只有一端时必然报错，这是正常的**
先只接板子、不接适配器 → 主机发任何帧都会 `ACK error`。**这说明你的适配器确实在发**。

**② 双方都接上，先看有没有 ACK（判据必须用底层状态）**

> 2026-11-07 修正：原先这句 `bus.state` 判据**不可用**。python-can 的 `state` 只是缓存属性（初值 ACTIVE），
> 初始化时还会打开 `PCAN_BUSOFF_AUTORESET`；`CAN_Initialize` 又清掉错误标志，所以「初始化后读状态」永远看不到 bus-off。
> 必须在**一帧发出之后**从 PCANBasic 底层读。

```powershell
cd C:\Users\li14\Desktop\pdf-read-resume
py can_errframe_probe.py
```

| 发帧后 `CAN_Read` / `CAN_GetStatus` | 含义 |
|---|---|
| `0x0` | 帧成功上总线，有节点 ACK |
| `0x10` | `PCAN_ERROR_BUSOFF`，没有节点 ACK，帧没发出去（灯自然不闪） |
| `0x1400` | `PCAN_ERROR_ILLHW`，适配器不在线（拨码是否在 `BOOT`？重新插 USB） |

实测 2026-11-07：`CAN_Initialize=0x0`、`CAN_Write=0x0`、`CAN_Read=0x10`、`CAN_GetStatus=0x10`，两次复现；
同期 `bus.state` 仍报 ACTIVE、扫 6 种波特率与 20 帧 `0x22` 的 `0x7E8` 回帧均为 0。

**③ 跑 UDS**
```powershell
cd D:\Project\Can-learning\ota-core
python tools\uds_tool.py request --hex "22 F1 90" --backend can --interface slcan --channel COM7 --bitrate 500000
```
期望（真实 ECU，非台架）：
```json
{"request": "22 F1 90", "response": "62 F1 90 4C 4F 54 ...", "response_bytes": 20}
```

## 6. 失败了怎么读现象

| 现象 | 最可能的原因 | 处理 |
|---|---|---|
| 一发改就 `ACK error`，总线无第二节点 | 只有自己在总线上 | 接上第二个节点（正常现象） |
| 一直 `ACK error` 即使两端都接了 | CAN_H/L 接反、没共地、P5 没切到 CAN | 万用表量 CAN_H-CAN_L 静态约 2.5 V（隐性） |
| `bus-off` / 错误计数飙到 255 | 波特率或采样点不一致 | 确认固件是 500 k、主机也是 500 k |
| 能发、抓不到任何回包 | 固件是 loopback 模式；或滤波器/ID 不对 | 看开机日志那行 `CAN: ...` |
| 能收到帧但 `22 F1 90` 超时 | ISO-TP 流控没发出去 / 响应 ID 过滤错 | 用 `can.viewer` 看 0x7E8 有没有出首帧 |
| 只收到自己发的帧 | 固件 loopback + 主机 `receive_own_messages` | 正常，换成 normal 固件 |

## 7. 记录模板（跑通后填这里）

```
日期：
固件：explorer_ZG_can_READONLY.bin / sha256
适配器：型号 + interface + channel
接线：H/L/GND + 终端电阻数量
结果：info / VIN / 会话 三条的回包
异常：
```

> 注意：`explorer_ZG_loopback_READONLY` 只在芯片内部回环，接真实总线收不到任何外部帧；
> 板上现在跑的 `explorer_ZG_uart_READONLY` 已经是 normal 500 kbit/s，可以直接上物理 CAN。

## 8. 实机验收记录（2026-11-07，已跑通）

- 适配器：UTC-T01 / `PCAN-USB`（VID `0C72:000C`），PEAK 驱动 4.4.0.16619 + `PCANBasic.dll` 4.6.2.753，channel `PCAN_USBBUS1`，**500 kbit/s**。
- 固件：`explorer_ZG_diagnostics_READONLY.bin`（sha256 `8a3833885295f7cde4e3207a0669e39b21c6361b2b9c82ff906ba7caafd34f29`）；开机横幅 `CAN: normal physical 500 kbit/s`、`loader diagnostics: request 7E0, response 7E8`、`hclk=168 MHz / pclk1=42 MHz`。
- 接线：CANH/CANL/GND 共地；P5 两个跳线帽竖直短接 `CAN_RX↔PA11`、`CAN_TX↔PA12`（CAN 侧）。板子自带 120 Ω 终端电阻；UTC-T01 拨码**确认在 `120R`**，用户实测 CANH–CANL 160 Ω（理想 ≈60 Ω）。适配器原理图显示终端网络含 `R26 120R`、`R4 120R/NC`（预留位）、BL1551B 模拟开关（由拨码 `CTR` 控制），CANH/CANL 上还串了多颗 `10R` 电阻；叠加带电测量与表笔/端子接触电阻，可解释读数偏离理想值（推断，未逐项定量）。**链路在 160 Ω 下稳定通过验收，该偏差不是阻塞项，短线台架不必强求 60 Ω。**
- 验收脚本：`C:\Users\li14\Desktop\pdf-read-resume\can_uds_acceptance.py`（4 轮只读 `0x22`，`status=passed`，2 次正响应）与 `can_uds_multiframe.py`（ISO-TP 多帧，`status=passed`）。
- 逐帧结果：
  ```
  TX 0x7E0 03 22 F1 90 00 00 00 00
  RX 0x7E8 10 14 62 F1 90 4C 4F 54   首帧，total 20 B
  TX 0x7E0 30 00 00 00 00 00 00 00   流控 BS=0 STmin=0
  RX 0x7E8 21 41 30 30 30 30 30 30
  RX 0x7E8 22 30 30 30 30 30 30 31
  ```
  拼出 20 B：`62 F1 90` + 17 位 VIN `LOTA0000000000001`。
- 证据：`stages/board-lab/evidence/can/uds_readonly_acceptance.json`、`uds_multiframe_evidence.json`、`boot_banner_20261107.txt`。
- **判据坑（务必记住）**：自写探针 `can_errframe_probe.py` 曾把 `PCAN_BAUD_500K` 写成 `0x001C0000`，正确值是 `0x1C`（`c_ushort`）；传给 `CAN_Initialize` 被截成 `0x0000` → 适配器与板子不同速 → 假的 `BUSOFF 0x10`，误导了一整轮排查。修正后同一脚本 `CAN_GetStatus=0x0` 且收到 `0x7E8`。**任何状态判据都要先交叉校验常量。**
- **边界**：只读诊断固件 `OTA_ENABLE_FLASHING=0`，真实 CAN 上的 `0x34/0x36/0x37/0x31/0x11` 完整 OTA 未验证；`0x22 0200` 回 `03 7F 22 72` 已定位为只读构建 `STORAGE_TRUSTED=0` 的门控设计，不是缺陷（证据 `evidence/can/nrc_72_gate_evidence.json`，复现脚本 `host/can_gate_probe.py`）；总线负载/错误帧/长时间稳定性未测。真实 CAN 完整 OTA 的改动清单与工作量评估见 `stages/board-lab/REAL_CAN_OTA_ASSESSMENT.md`。
