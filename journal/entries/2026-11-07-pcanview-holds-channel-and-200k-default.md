# PcanView 占着通道，还留着 200 kbit/s 的旧配置

## 1. 一句话

上位机 PcanView 显示「已连接 / Status OK」时，我的脚本反而连不上硬件（`0x1400`）；同时它的遗留配置是 **200 kbit/s**，而板子固件是 500 kbit/s。

## 2. 背景

真实 CAN 台架验收阶段。板子已烧纯 CAN 只读诊断固件，横幅 `CAN: normal physical 500 kbit/s`，只接受 `0x7E0`、回 `0x7E8`。此前 python-can 在 1M/800k/500k/250k/125k/100k 六种速率下全部 0 回帧，发帧后底层 `CAN_GetStatus = 0x10`（BUSOFF）。用户打开厂商包里的 PcanView 想确认「到底连没连上」，发来两张截图。

## 3. 现象（实测，读图 + 当场探针）

- PcanView 主窗口状态栏：绿色勾 `Connected to hardware PCAN-USB | Bit rate: 200 kbit/s | Status: OK | Overruns: 0 | QXmtFull: 0`。
- Connect 对话框：`Mode: CAN (SJA1000)`、`Clock Frequency: 8 MHz`、`Database Entry: 200 kbit/s`、`Bit Rate [kbit/s]: 200`、`BTR0=C1`、`BTR1=3E`、`Prescaler=2`、`Sample Point 80%`、`tq 250 ns`、`Nq 20`、`SJW 4`。
- 主窗口 Receive / Transmit 两个列表**全空**。
- 同一时刻跑 `can_errframe_probe.py`：`CAN_Initialize -> 0x1400`（PCAN_ERROR_ILLHW，硬件不可用）。

## 4. 排查（留弯路）

第一反应是「适配器又掉线了」——上一轮确实出现过 `Present=False` / `0x1400`，差点让用户重新插拔 USB。接着又差点把状态栏的 `Status: OK` 当成「总线已恢复、可以跑验收」的证据。这两步都错，而且和上一轮踩过的 `bus.state` 缓存坑是同一个毛病：**把上位机自己的初始化状态当成链路证据**。真正让我停下来的判据是：既然 PcanView 自己说连上了，为什么第二个进程报硬件不可用——两者只能有一个成立。

## 5. 根因

- `0x1400` 不是掉线，是**通道占用**：这台 VID/PID `0C72:000C` 兼容适配器只允许一个客户端，PcanView 已 `CAN_Initialize(PCAN_USBBUS1)`，第二个进程被拒（实测）。
- PcanView 的 `Status: OK` 是「控制器初始化成功、尚未发帧」的静默状态，不表示有节点在 ACK；列表空是被动监听的正常表现，两者都不能证明或否证链路。
- **附带发现的配置不一致**：PcanView 遗留 200 kbit/s；板子侧 `OTA_EXPLORER_CAN_PRESCALER=6u`，按 8 MHz 晶振、PCLK1=42 MHz、`42 MHz / 6 / (1+11+2) = 500 kbit/s` 推算（**板子时钟未用仪器实测**）。而之前的扫描列表 `(1M, 800k, 500k, 250k, 125k, 100k)` 恰好漏掉 200 kbit/s。
- 注意：这个 200 kbit/s **不是**此前脚本失败的原因——脚本自己设 500000，且当时 PcanView 尚未打开。它的价值是把「板子时钟未实测」这条边界重新摆到台面上。
- **当轮补查厂商手册**（`USB-CAN-T01使用说明-V1.2.pdf` 第 11、12 页）：用户所说「视频里应该有个设备」指 Connect 对话框左侧树里的 `PCAN-USB: Device ID FFh`，它是 CAN-Hardware 类设备，**不是 COM 口**；手册默认配置就是 **500 kbit/s**（BTR0=00 / BTR1=1C / 8 MHz SJA1000），与板子一致，用户 PcanView 的 200 kbit/s 属遗留配置。第 12 页原文「**两个板子互相通信的发送接收的数据**」：演示里 Receive 列表的 `123h` 报文来自**第二块 USB-CAN 板**，发送端是 `182h`（`10 12 12 12 12 21 21 02`、100 ms 周期）。**PcanView 不会回复自己发出的帧**，它只显示总线上真实流过的帧。所以「发一条、回一条」必须由总线上第二个节点产生；本项目里那个节点是 STM32 板子，当前它未在总线上应答。这澄清了用户「按视频操作却看不到回复报文」的原因，也说明只靠一个适配器无法复现视频效果。

## 6. 修复

- 任何自动化验收前先关闭 PcanView，释放 `PCAN_USBBUS1`。
- 把 200k / 400k / 666k / 83.3k / 50k 补进扫描，并在每个速率**发帧之后**直接从 `PCANBasic.dll` 读 `CAN_GetStatus`，而不是看 `bus.state`。脚本：`C:\Users\li14\Desktop\pdf-read-resume\can_full_probe.py`（临时探针，不在项目源码里）。
- 顺手核过镜像字符串：`FOUND "CAN: normal physical 500 kbit/s"`、`ABSENT "CAN: silent internal loopback"` → 编译期 `OTA_EXPLORER_CAN_LOOPBACK=0`，控制器是 `CAN_MODE_NORMAL`（会发 ACK），排除「烧成静默回环」这个一度怀疑的方向。

## 7. 验证

**全速率扫描与链路验收尚未执行，未实测。** 本条已实测的只有：PcanView 状态栏与对话框参数、`0x1400`、镜像内两条字符串、`OTA_EXPLORER_CAN_PRESCALER=6u`。扫描结果待用户关闭 PcanView 后补记。

## 8. 学到

上位机 UI 的 `Connected` / `Status OK` 只描述「本机控制器初始化成功」，与「总线上有节点在 ACK」是两件事；同一个 CAN 通道的客户端占用会伪装成「硬件不存在」。上位机的遗留配置也要当作独立变量核对，不能假设它和目标固件一致。

## 9. 简历表述

链路未打通前**不可使用**。若后续打通，可写：「在真实 CAN 台架上用底层控制器状态与逐速率扫描定位物理层故障，区分上位机连接状态与总线 ACK 证据，完成 UDS 只读诊断验收」。当前只完成排障，未形成可写的成果句。

## 10. 边界

- 本条全部为 PC 侧 / 上位机观测，无新增板级实测。
- 板子时钟、P5 跳线位置、终端电阻实际阻值、CAN 收发器型号均**未实测**；160 Ω / 2.3–2.4 V 是用户万用表读数，其中 160 Ω 偏高的成因仍是推断。
- **真实 CAN 链路仍未打通，不能写进简历或项目结论。**
