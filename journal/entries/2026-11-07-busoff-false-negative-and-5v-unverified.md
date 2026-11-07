# BUSOFF 判据的假阴性，以及「2.4 V 不等于板子收发器有电」

## 1. 一句话

单帧探针报 `CAN_GetStatus = 0` 并不能说明链路恢复；而此前用 CANH/CANL ≈2.4 V 推出的「板子 CAN 收发器有 5 V」也不成立——那电平可能完全来自适配器侧。

## 2. 背景

真实 CAN 台架验收，板子烧的是 `explorer_ZG_diagnostics_READONLY.bin`（纯 CAN 只读诊断，500 kbit/s，`0x7E0` → `0x7E8`）。此前 `can_errframe_probe.py` 两次复现发帧后 `CAN_Read = 0x10` / `CAN_GetStatus = 0x10`（BUSOFF）。用户本轮反馈「测电流都是 0，感觉没接好」，并重新插过 USB-CAN。

## 3. 现象（实测）

- `PCAN-USB`：`OK / Present=True`（回来了）；**CH340 全部实例 `Present=False`**，当前 COM4/5/6/7/10/11 全是**蓝牙虚拟串口**，没有一个是板上 `USB_232`。
- ST-Link `mode=hotplug` 连上：`Voltage 3.24 V`、`Device ID 0x413`。
- `can_full_probe.py` 扫 11 个速率（1M/800k/666k/500k/400k/250k/200k/125k/100k/83.3k/50k）：**全部 0 回帧，且每个速率 `CAN_GetStatus = 0x0000`**。
- 紧接着跑 `can_errframe_probe.py` 对照：`CAN_Initialize=0`、`CAN_Write=0`、`CAN_Read=0x10`、`CAN_GetStatus=0x10` —— **BUSOFF 稳定复现**。
- Flash 向量表 `20020000 08009969 080099B9 080099B9` 与构建镜像 `.bin` **逐字一致**，固件没丢。
- SWD 读 `0x40006400`（bxCAN1 真实基址）两次、间隔 3 s，读数完全相同：`MCR=0x00010002`（SLEEP=1）、`MSR=0x0000040A`、`TSR=0x1C000000`、`IER=0`、`ESR=0`、`BTR=0x01230000` —— 全是 bxCAN **复位默认值**，不是固件该写的 `BTR≈0x001A0005`。`RCC_APB1ENR=0x06040000` 里 CAN1EN/CAN2EN 已置位。GPIOA `AFRH=0x000AA000` 解析为 PA11/PA12 = **AF10(OTG_FS)**，而源码写的是 `GPIO_AF9_CAN1`（HAL 定义 0x09）。

## 4. 排查（留弯路）

第一眼看到全速率 `0x0000` 差点写成「BUSOFF 消失、物理层改善了」，甚至准备直接跑验收。停下来做对照才发现问题。另一个弯路是更早一轮把 `0x40002000` 当成 CAN1 基址读——那其实是 **TIM14**，之前「读出全 0 是复位值」的解释是错的，正确基址是 `0x40006400`。这组 SWD 读数还受到调试器连接时是否 halt/复位内核的影响，不能直接当运行态证据。

## 5. 根因

- `can_full_probe.py` 的 `0x0000` 是**假阴性**：每个速率只发 1 帧、等 0.6 s，且 python-can 路径没开 `PCAN_ALLOW_ERROR_FRAMES`，TEC 来不及累积到 bus-off；`CAN_Initialize` 又清了错误标志。对照脚本开错误帧并从 `CAN_Read` 读，立刻复现 `0x10`。**判据必须是「发帧后、开错误帧、从底层读」**，单帧 + 缓存状态不算。
- 「2.3~2.4 V 隐性 → 板子 CAN 收发器有 5 V」这个旧推断**不成立**：UTC-T01 拨在 `120R` 时，它自己的收发器就在总线上输出 ≈2.5 V 隐性；板子侧收发器若没电呈高阻，用户量到的电压完全一样。电压读数只能证明「总线上有人把电平建立起来」，不能证明是板子。同理 160 Ω 也可能全部来自适配器侧。
- 曾怀疑**板子 5 V 供电**：`USB_232`（CH340）完全不在线，而 ST-Link 只看到 3.24 V（3.3 V 域）。CAN 收发器若缺 5 V，MCU 仍可被 ST-Link 读到，但总线上不会有任何节点 ACK → BUSOFF。**该推断于当轮被用户实测推翻**：用户用电压档量到板上 `ATK MODULE` 测试点 **5 V**、`OLED/CAMERA` 测试点 **3.3 V**，两路供电都在；且用户说明「电流 0」量的是 **CANH–CANL 回路**——隐性时差分电压≈0、120 Ω 上无压降，电流本就近 0，属正常，不是接线问题。故**供电与 CAN 线接线均排除**，断点收敛到「板子此刻是否在运行诊断固件 / CAN1 是否已初始化」。
- SWD 看到的 CAN1 复位默认值与 AF10 疑点暂不作结论：调试器 halt 瞬间的中间态、或固件初始化早期卡住都可能产生这种读数，需要串口横幅才能确认。

## 6. 修复

- 给 `can_full_probe.py` 标记为不可单独作判据；正式验收统一用 `can_errframe_probe.py` 的方式（开 `PCAN_ALLOW_ERROR_FRAMES`、发帧后从 `CAN_Read`/`CAN_GetStatus` 读）。
- 把「板子 5 V 轨未实测」写成显式边界，不再用 CANH/CANL 电压代替供电证据。
- 纠正 CAN1 寄存器基址为 `0x40006400`（此前误用 `0x40002000` = TIM14）。

## 7. 验证

本条实测项：设备 PresentOnly 状态、3.24 V、11 速率 0 回帧、`0x10` 复现、Flash 向量表一致、CAN1/RCC/GPIOA 寄存器读数、HAL 中 `GPIO_AF9_CAN1=0x09` / `GPIO_AF10_OTG_FS=0x0A` 定义；用户电压档实测 `ATK MODULE=5 V`、`OLED/CAMERA=3.3 V`；`mode=normal` 软件复位后 `VTOR=0x00000000`（复位默认值），再次测得 `CAN_Read=0x10` / `CAN_GetStatus=0x10` 与 20 帧 `0x22` 全 0 回帧 —— **复位未改变链路状态**。ST-Link `mode=hotplug` 反复报 `Unable to get core ID`，只有复位瞬间能附着。板子开机横幅、P5 跳线位置、终端电阻实际归属仍**未实测**；链路验收**未完成**。
当轮补查并排除驱动侧：`PCAN-USB` `Status=OK` / `DEVPKEY_Device_ProblemCode=0`；驱动为 PEAK-System 官方 `pcan_usb.inf` 4.4.0.16619（2022/9/22，Microsoft WHQL 签名）、`PCAN_USB.sys` 同版本，`PCANBasic.dll` 4.6.2.753 在 System32/SysWOW64 各一份；直接调 `CAN_Initialize(PCAN_USBBUS1, 500k)=0x0`、`CAN_GetValue(HARDWARE_NAME)="PCAN-USB"`。驱动 4.4.0 与 API 4.6.2 属 PEAK 两条独立版本线，初始化成功即证明兼容。故 **BUSOFF 不在 PC 侧**；此前某次扫到 `Present=False` 是瞬时枚举波动，设备实际一直在线。

## 8. 学到

「状态寄存器返回 OK」和「链路能通信」之间隔着采样时机与错误帧开关；一个自写的探针若比正式判据更宽松，就会产出比事实更乐观的结论。另一条：总线上的静态电平是**所有节点共同作用**的结果，不能反向归因到某一个节点。

## 9. 简历表述

链路未打通前**不可使用**。若后续打通，可写：「通过底层错误计数器与发帧后状态读取，区分适配器侧错误与总线无 ACK，定位供电与引脚复用配置问题」。当前只完成排障，未形成成果句。

## 10. 边界

- 本轮无板级实测供电数据；3.24 V 来自 ST-Link 的目标电压检测，只覆盖 3.3 V 域。
- SWD 寄存器读数是在调试器连接状态下取得的，可能不是固件稳定运行态；AF10 疑点待串口横幅与重新上电后复核。
- 「电流为 0」需先分清量的是 CANH–CANL 回路（隐性时本就近 0，正常）还是板子 5 V 供电回路（0 才是故障）；用电流档并联测电源会短路。
- **真实 CAN 链路仍未打通，不能写进简历或项目结论。**
