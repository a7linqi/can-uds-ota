# 真实 CAN 断电恢复验收 Runbook（2026-11-09 拟定，待执行）

> 前置：真实 CAN 完整 OTA 已于 2026-11-09 22:44 通过（`REAL_CAN_OTA_RUNBOOK.md` 第 12 节、
> `evidence/can/real_can_ota_summary.json`）。板子当前 `IDLE / application / v24`。
> 配套历史脚本：`host/power_cut_upgrade.py`、`host/power_cut_recover.py`（**UART 后端**，需改造）。
> **状态：方案与判据已定，实验未执行。**

## 1. 先纠正三处会带偏实验的前提

### 1.1 14.197 s 擦的不是内部 App 区

`src/core/uds.c` 的 `handle_request_download()`：

```c
s->boot->external.erase(s->boot->external.user, DOWNLOAD_OFFSET, EXT_IMAGE_SLOT_SIZE);
```

擦的是**外部 SPI NOR 的 1 MiB 下载槽**（`EXT_IMAGE_SLOT_SIZE=0x100000`），
`EXT_ERASE_SIZE=4096` → **256 个 4 KiB 扇区**，实测 14.197 s（约 55 ms/扇区）。
内部 960 KiB App 区的擦除发生在 `0x11` 复位后的 `boot_select()` 安装阶段（历史 UART 台架
实测约 8.13 s），**不在这 18.552 s 内**。

→ 「按需擦除」这个优化方向成立，但对象是**外部 NOR 下载槽**：44920 B 只需 11 个扇区，
理论 14.197 s → 约 0.6 s。改动点：`handle_request_download` 里把
`EXT_IMAGE_SLOT_SIZE` 换成按 `size` 向上取整到 4 KiB 的扇区数；必须保留
「先把 `UPDATE_DOWNLOADING` 落盘再擦」的顺序（`uds.c` 现有注释明确写了）。
**注意**：改完要重跑主机回归与断电模型，并用真实 NOR 复核跨页写入（历史上踩过
`0x34` 声明 512 B 块 vs NOR 256 B 页 + `0x02` 页内回卷的坑）。

### 1.2 IWDG 32.341 s 对本次构建不成立

| 判据 | 实测/源码 |
|---|---|
| 本次 loader 变体 | `build-temp/build_stage_d_can.py` 显式 `OTA_ENABLE_WATCHDOG=0`；`ota_stm32.h:17` 与 `explorer_reference.h:88` 默认也都是 0 |
| 32.341 s 的来源 | 专门 opt-in 的 `*_wd` 实验变体（`OTA_ENABLE_WATCHDOG=1` + `WATCHDOG_CAPABLE=1`）测的，**产品/本轮路径没开** |
| 擦除循环喂狗 | `platform/stm32f407/src/stm32_spi_flash.c` 的 `nor_erase()` 每扇区前后都调 `ota_stm32_storage_service(p)`，该函数在 `OTA_ENABLE_WATCHDOG` 下会调 `ota_firmware_watchdog_service()` → **设计上已有喂狗点** |

所以「App 超过约 200 KB 就撞 IWDG」「刷写流程没喂狗会中途复位」都不成立。
真实容量上限由 `include/ota/flash_layout.h` 决定：`IMAGE_MAX_PAYLOAD = APP_SIZE - 96 B`
≈ **960 KiB**，并受 1 MiB NOR 下载槽约束；不是 200 KB。

**但要保留那份评估的警觉**：「这次没出事是因为包小，不是因为设计有余量」这句话作为
工程态度是对的——14.197 s 的固定全槽擦除确实是浪费，且 `0x34` 期间占用 14 s 会让
整条链路对会话/总线扰动更敏感。**P1 仍值得做，理由换成「耗时与 NOR 寿命」，不是 IWDG。**

### 1.3 真实 CAN 与 UART 台架的断电语义不同

UART 台架是点对点、断电后主机与 ECU 一起重启；真实 CAN 上断电时，适配器仍在总线上，
恢复瞬间的错误帧、bus-off/自动恢复、以及「上位机通道还开着」都是新变量。
历史结论**不能自动外推**，必须单独留证据（这正是本项目一直守的边界）。

## 2. 断电注入：三种方式，验证三种不同故障

| 方式 | 故障语义 | 检验什么 | ECU 真掉电 | 可互换吗 |
|---|---|---|---|---|
| **A. 切断整板全部供电**（切 5 V 总输入 + 拔 ST-Link + 拔 USB_232） | **电源中断** | EEPROM 双副本元数据、NOR 半写、内部 Flash 半擦、TRIAL 自愈、冷启动状态机 | ✅ | 否，P0 主实验 |
| **B. 只断 CANH/CANL**（板子继续供电） | **传输链路中断** | ISO-TP 多帧流控超时、S3 会话超时、`UPDATE_DOWNLOADING` 半事务残留、重连后能否用新 `0x34` 重启事务 | ❌ | 否，独立一轮 P0-b |
| **C. 只断主 5 V 但留 ST-Link** | **假断电** | 无效实验 | ❌/部分 | 见下方警告 |

> ⚠️ **C 是今晚刚踩过的坑**：ST-Link 会经 SWD 寄生供电 **2.87 V**，此时 MCU 仍可被
> SWD 读写、Flash 甚至能擦写，但 CAN 收发器缺 5 V 不工作。只断主电源会得到
> 「板子没死透」的假断电，实验结论全废。**做 A 必须把 ST-Link 一起拔掉**，
> 并用「整条 USB 消失 / SWD 探针失联」确认。

**推荐顺序**：先 A（项目靶心，与 UART 台架三种切点同类可对照），再 B（真实 CAN 特有）。
两轮各自单独留证据，不要混在一次实验里。

### 断点窗口（基于 2026-11-09 实测时序，CAN 上安装耗时尚未实测）

| 切点 | 触发判据 | 窗口 | 历史对照 |
|---|---|---|---|
| 空闲 | `IDLE` 状态直接断 | 任意 | UART 台架已验：三路全拔 16.5 s，恢复后逐字段相同 |
| 下载中 | `0x34` 发出后即进入 14.197 s 擦除窗口；或按 `0x36` 块计数（89 块共 3.757 s，约 42 ms/块） | 擦除窗口最宽、最易命中 | UART 台架在 9690/44920 B（19/89 帧）时拔电约 50 s |
| 安装中 | `0x11` 复位后横幅出现 `loader: selecting boot target` | 约 8 s（**UART 台架实测 8.13 s，CAN 上未实测**） | UART 台架第 3~4 s 拔最有效 |

### 恢复语义（期望结果，用来判 pass/fail）

- **下载中断**：旧固件完好、状态停在 `DOWNLOADING` 半事务、seq 仅小幅前进；同板立刻能
  用新 `0x34` 重启并完成一次全新升级（UART 台架 v15 → v17 已验）。
- **安装中断**：无人干预自己重做安装，终态 `IDLE/application/新版本`，内部 Flash
  与构建产物逐字节相同、凭证签名与包一致（UART 台架 v18 → v19 已验）。

## 3. 判据与取证（硬性要求）

1. **断电瞬间证据**：整条 USB 消失、SWD 探针 `No debug probe detected`、
   电压探针读数塌陷。若有条件，**用独立供电的电压记录器补一次 0 V 实测**
   ——历史一直缺这一项（塌陷到最后 2.87 V 探针自己就失联了）。
2. **恢复证据收两轮**：事件瞬间 + 稳定态。第一轮的 0 字节横幅 / `status` 超时 /
   SWD `Data read failed` 往往只是板子在忙自愈，不能判为失败。
3. **终态核对**：`state=IDLE`、`active_version`、内部 Flash 读回 sha256、
   96 B 凭证头与签名、`metadata_sequence`、`job_error` / `requests_dropped`。
4. **NRC ≠ 断电**：收到 NRC 说明板子还活着；只有传输层异常才是链路断了。
5. **单独留真实 CAN 证据**：`evidence/can/` 下 `events/results/summary` 三件套，
   不覆盖 UART 台架的历史证据。

## 4. 已知的坑（历史实录，别重犯）

- **拔电信号必须发到操作者能看到的通道（聊天）**；只打后台终端等于没打。
- **关串口会复位板子**：CH340 的 DTR/RTS 接 NRST。所以「先跑脚本留状态、再跑下一个
  脚本」不成立；驱动与取证要么同一会话内完成，要么用独立取证脚本。
- pyserial 在设备消失后连设 `timeout` 都会抛 `PermissionError`，断电处理路径必须全
  `try/except`。
- **不要为了等拔电而暂停请求**：S3=5000 ms，空闲超过 5 s 会同时清会话与解锁；
  OTA 下载过程每个请求都会刷新 `last_request_ms`（`uds.c:595/626`），但脚本若
  停下来倒计时等人工操作，会话会掉。信号要按帧/块计数实时发。
- 长倒计时别靠 `3E 00` 保活来「续命」——它改变的是会话状态，不是断电语义。
- `can_uart_encode` 是旧格式；真实线格式用 `IsoTpClient.send()`。
- 适配器拨码保持 `120R`，P5 跳帽、CANH/CANL/GND 接线不要动。

## 5. 需要改造的脚本（开工第一步）

现有 `host/power_cut_upgrade.py`（468 行）与 `host/power_cut_recover.py`（216 行）
都用 `SerialCanTransport`（UART 上封装 CAN 帧）。真实 CAN 版需要：

1. 诊断通道换成 `PythonCanTransport('pcan','PCAN_USBBUS1',500000)` + `IsoTpClient`；
2. **另开一个独立的 `pyserial` 句柄只读 COM3 文本横幅**——CAN 诊断构建不占用
   USART1 二进制传输，横幅可读（2026-11-09 已实测），但必须 `dtr=False rts=False`；
3. 断电/恢复全程包 `try/except`，驱动脚本崩了也能靠独立取证脚本收回证据；
4. `--cut-phase download|install` 的窗口判据从 UART 帧计数改为 `0x36` 块计数 /
   `loader: selecting boot target` 横幅；
5. 信号输出通道改为聊天可见（脚本打印 + 我在会话里同步喊 `PULL NOW`）。

## 6. 边界

- 本文件是**方案与验收清单**，不是已完成的验证。真实 CAN 断电恢复仍**未验证**。
- 三种切点目前只在 UART 台架实机验证过；`0 V` 电压实测、CAN 上安装耗时、
  总线负载/错误帧统计均未测。
- OTP 单调版本计数器、WRP/RDP2 按既定边界**不动**。
- 断电实验后若板子进入异常状态，用 `diagnostics_READONLY_backup.bin`
  （sha256 `8a383388…`）经 ST-Link 重烧即可回退。
