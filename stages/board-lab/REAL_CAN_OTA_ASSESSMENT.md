# 真实 CAN 上跑完整 OTA 的工作量评估（2026-11-07）

**结论：不需要写新的 C 代码，主要是构建配置 + PC 驱动换传输层 + 联调。估计半天到一天。**
对比早前评估的 FreeRTOS 移植（2–3 天开发 + 1–2 周同证据回归），这是投入产出比最高的一跳。

## 1. 已经具备的条件（本轮实机实证）

| 项 | 状态 | 证据 |
|---|---|---|
| 真实 CAN 物理层 / ISO-TP / UDS 0x22 | 已通过 | `evidence/can/uds_readonly_acceptance.json`、`uds_multiframe_evidence.json`（VIN 20 B 多帧） |
| PC 侧真实 CAN 传输层 | **现成** | `ota-core/tools/pc/transport.py:243` `PythonCanTransport(interface, channel, bitrate)`，显式接口、滤波 0x7E8 |
| ISO-TP 多帧发送与流控 | **现成** | `tools/pc/isotp_client.py`：`send()` 支持首帧/连续帧、`_flow()` 解析 BS/STmin，`max_payload=2048` |
| 完整下载/安装客户端 | **现成** | `tools/pc/uds_client.py` `flash()`：`0x34` → 循环 `0x36` → `0x37` → `0x31` → `0x11`，含 `UnknownCommit` 保护 |
| Seed/Key 解锁在真实 CAN 上 | **本轮实证** | `0x27 01` → `67 01 5E E9 79 B1`；`0x27 02 44 A5 84 36` → `67 02` |
| 11 字节 ISO-TP 多帧请求 | **本轮实证** | `0x34 00 44 08 01 00 00 00 00 AD 60` 经真实 CAN 发出并收到 `7F 34 22` |
| 可写固件构建变体 | **现成** | `ota-core/build-temp/build_stage_d.py` 的 `loader` / `app`，宏见下 |
| 签名包工具 | 现成 | `tools/ed25519.py`；历史签名包 v8–v11（各 44384 B） |
| NOR/EEPROM、安全启动、TRIAL 回滚、三种切点断电 | 此前 UART 台架实机验证 | `stages/board-lab/POWER_CUT_EVIDENCE.md` 等 |

本轮门控实证落在 `evidence/can/nrc_72_gate_evidence.json`。

## 2. 需要改动的具体清单

### A. 固件（只改构建宏，不改源码）

`build-temp/build_stage_d.py` 的 `loader` 变体：

```
OTA_ENABLE_FLASHING=1, OTA_ENABLE_BOOT_HANDOFF=1, OTA_ENABLE_WATCHDOG=0,
OTA_EXPLORER_UPDATE_CAPABLE=1, OTA_EXPLORER_STORAGE_TRUSTED=1,
OTA_EXPLORER_HANDOFF_CAPABLE=1, OTA_EXPLORER_BOOT_ROLE=1
```

`app` 变体：`FLASHING=0 / BOOT_HANDOFF=0 / UPDATE_CAPABLE=0 / STORAGE_TRUSTED=1 / BOOT_ROLE=2`。

**关键前置**：`build_stage_d.py` 的 `target_sources()` 从 `mdk/explorer_readonly.uvprojx` 继承 defines。当前工程是 `--uart` 版（`OTA_DIAG_UART=1`），直接跑会生成 **UART 诊断** 固件。必须先：

```powershell
python tools/generate_explorer_mdk.py --dfp <DFP> --cmsis <CMSIS>   # 不带 --uart，生成 diagnostics target
python build-temp/build_stage_d.py --only loader app
```

`OTA_DIAG_UART` 不在 `build_stage_d.py` 的 `OWNED` 列表里，会继承工程的 `0` → 得到 CAN 诊断 + 可写门控的固件。

然后烧 `loader` 到 `0x08000000`、初始 `app` 到 `0x08010000`，建立基线。注意**烧完要重新验证 CAN 链路**（新固件的 `0x10 02` 会打开 `programming_allowed`）。

### B. PC 侧驱动（约 10–20 行）

`stages/board-lab/host/uds_stage_d.py` 目前用 `SerialCanTransport`（UART 上的 CAN 帧封装）。两个选择：

- **推荐**：新写一个小驱动，直接复用现成库：
  ```python
  from pc.transport import PythonCanTransport
  from pc.isotp_client import IsoTpClient
  from pc.uds_client import UdsClient
  t = PythonCanTransport("pcan", "PCAN_USBBUS1", 500000)
  client = UdsClient(IsoTpClient(t), logger, total_timeout=300)
  client.programming()          # 10 02
  # 27 01 → seed；27 02 → demo_key(seed)
  client.flash(package, flash_kib=1024)
  ```
- 或把 `uds_stage_d.py` 的 transport 构造替换掉，保留它现成的 `ask()` 记录与 `0x11` 后抓串口横幅的逻辑。

### C. 必须补的一件事：`3E 00` 保活

`0x10 02` 正响应 `50 02 00 32 01 F4` → P2 = 50 ms、**P2\* / S3 = 5000 ms**。下载预计 20–40 s，不发 `3E 00` 会同时清掉会话与解锁，`0x34` 回 `0x22`（此前 UART 台架踩过）。要么在下载循环里定期发 `3E 00`，要么把会话超时配长。

### D. 签名包

用 `tools/ed25519.py` 生成与新 `app.bin` 版本匹配的包（32 B 头 + payload + 64 B 签名，flags bit0），或复用历史包但必须核对 version / CRC / 尺寸一致。`OTA_REQUIRE_SIGNATURE=1` 会强制验签。

### E. 证据

events/results/summary、内部 Flash 读回 SHA256 与构建产物逐字节比对、元数据 seq / 状态机终态 `IDLE`。断电恢复可另做（UART 台架三种切点已验证，真实 CAN 上是新证据但不是必须）。

## 3. 时序估算（外推，未在真实 CAN 实测）

UART 台架历史实测：`0x34` 擦 1 MiB 槽 **13.8–14.3 s**；89 个 `0x36` 各 **0.05–0.34 s**；`0x37` **0.53 s**；`0x31` **0.48 s**；安装到跳转 **8.13 s**。

真实 CAN：每个 `0x36` 约 512 B → ISO-TP 约 **74 个 CAN 帧** + 每 8 帧一个流控帧；500 kbit/s 下一帧约 130 µs，纯传输约 **1–3 s**，瓶颈是 NOR 页写入与流控往返。估算下载 **20–40 s**、安装 **约 8 s**，合计 **30–50 s**。

**这是按 UART 台架实测外推的估算，未在真实 CAN 上测量过，不要写成实测值。**

## 4. 风险与未验证项

1. **S3 超时**（见 C），最容易踩。
2. **NOR 擦写阻塞 > P2**：`0x34` 擦 1 MiB 可能十几秒，必须靠 `0x78` ResponsePending / 延迟服务；`stm32_diag.c` 的 `job_service` 有 1000 ms 内回服务的约束，需核对真实 CAN 下的响应节奏。
3. **块大小约束**：`UDS_MAX_DOWNLOAD_BLOCK` 与 `IsoTpClient(max_payload=2048)`，需确认 `0x34` 协商的 `maxNumberOfBlockLength` 与 ISO-TP 能承载的一致。
4. **板子当前 App 区是空的**：先建立 baseline loader+app，否则第一次 `0x11` 后无处可跳。
5. **真实 CAN 上的断电中断恢复未做**（UART 台架做过空闲/下载中/安装中三种切点）。
6. 改变构建宏后需重跑目标构建校验；**源码未改，主机模型回归不必重跑**。
7. 长时间稳定性、总线负载/错误帧统计仍未测。

## 5. 工作量与建议

| 步骤 | 估计 |
|---|---|
| 生成 diagnostics 工程 + 构建 loader/app 可写变体 + 烧写基线 | ~30 min |
| 生成/核对签名包 | ~15 min |
| PC 驱动换传输层 + `3E 00` 保活 + 联调 | 2–4 h |
| 完整 OTA 跑通 + 落证据 | 1–2 h |
| （可选）真实 CAN 断电恢复 | 1–2 h |
| **合计** | **半天到一天** |

**建议优先做**：它把「真实 CAN 只读诊断」直接升级为「真实 CAN 完整 OTA」，是简历里最有分量的一句；优先级高于 OTP 单调版本计数器与 WRP/RDP2。

## 6. 边界

本文件是**评估**，不是已完成的验证。真实 CAN 上目前只跑通只读诊断（含 ISO-TP 多帧、Seed/Key、门控遥测），完整 `0x34…0x37` → 安装链路仍只在 UART 台架实机验证过。**不得据此写成「真实 CAN OTA 已通过」。**
