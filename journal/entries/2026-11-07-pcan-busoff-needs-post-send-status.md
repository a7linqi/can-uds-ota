# 推翻「PCAN 状态判据不可信」：状态要在发帧后从底层读

- 日期：2026-11-07
- 类型：决策（推翻旧判断）
- 关键词：`pcan` `busoff` `CAN_GetStatus` `物理层` `python-can`
- 验证等级：实机（UTC-T01 适配器 + 探索者 V3 板）；目标真实 CAN 链路尚未打通

## 1. 一句话

把「PCAN 状态在所有波特率都报 OK、不能当 ACK 判据」修正为：`bus.state` 是缓存、`CAN_Initialize` 会清错误，只有**发帧后用 PCANBasic 底层 `CAN_Read` / `CAN_GetStatus` 读**才有效；`0x10 = BUSOFF` 是「没有节点 ACK」的硬判据。

## 2. 背景与约束

真实 CAN 台架：UTC-T01（VID/PID 克隆为 `0C72:000C`，PEAK PCAN-USB 兼容固件）+ 探索者 V3 板，板内跑 `explorer_ZG_diagnostics_READONLY.bin`（纯 CAN、500 kbit/s、0x7E0/0x7E8）。上一轮已把固件接收链逐层核对完毕，没有断点，因此不许再靠改固件去猜物理层，必须先找到可信的物理层判据。

## 3. 现象

- `CAN_Initialize(PCAN_USBBUS1, 500K) = 0x0`，`CAN_Write = 0x0`，但 `CAN_Read = 0x10`、`CAN_GetStatus = 0x10`，连跑 2 次完全稳定。
- python-can 的 `bus.state` 始终 `BusState.ACTIVE`；`can_ack_probe.py` 在 1M/500k/250k/125k 连发 20 帧后一律报 `OK`。
- 发 20 帧只读 `0x22`（`03 22 F1 90`、`03 22 02 00`）以及扫 6 种波特率，`0x7E8` 回帧数均为 0。
- 适配器 `TXD`/`RXD` 灯不亮；`PWR` 未单独确认。板子开机横幅正常（`CAN: normal physical 500 kbit/s`、NOR/EEPROM 健康）。

## 4. 排查过程

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 波特率不匹配 | 扫 1M/800k/500k/250k/125k/100k 发 `0x22` | 6 种全部 0 回帧，排除 |
| 2 | 固件接收链断了 | 核对 PA11/PA12→AF9_CAN1、bank0 掩码 `0x7FF`、向量表 IRQ20=`0x0800670F`（符号 `0x0800670E`）、ISO-TP 单帧 DLC=8、UDS `0x22`→`0x7E8` | 全对，无断点 |
| 3 | `bus.state` 可信 | 查 python-can pcan 源码 | `state` 只是 `self._state` 缓存，初值 ACTIVE；且初始化时开了 `PCAN_BUSOFF_AUTORESET` |
| 4 | 底层状态可读 | ctypes 直连 `PCANBasic.dll`，`CAN_SetValue(ALLOW_ERROR_FRAMES, ON)` 后发 1 帧再 `CAN_Read` / `CAN_GetStatus` | `0x10`（`PCAN_ERROR_BUSOFF`），2 次复现 |
| 5 | 想用 SWD 读 CAN1 寄存器交叉验证 | `STM32_Programmer_CLI -r32 0x40002000 0x20` | `mode=normal` 先 Software reset，读到的是复位值全 0；`mode=hotplug` 报 `Unable to get core ID`，放弃 |

**转折点**：第 4 步 `CAN_Read -> 0x10` 且 `CAN_GetStatus -> 0x10` 稳定复现。旧判断错在**读状态的时机与通道**（初始化后立刻读、以及 python-can 的缓存属性），不是 PCAN 状态本身没有判别力。

## 5. 根因

`CAN_Initialize` 会清掉错误标志，`CAN_GetStatus` 本身也有清除语义，python-can 又默认开启 `PCAN_BUSOFF_AUTORESET`，所以「初始化后读状态」和 `bus.state` 都看不到 bus-off。只有在**一帧发出之后**从底层读，控制器因收不到任何 ACK 而进入 BUSOFF 才会显形：帧进了发送队列但没成功上总线，`TXD`/`RXD` 灯自然不闪，板子当然也收不到请求。

## 6. 修复

- 文件：`C:\Users\li14\Desktop\pdf-read-resume\can_errframe_probe.py`（新增，ctypes 直连 + 开错误帧 + 发一帧后读状态）
- 文件：`C:\Users\li14\Desktop\pdf-read-resume\can_bringup_check.py`（新增，先验 `CAN_Initialize` 再连发 20 帧只读 `0x22`）
- 待改：`stages/board-lab/host/can_readonly.py` 的 `CORE_TOOLS` 仍指向 `D:\Project\Can\ota-core\tools`（旧快照）；三个 pc 文件哈希与活库 `Can-learning\ota-core\tools` 都不同，正式验收前必须改指活库。
- 备选方案与放弃原因：用 SWD 读 `CAN1->ESR`/`RF0R` 想直接看接收计数，放弃，因为 CubeProgrammer 的 `mode=normal` 会先软件复位、`mode=hotplug` 连不上内核，读到的不是运行时状态。

## 7. 验证

```
cd C:\Users\li14\Desktop\pdf-read-resume
py can_errframe_probe.py
```

| 项 | 期望 | 实际 |
|---|---|---|
| `CAN_Initialize` | `0x0` | `0x0` |
| `CAN_Write` | `0x0` | `0x0` |
| 发帧后 `CAN_Read` / `CAN_GetStatus` | 物理层通时非 `0x10` | `0x10` / `0x10`，2 次复现 |
| `0x7E8` 回帧 | ≥1 | 0 |

未验证：把物理层修好后 `CAN_GetStatus` 是否回到 `0x0`、能否收到 `0x7E8`；P5 跳线、适配器 120R 拨码、CANH/CANL 极性、板载 120 Ω、共地质量均未实测。

## 8. 学到的东西

1. 「状态全 OK 所以判据不可信」是个偷懒结论：要分清**缓存属性**、**初始化后读**、**发帧后读**三种完全不同的读取时机。
2. 适配器灯不亮 + 零回帧 + 固件全对，优先看控制器是不是 BUSOFF，而不是继续怀疑固件；BUSOFF 意味着帧压根没上总线。
3. 高层库为「好用」做的默认（自动 bus-off 恢复、缓存 state）会掩盖故障；排查物理层必须留一条直连 DLL 的底层路径。

## 9. 简历表述

> 在 STM32F407 + PCAN-USB 真实 CAN 台架上，绕开 python-can 的缓存状态与自动 bus-off 恢复，用 PCANBasic 底层「发帧后读状态」把零回帧定位到 `BUSOFF(0x10)`，两次复现稳定，避免了继续误改已核对无误的固件接收链。

> 边界：该判据只能证明「总线上没有节点 ACK」，具体断点（跳线 / 终端电阻 / 极性 / 供电）仍需逐项实测，未写成已打通。

## 10. 边界与未解决

- 真实 CAN 链路**仍未打通**，不得写进简历或项目结论。
- 待用户实测：UTC-T01 拨码是否在 `120R`（`BOOT` 会让设备变 DFU、`0R` 会断开终端电阻）、板子 `P5`（旧版 `P11`）是否短接 1–3 / 2–4 到 CAN 侧、CANH/CANL 是否接反、GND 是否接在正确端子、板载 120 Ω 是否真的在位、断电测 CANH–CANL 阻值。
- `PCANBasic.dll` 4.6.2.753 与设备驱动 `pcan_usb.inf` 4.4.0.16619 版本不同源，若重插后仍报 `0x1400 ILLHW` 或异常，再排这一条。
