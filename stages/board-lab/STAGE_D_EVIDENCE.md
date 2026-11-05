<!-- 证据原件在 stages/board-lab/evidence/stage-d/20261105/，该目录被 .gitignore 排除（体积大）。
     这里保留可复核的清单与结论；二进制快照只在本机。 -->

# 阶段 D 证据：完整升级链在实机上走通

日期：2026-11-05
板子：STM32F407ZGT6 + W25Q128（16 MiB）+ AT24C02，UART 台架 115200（COM9）
固件：`build-temp/stage-d/loader/loader.bin`（46836 B，sha256 `6070b7190d76047e…`）
包：`build-temp/stage-d/app_v1.img2` / `app_v2.img2`（各 20380 B，payload 20284 B）

## 结论

1. 设备通过产品 UDS 路径收下**合法 Ed25519 签名包**，`0x37` 验签通过，`0x31` 确认 READY。
2. `0x11` 复位后 loader 自行擦 960 KiB 内部 Flash、安装、进 TRIAL、跳转到 0x08010000。
3. App 运行 500 ms 后确认健康，元数据转 `state=IDLE`、`active_version=1`、
   `active_size=20284`、`active_crc=0x32EC9BA9`、`min_version=1`。
4. 从 0x08010000 读回 20284 字节，SHA256 `654eb54cd009d24dc136b5424b5a7e0c1a306195c9de4e8675d939eadb960ab1`，
   与构建产物逐字节相同。
5. 做了两遍（version 1 与 version 2），第二遍的安装阶段耗时 8.13 s。

## 文件

| 文件 | 内容 |
|---|---|
| `internal-64k-before-loader.bin` | 烧 loader 前内部 Flash 前 64 KiB 备份（65536 B） |
| `loader-boot.bin` | loader 首次启动的串口输出（520 B） |
| `reset-console.bin` / `.txt` | 第一遍：`0x11` 之后 1043 字节原始输出，含安装与跳转 |
| `stage-d.json`（上一级目录） | 第一遍每一步的请求/响应/耗时 |
| `installed-app.bin` | 第一遍后从 0x08010000 读回的 20284 B |
| `reset-idle-boot.txt` | 第一遍后再复位：0.06 s 选择、0.14 s App，无重装 |
| `second-upgrade/stage-d.json` | 第二遍（v2）全部步骤与串口时间线 |
| `second-upgrade/reset-console.txt` | 第二遍复位后的输出 |
| `final-boot-after-v2.txt` | v2 之后复位：0.08 s 选择、0.14 s App |
| `eeprom-dump.txt` / `eeprom-snapshot.bin` | 元数据仍是 TRIAL(seq 9)+IDLE(seq 10) 时的原始 256 B |
| `eeprom-write-probe.txt` | 写入前后对照：seq 12→13，`eeprom_crc32` 不变 |
| `eeprom-after-save.bin` / `eeprom-seq13-14.bin` | 两次快照，用于证明整块 CRC 是常数 |
| `eeprom-bus-recover.txt` | 一上电就读成功的那次（未触发恢复分支） |
| `metadata-wipe.txt` | 清零两份记录，模拟空片 |
| `eeprom-read-compare.txt` | 256 B 一次读失败的那次（`EEPROM read failed`） |

## 关键数字

| 项 | 值 |
|---|---|
| `0x34` 擦 1 MiB 外部槽 | 13.78 s |
| `0x36` × 40 帧（510 B） | 每帧 0.31–0.34 s |
| `0x37` 验签 20380 B | 0.22 s |
| `0x11` 后选择目标 | 0.06 s |
| 擦 960 KiB 内部 + 装 20284 B | 8.13 s |
| 复位到 App 首行 | 8.27 s |
| 已确认后复位到 App 首行 | 0.14 s |

## 不成立的说法

- **"`eeprom_crc32` 变了说明 EEPROM 内容变了"**：该值是结构指纹
  （`CRC32(m ‖ CRC32(m))` 为常数），四种不同元数据状态都得到 `0x316AB2D3`。
  2026-11-02 记的"`0x29058C73` 与上次一致"不构成内容一致的证据。
- **"读到了陈旧快照"**：曾经据此把 256 B 一次读改成分块读，实测分块同样会失败，已回退。
- **"安装后能自动发起下一次升级"**：当时确实做不到——元数据回到 IDLE 后每次复位都直接跳 App，
  App 没有 UDS，第二次升级靠清零元数据模拟空片才做成。**该结论已于同日关闭**，见下一节。
- **"App 收到 `34` 回 `7F 34 22` 说明门控坏了"**：App 编译时 `OTA_ENABLE_FLASHING=0`，
  拒绝写服务是设计行为；把它当成故障，导致上一轮的排查方向整段跑偏（详见
  `journal/entries/2026-11-05-gate-is-not-a-fuse.md`）。

## 阶段 D 收尾：两个自检缺陷的修复与复验（2026-11-05 晚）

阶段 D 完成后自检发现两处真实缺陷，都已修复并在实机上复验：

| 缺陷 | 现象 | 修法 | 实机证据 |
|---|---|---|---|
| 没有 App→loader 入口 | IDLE 后每次复位直接跳 App，第二次升级只能靠清元数据 | `10 02` 在 App 侧把一次性 `boot_request` 写入 64 B 元数据（复用 `reserved[3]`），loader 启动时消费 | EEPROM seq 42→43（`boot_request=1`）；loader 打印 `loader: stop requested by the application` 并停住；随后从 loader 装 v5 成功 |
| `active_size!=0` 但内部镜像损坏 → READY 卡死 | 合法新包永远装不进去，反复复位停在 loader | 校验改三态（VALID/CORRUPT/UNREADABLE），CORRUPT 时清 active 四字段（含 `min_version`）后照常安装，UNREADABLE 仍停 | 破坏 `0x08011000` 一个字（`4F`→`4E`，CRC `0x32EC9BA9`→`0x137FF3F6`），loader 打印 `loader: staying in the loader` 不再卡死；随后装 v3 并跳转 |

同一轮还改了状态 DID：0x0200 的记录从 40 字节扩到 44 字节，末尾 4 字节是调度遥测
（`programming_allowed` / `job_error` / `pending_in_flight` / `role|deadline_misses`）。
目的是让"App 按构建拒绝"与"loader 门控故障"在总线上可区分——两者对应同一个 NRC `0x22`。

当前固件（本轮重建）：loader 47308 B（sha256 `73c1a08e…`）、app 44280 B（sha256 `b704f99d…`）、
签名包 v7 44376 B（payload 44280 B）。

~~**本轮没做的**：板子未连接（只有蓝牙串口、无 ST-Link），所以 44 字节记录、`diag[3]` 的
`role` 位在实物上尚未读数。~~

**更正（2026-11-05 深夜，已上板）**：板子一直连着，是枚举时只筛了 `Class -eq USB`，
而 `STM32 STLink` 挂在 `USBDevice` 类下。实物读数已完成，见下一节。同一节里还有两处结论被推翻：

1. 第 4 个遥测字节里的 `pending_in_flight` 在实物上**恒为 1**（读它的请求本身就是延迟服务），
   已换成 `requests_dropped`。
2. `31 01 00 01` **不是**门控探针：它校验的是"有待装包"，没包时对任何门控状态都回 `0x22`。
   可用的非破坏性探针是 `36 01 00`（门控开 → `0x24`，门控关 → `0x22`）。

## 实机复验（2026-11-05 深夜，板子在线）

实物：ST-Link（`USBDevice / STM32 STLink`，FW V2J47S7）+ CH340 在 **COM9**；
`STM32_Programmer_CLI -c port=SWD mode=UR` 读到 `Device ID 0x413`、`NVM 1 MBytes`、`Rev 2.0`、3.24 V。

固件（本轮重建并烧录）：loader 47324 B（sha256 `d61020cf…`）、app 44288 B（sha256 `bed181e9…`）。

| 项 | 命令 | 结果 |
|---|---|---|
| 元数据过时不再乱跳 | 烧新 app 后复位 | `loader: selecting boot target` → `loader: staying in the loader`，状态仍 IDLE，未清记录 |
| 状态 DID 44 字节 | `uds_gate_probe.py` | `role=loader`、`programming_allowed=1`、`requests_dropped=0` |
| 门控遥测与总线一致 | `--probe-gate` | `36 01 00` → NRC `0x24`（门控开），`verdict.consistent=true` |
| App 侧对照 | 同上 | `role=application`、`allowed=0`、`36 01 00` → NRC `0x22` |
| App→loader | `uds_app_handoff.py` | `loader: stop requested by the application`，不跳转 |
| 首次安装（v8） | `uds_stage_d.py` | `37→77`、`31→71 01 00 01 00`、`handing off to the app` |
| 连续升级（v9/v10/v11） | `uds_stage_d.py --handoff` | 三次全部成功，`active_version` 9/10/11 |
| 读回比对（v10） | ST-Link `-u 0x08010000 0xAD00` | `sha256 bed181e9ea2b4089`、`crc32 0x5F966357`、44288 B，与构建产物**逐字节相同** |

### 这轮新发现的硬件事实：关串口会复位板子

CH340 的 DTR/RTS 接了 NRST。实测：读状态得 seq 59 / `role=application` → 发 `10 02` → **直接关端口**
（不发 `11 01`）→ 等 3 s → 再读：seq **61**（+2 = 写入 + 消费）、`role=loader`。
没有任何复位指令，却多出一次 loader 的消费写入，所以复位发生在关端口那一刻。

后果与对策：跨进程"先留状态、再跑下一个脚本"不成立。`uds_stage_d.py` 新增 `--handoff`，
在同一串口会话内完成 `10 02 → 11 01 → 抓横幅 → 10 02 → 27 → 34/36/37/31`。

### 时序（实测，同一 loader 二进制）

| 步骤 | v8（会话起点=loader） | v9/v10（`--handoff`，超时 bug） | v11（超时修复后） |
|---|---|---|---|
| `34` 擦除 | 13.82 s | 14.26 / 14.24 s | 13.90 s |
| `36` 每帧 | 0.05–0.34 s | 0.43–2.47 s | 0.06–0.34 s |
| `37` 验签 | 0.34 s | 0.53 s | 0.53 s |
| `31` 安装 | 0.29 s | 0.48 s | 0.48 s |

v9/v10 的 7 倍慢是 `--handoff` 里把 `ser.timeout` 临时改成 0.2 s 后忘了恢复（传输层默认 0.01 s），
不是固件问题；修复后回到 0.06–0.34 s。
