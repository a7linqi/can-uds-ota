# CAN 台架：接线与验证（USB-CAN 到货即用）

- 适用：正点原子探索者 V3（STM32F407ZGT6，CAN1）
- 目标：让上位机通过**真实 CAN 总线**跑通 `0x10/0x22/0x27/0x3E`，为 `0x34/0x36/0x37` 铺路

## 1. 先确认三件事，缺一不可

| 条件 | 说明 |
|---|---|
| **固件必须是 normal 模式** | 三种镜像里只有两种是 normal：`explorer_ZG_uart_READONLY`（板上现在这颗，日志 `CAN: normal physical 500 kbit/s`）和 `explorer_ZG_diagnostics_READONLY`（纯 CAN，无 UART 日志）。**`explorer_ZG_loopback_READONLY` 是 silent internal loopback，接物理总线永远收不到外部帧**，别拿错 |
| **总线两端要有 120 Ω** | 只有两个节点时，两端各一个（板端若已有跳线帽就不必外加） |
| **P5 跳线切到 CAN** | V3 手册的 `P5`（旧版 `P11`）是 CAN/USB 选择，需按手册短接；切错时 PA11/PA12 根本没接到收发器 |

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

**② 双方都接上，先看有没有 ACK**
```powershell
python -c "import can; b=can.Bus(interface='slcan', channel='COM7', bitrate=500000); b.send(can.Message(arbitration_id=0x123, data=b'\x00', is_extended_id=False)); print('sent, state:', b.state)"
```
`state: error-active` 且无 `ACK` 报错 = 两个节点都在线、波特率一致。

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
