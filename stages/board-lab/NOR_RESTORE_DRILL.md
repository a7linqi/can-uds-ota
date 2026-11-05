# NOR 恢复演练（nor_restore）— 首次真实擦除前的强制门槛

更新：2026-11-04。执行端（board-lab）产物，未修改 `ota-core/` 的 `src/`、`platform/`。

## 为什么必须先做这一步

板上外部 NOR 保存着出厂 FAT16 内容（图片 / 音乐 / 视频 / NES / 触摸校准），
全片 16 MiB 只做过一次两遍一致的全量 dump：

| 项 | 值 |
|---|---|
| 金标镜像 | `backups/nor-original/full/20261103_200408_779736/pass1/pass1.bin` |
| 大小 | 16777216 B |
| SHA256 | `929af96ac660590418cb74878033b742bcd31d9e7108933ce769548f40e305e1` |
| CRC32 | `0xA4188821` |

这份 dump 是全片内容的**唯一副本**。只要第一次真实擦除之后发现写坏、且没有可用的
回写工具，就再也回不到出厂状态。因此规定：

> **在 `nor_restore` 演练通过之前，不得对 NOR 执行任何擦除/写入。**
> 演练 = 用本工具把金标镜像写回 → 器件自校验整片 CRC 等于 `0xA4188821`。

## 交付物

| 文件 | 作用 |
|---|---|
| `firmware/w25q_bench.{h,c}` | 台架 W25Q 写驱动：WREN + 回读 WEL、4 KiB 扇区擦、256 B 页写、WIP 超时、流式 CRC |
| `firmware/nor_restore.{h,c}` | 协议 v3 状态机（无 HAL 依赖）：擦 → 写 → 逐页回读比对 → 提交 → 整片 CRC |
| `firmware/nor_restore_main.c` | STM32 主程序：BSP 初始化、JEDEC 双源核对、把 HAL SPI 接到驱动 |
| `firmware/nor_restore_sim.c` | 仅主机用：把同一份状态机导出成 DLL，供 Python 模拟器驱动 |
| `build_restore.py` | 交叉编译 `nor_restore_WRITE.bin`，绑冻结核心 + 本阶段输入哈希 |
| `host/nor_restore.py` | 主机工具：`sim`（默认）/ `--hardware`（SWD mailbox） |
| `tests/test_w25q_bench.c` | 驱动测试：字节级 SPI 从机模型（含 WREN/WEL/WIP/页边界） |
| `tests/test_nor_restore_c.c` | 状态机测试：先擦后写、块 CRC 错不碰 NOR、错误码、verify 只读 |
| `tests/test_nor_restore_host.py` | 端到端断电矩阵 + CLI/清单隔离 |
| `tests/run_restore_checks.py` | 聚合回归 |

## 协议 v3（SWD RAM mailbox，`0x4E4F5257` / v3）

与只读 dump 固件的 v2 用不同 magic + version，读写两种会话不可能互相误认。

40 B 头（10 × little-endian u32）+ 32 KiB 数据块，符号 `nor_restore_mailbox`：

| 偏移 | 字段 | 方向 | 含义 |
|---|---|---|---|
| 0 | magic | 设备 | `0x4E4F5257`（"NORW"） |
| 4 | version | 设备 | 3 |
| 8 | session | 主机 | 随机 nonce，非 0 |
| 12 | seq | 主机→设备回显 | 块号，从 1 严格递增 |
| 16 | offset | 主机 | 本块在镜像中的偏移 |
| 20 | length | 主机 | 本块字节数（≤ 32768） |
| 24 | status | 设备 | IDLE/BUSY/READY/BLOCK_OK/DONE/ERROR |
| 28 | error | 设备 | 错误码 |
| 32 | crc32 | 主机→设备回显 | 本块 CRC32；DONE 时 = 整片 CRC32 |
| 36 | ack | 主机写、设备清 | 命令或"提交第 N 块"，**永远最后写** |
| 40 | data | 主机 | 32 KiB 载荷 |

**ack 是门铃**：主机写非 0 值提交，设备消费后把它清回 0，然后才发布
READY / BLOCK_OK / DONE。这解决了一个真实竞态 —— 上一块的 `BLOCK_OK` 一直留在
`status` 里，主机若"看到 BLOCK_OK 就当成功"，会在设备根本没收到新块时误判通过
（本工程在开发中真的踩到过，故写进协议而非只写在主机里）。

命令值 ≥ `0x80000000`，合法 seq ≤ `0x7FFFFFFF`，因此单个字不会歧义。

三条命令：`0xA1000000` 整片恢复、`0xA2000000` 只读核验、`0xA3000000` 范围恢复。
范围恢复复用同一套块协议，差别只在会话边界：**主机在写命令字之前**把起点放进
`offset`、字节数放进 `length`，设备在 `begin()` 里先校验（必须扇区对齐、非 0、
且 `start + length <= total`）再把它们清零。校验不过就回 `ERROR/BAD_REQUEST`，
一个扇区都不擦。会话结束条件是 `offset >= end`，DONE 发布的是 `end` 与**该范围**
的 CRC32，不是整片 CRC。整片恢复等价于 `start=0, end=total`，行为完全不变。

为什么要它：整片 16 MiB 恢复一次 5236 s，而写一个块的实验只需要改 32 KiB。
有了范围恢复，一次"写坏→修回"从 87 分钟降到约 78 s（写 11 s + 全片扫描 66 s），
恢复动作才会被真的执行，而不是被"太贵了"逼成跳过。

## 设备侧行为（`nor_restore.c`）

每个块严格按此顺序，任何一步失败立即发布 `ERROR` + 错误码并停住：

1. **先验后动**：校验 `offset == 期望`、`0 < length ≤ 32768`、`offset+length ≤ total`、
   `CRC32(data) == crc32`。任何一项不过，**NOR 一个字节都不动**（`BLOCK_CRC` / `BAD_REQUEST`）。
2. `status = BUSY`（先撤掉可信状态，再碰芯片）。
3. 按 4 KiB 扇区擦除本块覆盖的所有扇区（每块 8 次）。
4. 按 256 B 页写入，每写一页立刻用 `0x03` 回读并与提交数据逐字节比对
   （`PROGRAM` / `VERIFY` 错误码分开）。
5. 回显 `seq/offset/length/crc32` → **清 ack** → `BLOCK_OK`。
6. 末块之后进入 `FINAL_CRC`：整片 16 MiB 用 `0x03` 读一遍算 CRC32，
   期间 `status` 保持 `BUSY`（此时发布 DONE 就是撒谎），完成后 `DONE` + `crc32=整片 CRC`。

错误码：`INIT 1` `JEDEC 2` `SPI 3` `BAD_REQUEST 4` `BLOCK_CRC 5` `ERASE 6` `PROGRAM 7` `VERIFY 8`。

固件在写之前做两次 JEDEC 读取（自己的驱动 + 参考 BSP 的 `ota_explorer_probe`），
两者都必须是 `0x00EF4018`，否则停在 `JEDEC` 错误 —— 几何是硬编码的 16 MiB，
换芯片必须停机而不是写一半。

## 为什么用"器件自校验整片 CRC"而不是再读一遍

SWD 读 16 MiB 的实测代价是 **3 小时**（`STM32_Programmer_CLI -u` 约 3.63 KB/s，
2048 块 × 2.2 s），这是本项目已经付过一次的账，不能再付。

器件侧用 SPI（5.25 MHz）把 16 MiB 读回来算 CRC，量级是几十秒，覆盖全片，
且校验的是**芯片里真实的内容**（不是主机内存里的镜像）。因此验收用：

- 每页写后立即回读比对（定位到页）；
- 末块后整片 CRC32（覆盖全片）；
- 需要独立证据时，再用 SWD 抽查若干块（每块约 2.6 s）。

## 主机工具

```powershell
# 纯模拟（默认）：编译真实的 C 状态机为 DLL，用带 W25Q 语义的芯片模型驱动
C:\Users\li14\AppData\Local\Programs\Python\Python312\python.exe host\nor_restore.py `
  --image D:\Project\Can\backups\nor-original\full\20261103_200408_779736\pass1\pass1.bin `
  --expect-sha256 929af96ac660590418cb74878033b742bcd31d9e7108933ce769548f40e305e1 `
  --expect-crc32 0xA4188821

# 掉电演练：在第 100 块编程阶段掉电，重启后再跑一次必须收敛
... --cut-at-block 100 --cut-phase program

# 只读核验（不写）：器件算整片 CRC 与金标比对
... --mode verify

# 硬件（需 ST-Link + 已烧 nor_restore_WRITE.bin）
... --hardware --mailbox-addr 0x200003B8

# 只重写一块（0 基 32 KiB 块号），随后复位做全片 CRC 扫描
... --block 0 --verify-full --hardware --mailbox-addr 0x200003B8

# 一次恢复多块
... --block 8 --block-count 4 --verify-full --hardware --mailbox-addr 0x200003B8
```

模拟器的三点设计（都不是"再写一遍 Python 逻辑"）：

1. **跑的是同一份 C**：TinyCC 把 `nor_restore.c` 编成 DLL，ctypes 驱动，
   芯片模型通过回调提供擦/写/读。协议或顺序改错，C 测试和模拟会一起失败。
2. **芯片模型带真实约束**：擦除→`0xFF`；写入只能清位（AND）；未擦先写会被计数；
   页/扇区对齐与越界会拒绝。模拟初始内容故意是**全 0x00**（不是 0xFF），
   所以"忘记擦除"不可能蒙混过关。
3. **掉电是真掉电**：触发点上的擦/写先做一半效果再让后续传输全部失败，
   然后丢弃整个设备上下文（等于 MCU 复位、SRAM 丢失），下一次从第 0 块重来。

`--hardware` 前会核对证据文件：构建记录 `status=ready_for_review`、本阶段输入哈希、
冻结核心哈希、mailbox 地址、以及三个产物的 sha256 全部匹配才允许连板子。
mailbox 地址由构建记录给出（`ctx` 结构一变就跟着变，2026-11-05 加了 `start`/`end`
两个字段后从 `0x200003B0` 变成 `0x200003B8`；写错会直接被闸门拒绝，不会打到错地址）。

## 已完成的验证（2026-11-04，**无硬件**）

| 检查 | 结果 |
|---|---|
| W25Q 驱动 C 测试（字节级从机模型） | 通过 |
| 状态机 C 测试（10 个场景） | 通过 |
| 主机自检（happy / 掉电 / verify） | 通过 |
| 断电矩阵：8 块 × {擦, 写, 回读} + 末次整片扫描掉电 | 25 个切点全部两次收敛、逐字节一致 |
| 16 MiB 金标模拟恢复 | 512 块 / 4096 扇区 / 65536 页，器件 CRC `0xA4188821` = 金标 CRC，`program_without_erase=0` |
| 还原固件交叉链接 | `nor_restore_WRITE.bin` 13636 B，sha256 `1f09cb0c…`，`0x08000000` 起，mailbox `0x200003B0` |
| 只读 dump 固件重建 | `nor_backup_READONLY.bin` 12288 B，sha256 `af69df8e…` **与重建前完全一致** |

上表是**上板前**的验证记录（`hardware_executed=false`），保留作对照。
2026-11-04 晚已在真机上跑通**只读核验**（见下一节）；**写入/擦除本身仍未执行**。
模拟里的"掉电"是模型行为，不等于真实掉电；真实掉电还涉及 NOR 内部擦除时序、
SPI 电平、以及 ST-Link 掉线，这些只有真正跑一次恢复才能验证。

## 硬件实测记录（2026-11-04 晚）

**只读核验已在真实板卡上通过**（第一步门槛已跨过）：

| 项 | 实测值 |
|---|---|
| 命令 | `nor_restore.py --image <金标 pass1.bin> --mode verify --hardware --mailbox-addr 0x200003B0` |
| 注意 | `--image` 是必填项（漏了直接 `ERROR: --image is required`，2026-11-04 核对源码确认）；完整参数见上面「主机工具」第一条命令 |
| 器件自算整片 CRC32 | **`0xA4188821`** = 金标 CRC ✅ |
| `crc_match` | `true` |
| 耗时 | **66.0 s**（含每 0.34 s 一次的 SWD 轮询打断） |
| 说明 | 这一步证明：还原固件能跑、SPI 能读全片、JEDEC 对、**金标镜像与芯片现状逐字节一致** |

同期实测的搬运成本（决定演练要跑多久）：

| 操作 | 实测 |
|---|---|
| 32 KiB `-w` 到 SRAM | **8.33 s**（两次一致）→ **3.9 KB/s** |
| `-w32` 单字写 | 0.13–0.15 s |
| `-u` 读 40 B 头 | 0.14 s |
| 整片 16 MiB 写（推算） | ≈ 72 min |
| 演练总耗时（推算，含器件侧 5–6 min 与轮询） | **≈ 85–90 min** |

**一个只有上板才会暴露的坑**：CubeProgrammer 2.23.0 的 `-w32` 写完会自己回读校验，
`-nv` 实测**并不能**抑制它；本固件一收到命令就把 `ack` 清零，回读值与原值不符，
CLI 报 `Failed to download data!`——**但那个字其实写进去了**。主机现在只在日志同时证明
「本地址本值已发出」与「错误正是回读不符」时容忍，其余异常照旧抛出。

**2026-11-05 补充**：只用「一次头部读 + 状态必须进 BUSY」来确认是不够的，
这正是 477/512 停住的根因，详见下一节。

## 握手竞态：477/512 的根因与修复（2026-11-05）

**结论**：不是 `ack` 被吞，也不是写入失败，而是**设备比主机的一次读还快**。

单块上板探针（`build-temp/handshake-probe/probe.py`，证据
`evidence/nor-restore/handshake-probe-20261105_081709_238678/`）：

| 时刻 | status | ack |
|---|---|---|
| 写 `ack=1` 后 +0.156 s | `BUSY` | 1 |
| +0.875 s | `BLOCK_OK` | 0 |

设备完成一块只要约 **0.8 s**；端到端 10 s/块里 8.9 s 是 SWD 传 32 KiB。
昨晚长跑末期一次读头要 0.875 s，那次读横跨整个 BUSY 窗口，回来时已是完成态，
而 `_confirm()` 只接受 `{BUSY, DONE}`，把「做完了」判成「没接受」。
另外 `23:20:36.439 → 23:20:36.450` 相隔 **11 ms**，0.877 s 是读头命令自身耗时、
不是等待间隔 —— 昨晚据此推出的「物理上不可能」是个误读。

主机侧三处修复（**固件与板上镜像零改动**）：

1. `_confirm()` 改成 10 s 超时循环 + 连续两次读一致，并把 `BLOCK_OK + ack==0` 纳入接受集；
2. `set_u32()` 的容错按真实日志收窄（CubeProgrammer 2.23.0 的 `-nv` 实测不抑制 `-w32` 回读）；
3. `finish()` 对 DONE 头读两次比对，拒绝混合快照。

验证：`--selftest` 三项全过；16 MiB 模拟恢复 **512 块 / 13.328 s**、器件 CRC `0xA4188821`、
`chip_matches_image=true`、`program_without_erase=0`；真机 3 块 **10.14 / 10.08 / 10.11 s**
全部 `BLOCK_OK`、echo 吻合。最终验收仍是器件自算整片 CRC 与金标比对，
握手层的假通过不可能伪装成 `passed`。

**512 块全程演练于 08:24 启动，结果待填**（本节不预写结论）。

## 范围恢复硬件 A/B（2026-11-05，实机）

目的不是"跑一遍没报错"，而是先证明芯片真的被改坏，再证明只改回去坏的那一块。
证据在 `evidence/nor-range/`，四份 manifest 依次是：

| 步骤 | 命令 | 结果 |
|---|---|---|
| 1 故意写坏块 0 | `--image tampered-block0.bin --block 0 --hardware` | 11.0 s，器件范围 CRC `0xBFBAD03B` = 篡改块 CRC |
| 2 全片扫描 | 同一文件 `--mode verify` | 器件整片 CRC `0x7660036E` = 篡改镜像 CRC，66.2 s |
| 3 恢复块 0 | 金标 `--block 0 --verify-full` | 10.95 s，范围 CRC `0x830246CC` = 金标块 0 CRC |
| 4 复位后全片扫描 | 同上（同一次运行内） | 整片 CRC `0xA4188821` = 金标，`full_chip_crc_match=true` |

第 2 步是关键：整片 CRC 与篡改镜像逐字节相等，说明"写坏"这件事真的发生在硅片上，
而不是只改了主机内存；同时其余 511 块仍是金标内容，因此第 4 步的整片 CRC 才能
只靠重写一块就回到 `0xA4188821`。

第 4 步为什么必须复位：范围会话走到 DONE 就停在那里，状态机不再接受新命令，
所以全片扫描是一次全新的启动上下文 —— 顺带也证明扫描读的是硅片而不是残留状态。

代价对比：整片恢复 5236 s，写一块 + 全片扫描 77 s，**67×**。此后每次实机写 NOR
都可以先备份块、写完再定向修回，不必为了做一次实验等 90 分钟。

## 上板演练步骤（硬件到位后）

前置：ST-Link 已识别；NOR 仍是出厂内容（EEPROM 快照应为 `40aff2e9…`）；
金标镜像 sha256 已核对。

1. 烧 `build/nor_restore_WRITE/nor_restore_WRITE.bin` 到 `0x08000000`（约 1 秒，不是全片读）。
2. `--mode verify` 先跑一次**只读**核验，确认器件算出的整片 CRC 等于 `0xA4188821`。
   这一步就把"金标镜像 == 板上现状"钉死了；不等就说明镜像或芯片不对，立即停止。
   （verify 会新建一个 evidence 目录并写自己的 `manifest.json`，不会覆盖其它记录；耗时约 66 s。）
3. `--hardware --mailbox-addr <构建记录里的地址>` 执行恢复。
4. 接受条件（全部满足）：
   - 每块回显 `seq/offset/length/crc32` 与提交一致；
   - 末状态 `DONE`、`offset == 0x1000000`、`error == 0`；
   - `DONE.crc32 == 0xA4188821`。
   
   一条命令核对上面全部条件（只读证据目录，不碰板子）：

   ```bat
   python build-temp\drill_accept.py
   ```

   默认取 `evidence/nor-restore/` 下最新一次运行，退出码 `0=通过 1=失败 2=仍在进行`。
   它同时校验 `manifest.json`（`status`/`crc_match`/`device_crc32` 与金标一致/512 块）
   和 `events.jsonl`（512 条事件的 `offset` 必须无缝铺满 0..16 MiB）。
   三种判定已用构造样本自测过：合格样本 → `PASSED`；改坏 CRC → `FAILED`（4 条问题）；
   删掉最后一块事件 → `FAILED`（覆盖字节数不足）。样本在 `build-temp/drill-accept-selftest/`。
5. 恢复现场（**必做，否则板子上留着的是还原固件**）：
   把 `build/nor_backup_READONLY/nor_backup_READONLY.bin` 烧回 `0x08000000`，
   烧录方式与第 1 步相同。之后板子恢复成"只读全片 dump"状态；
   要回到 UDS 台架就再烧 `explorer_ZG_uart_READONLY.bin`（40 680 B）。
   **顺序注意**：一旦重烧，`nor_restore_WRITE.bin` 就没了，
   第 4 步的 `--mode verify` 便无法再跑；所以要补验就在这一步之前做完。
6. 抽查：用 SWD 读 4 块（32 KiB）与金标对应区间 `cmp` 一致；如仍不放心，
   再决定是否付 3 小时做一次全片重读（**非必需**，器件 CRC 已覆盖全片）。
7. 记录：EEPROM 快照必须仍是 `40aff2e9…`（证明全程只动 NOR）。

## 成本（必须如实说明）

| 路径 | 代价 | 说明 |
|---|---|---|
| **单块（擦+写+回读+块 CRC）** | **10.5 s（实测，3 块样本）** | 32 KiB/块，3.06–3.15 KiB/s |
| 器件侧整片 CRC 核验 | **66 s（实测，3 次一致）** | 16 MiB 经 SPI 读回算 CRC32 |
| **演练总耗时** | **≈ 89 分钟（按实测 ETA）** | 512 块 × 10.5 s |
| 全片重读对照 | 3 小时（实测） | 已付过一次，不再重复 |

块大小与频率的两条实测边界：

- **块越大越划算**：8/16/32/64 KiB 实测 2.26 / 4.25 / 8.39 / 16.59 s，速率恒定在
  3.5–3.9 KiB/s，说明每次 CLI 调用的固定开销只有约 0.2 s，**瓶颈是 SWD 链路本身**，
  不是进程启动。这也是当初 NOR 全片 dump 只有 3.63 KB/s 的原因（同一块 ST-Link）。
- **频率上不去**：`freq=2000` 立刻 `failed to download`，`4000/8000` 直接
  `Unable to get core ID`。这块 ST-Link 只能在 1 MHz 稳定工作，想更快只能换调试器。

即"演练一次"仍是重读的三分之一，而且演练是**必要的保险**，重读只是重复劳动。
32 KiB 块宽就是为了把 SWD 调用次数从 2048×N 降到 512×N；若某天 `-w` 不接受
32 KiB 到 SRAM，把 `NR_BLOCK_SIZE` 改成 8192 重编即可（调用次数 ×4，但 `-w` 单次也更短，
总吞吐受 SWD 带宽限制，未必更慢）。

## 不成立的说法（避免自欺）

- ❌ "模拟通过 = 硬件验证通过"。模拟验证的是**逻辑与顺序**，不验证电气与真实擦除时序。
- ❌ "写了没报错 = 恢复成功"。接受条件只看器件算出的整片 CRC，不看"没有异常"。
- ❌ "掉电后能续传"。**故意不支持续传**：每次从第 0 块重来，靠"先擦后写 + 幂等"
  收敛，比信任半途状态更可靠。
- ❌ "这就是 OTA 升级"。这是台架恢复工具，不走 UDS、不碰 `src/core`、不写 EEPROM 元数据。
