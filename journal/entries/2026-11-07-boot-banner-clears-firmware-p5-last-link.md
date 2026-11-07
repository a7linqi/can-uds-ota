# 开机横幅证明固件正常：断点收敛到 P5 一根跳线

## 1. 一句话

插回 `USB_232` 后抓到完整开机横幅，`CAN: normal physical 500 kbit/s` 和 `loader diagnostics` 都打印了；此前「板子没在跑诊断固件 / CAN1 没初始化」的推断被推翻，剩下的唯一未验证环节是板上 `P5` 跳线。

## 2. 背景

真实 CAN 台架验收。此前多轮排查已排除驱动、供电、接线、终端电阻、波特率；但 SWD 读到 CAN1 寄存器全是复位默认值，一度推断「固件卡在初始化早期或进了系统 Bootloader」。用户把 `USB_232`（CH340，枚举为 COM3）插回后读横幅。

## 3. 现象（实测）

`can_boot_log.py` 用 `dtr=False rts=False` 打开 COM3（CH340 的 DTR/RTS 接 NRST，置位会把 MCU 按在复位上），一次抓到 **511 字节**横幅，关键行：

```
ATK Explorer reference pins; update engine disabled
CAN: normal physical 500 kbit/s
device_id=0x00000413
flash_bytes=0x00100000
hclk_hz=0x0A037A00
pclk1_hz=0x0280DE80
...
NOR driver probe+dry read ok
loader diagnostics: request 7E0, response 7E8
```

- `hclk_hz = 0x0A037A00 = 168 000 000 Hz`、`pclk1_hz = 0x0280DE80 = 42 000 000 Hz`；配 `Prescaler=6 / BS1=11TQ / BS2=2TQ` → `42 MHz / 6 / 14 = 500 000 bit/s`。
- 端口关闭引发复位后复测：`CAN_Initialize=0x0`、`CAN_Write=0x0`、`CAN_Read=0x10`、`CAN_GetStatus=0x10`（BUSOFF）；20 帧 `0x22` 仍 0 回帧。
- 证据落盘：`stages/board-lab/evidence/can/boot_banner_20261107.txt`。

## 4. 排查（留弯路）

前面几轮用 SWD `mode=normal/hotplug` 读 `0x40006400`，看到 `MCR=0x00010002`（SLEEP=1）、`BTR=0x01230000`、`IER=0`，以及 `GPIOA AFRH=0x000AA000`（PA11/PA12 = AF10/OTG_FS 而非源码的 AF9/CAN1），据此推断「固件没跑起来 / 引脚被分给 USB」。**这是弯路**：那些读数是调试器连接时复位或 halt 内核瞬间取得的，不代表固件稳定运行态；`mode=normal` 本身会软件复位，`VTOR=0x00000000` 也只是复位默认值。真正判断「固件在不在跑」最轻、最干净的证据一直是串口横幅，绕了一大圈才回到它。

## 5. 根因

- **固件侧全部正常**：`CAN: normal physical...` 只在 `OTA_EXPLORER_CAN_LOOPBACK=0`（`CAN_MODE_NORMAL`）分支打印；`loader diagnostics...` 只在 `ota_bsp_can_start()` 返回 0 之后打印，说明 `HAL_CAN_Init / ConfigFilter / Start / ActivateNotification` 全成功，CAN1 在监听、会自动 ACK。时钟 168/42 MHz 与 500 kbit/s 由横幅实测确认，不再是推算。
- 既然控制器在 NORMAL 且已启动、驱动和物理层都清白，却仍无人 ACK，一度推断剩下只能是 **MCU 的 PA11/PA12 与板载 CAN 收发器之间的物理连接**：V3 板 `P5`（旧版 `P11`）是 CAN/USB 二选一跳线，需两个跳线帽把 `1–3`、`2–4` 短接到 CAN 侧。若在 USB 侧，CAN_RX 接到 USB D−/D+，收发器 TXD/RXD 悬空呈隐性，板子看不到总线帧 → 不 ACK → PCAN BUSOFF。
- **当轮用整板照片收回上一条推断**：用户拍到 `P5` 白框，丝印上排 `CAN_RX / CAN_TX`、下排 `PA11 / PA12`，**两个黄色跳线帽已竖直跨接上下两针**，正是「PA11↔CAN_RX、PA12↔CAN_TX」的 CAN 侧接法；旁边 `P10`（USART1↔CH340）同方向插法且串口横幅正常，第二张照片里 P4/P6/B1 也全是竖直插法。故「P5 没装或插在 USB 侧」不成立，此前的收敛结论作废。官方检索亦确认：探索者 CAN 实验要求「通过跳线帽将 PA11 和 PA12 分别连接到 CAN_RX 和 CAN_TX」，且板子自带 120 Ω 终端电阻（解释用户实测 160 Ω）。**P5 是否绝对正确仍受斜拍角度限制（排针列数未数清），但已无证据支持它是断点。**

## 6. 修复

本轮无源码改动、无固件重烧。待用户把 `P5` 切到 CAN 侧后复测；链路打通后再跑正式只读验收并落 events/results/summary。

## 7. 验证

已实测：511 B 横幅全文、168/42 MHz 时钟换算、复位后 BUSOFF `0x10` 复现、20 帧 0 回帧、驱动 `Status=OK / ProblemCode=0`、用户电压档 5 V / 3.3 V。**未实测**：P5 跳线位置、CAN 收发器型号与其 VCC 引脚实测电压、任何 `0x7E8` 回帧、真实 CAN 链路验收。

## 8. 学到

调试器 attach 瞬间的外设寄存器不是运行态证据，尤其 `mode=normal` 会先复位；用它去否定「固件在跑」会得出完全相反的结论。排查顺序上，一条 511 字节的启动日志比一堆寄存器快照更有信息量，应优先拿。

## 9. 简历表述

链路打通前**不可使用**。若后续跑通，可写：「在真实 CAN 总线上完成 STM32F407 与 PCAN-USB 的 500 kbit/s 物理层对接，用开机日志与底层错误状态定位引脚复用跳线问题，完成 UDS 只读诊断验收」。当前只完成排障，未形成成果句。

## 10. 边界

- 横幅证明的是「固件启动并完成 CAN 初始化」，**不等于** CAN 帧已成功收发。
- `CAN: normal physical 500 kbit/s` 是编译分支打印的字符串，但 168/42 MHz 是固件运行时读数，两者一致才使 500 kbit/s 成为实测值。
- **真实 CAN 链路仍未打通，不能写进简历或项目结论。**
