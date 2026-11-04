# UART 台架：接线、烧录、验证（10 分钟清单）

目标：让板子通过 USART1 收发 CAN 帧（`A5 5A` 封装），从而在没有 USB-CAN 的情况下
也能跑通真实 UDS 交互。

## 先认清依赖顺序（重要）

```
ST-Link 可用  ──►  烧 UART 固件  ──►  USB-TTL 接上  ──►  才有 UART 数据
```

板上现在跑的是 NOR 备份固件（`af69df8e…`，只用 SWD，不碰串口），**所以只插 USB-TTL
不会有任何输出**。必须先用 ST-Link 把 UART 固件烧进去。两样都插上才算就绪。

一次性确认当前状态：

```powershell
python D:\Project\Can\build-temp\board-ready-check.py --verbose
```

它会打印三件事：三个台架镜像是否与构建记录一致、ST-Link 是否在、板上**实际**跑的是哪个
镜像、有没有 USB-TTL。最后给出按顺序的下一步命令。

## 接线（正点原子探索者 V3，USART1 = PA9/PA10）

| USB-TTL | 板子 | 说明 |
|---|---|---|
| RXD | **PA9**（USART1_TX） | 交叉：适配器收 ← 板子发 |
| TXD | **PA10**（USART1_RX） | 交叉：适配器发 → 板子收 |
| GND | GND | 必须共地 |
| 3V3/5V | **不接** | 板子自己供电，接了会打架 |

- 板上 `P5`/`P6` 那排排针里找 PA9/PA10；如果不确定，用万用表通断从 MCU 引脚量。
- 波特率 **115200**，8N1，无流控。
- 先只接 RXD 看有没有数据，再补 TXD，能少走一半弯路。

## 烧固件

```powershell
$CLI = "D:\Keil\CubProgram\bin\STM32_Programmer_CLI.exe"
$BIN = "D:\Project\Can-learning\ota-core\build\gnu-explorer\explorer_ZG_uart_READONLY\explorer_ZG_uart_READONLY.bin"
& $CLI -c port=SWD index=0 mode=HOTPLUG freq=1000 -d $BIN 0x08000000 -v -rst
```

期望：`40680` 字节，sha256 `16f94df0488849e064f869d4deef2a211d46597c62d5e8df28af3e0529853d29`。
烧完 VTOR 已在 `0x08000000`，无需额外设置。

> 注意：这一步只写内部 Flash 的前 40 KB，**不碰外部 NOR**。外部 NOR 的第一次擦除必须
> 在 `stages/board-lab/NOR_RESTORE_DRILL.md` 的演练通过之后。

## 验证（三条命令，逐级加深）

### 1. 链路是否活（只看有没有字节）

```powershell
python D:\Project\Can-learning\ota-core\tools\uds_tool.py info --backend uart --serial-port COMx
```

期望：打印固件/状态信息。**失败先看这里**：

| 现象 | 原因 |
|---|---|
| 一个字节都没有 | TXD/RXD 没交叉、没共地、烧的还是备份固件 |
| 有字节但 CRC 报错 | 波特率不对（板子是 115200）、线太长/干扰 |
| 偶尔丢帧 | 见下一节：解码器重同步缺陷（已有补丁提案） |

### 2. 单服务往返（看帧格式对不对）

```powershell
cd D:\Project\Can-learning\ota-core
python tools\uds_tool.py request --hex "22 F1 90" --backend uart --serial-port COMx
```

期望输出（JSON，可直接贴进验证记录）：

```json
{
  "request": "22 F1 90",
  "response": "62 F1 90 4C 4F 54 41 30 30 30 30 30 30 30 30 30 30 30 30 31",
  "response_bytes": 20,
  "response_ascii": "b..LOTA0000000000001",
  "backend": "USART1 bench"
}
```

`response_ascii` 把非打印字节显示成 `.`，所以 `62 F1 90` 显示为 `b..`，后面的 VIN 可读。
这个 `request` 动作是给台架用的：任意 UDS 请求都能发，不用改代码。同样的请求在 GUI 里
用左下角「自定义请求(hex)」也能发，并能看到逐字节 TX/RX。

手工核对一帧的字节布局（这是能在面试里画出来的东西）：

```
A5 5A | 01 | 08 | 00 00 07 E8 | 62 F1 90 4C ... | CRC16
同步   ver  dlc   id(大端)      data[dlc]         CRC-16/CCITT-FALSE
                  ↑ 覆盖 ver..最后一个数据字节，不含 A5 5A
```

### 3. 图形界面（演示用）

```powershell
C:\Users\li14\AppData\Local\Programs\Python\Python312\python.exe `
  D:\Project\Can-learning\ota-core\tools\uds_gui.py --backend uart --serial-port COMx
```

> 必须用 Python 3.12（3.13 没带 tkinter）。

## 已知问题（先知道，别当成自己接错线）

- `src/core/can_uart.c` 在**半帧**后会吃掉下一帧（实测 300 次只恢复 19 次）。
  补丁提案与证据在 `build-temp/can_uart_resync/`（`NOTES.md` + `proof.py`），
  合并前若遇到"偶尔丢一帧"，那是这个缺陷，不是接线问题。
- 模拟后端不受影响（PC 侧自己封帧），所以 GUI 在 `sim` 下一切正常。

## 记录模板（跑完往 `04-验证记录/` 里加一条）

```
日期/时间：
固件 sha256：16f94df0…（UART bench）
串口：COMx @115200，适配器型号：
info 输出：
request 22 F190 输出（含原始字节）：
是否遇到丢帧：
```
