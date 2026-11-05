# RDP 才是信任锚：把 SWD 关掉之后，Bootloader 不验签这件事才成立

目标：给"Bootloader 自身不验签"这个已知边界一个**可执行的答案**。
结论先说：自校验没有意义（能改 Bootloader 的人也能改校验），真正起作用的是
**硬件读保护**。这份文档记录 RDP Level 1 在这块板上的实测行为与恢复过程。

## 一、威胁模型

| 攻击者能力 | 现有防护 | 缺口 |
|---|---|---|
| 能发 UDS（总线侧） | 包签名 + 版本策略 + 凭证，全部验过 | 无 |
| 能写外部 NOR / EEPROM（拆焊或飞线） | 凭证签名绑死镜像；元数据双副本带 CRC/seq | 可重放旧包（降级），除非有防回滚计数器 |
| 能连 SWD（调试口） | **RDP Level 1** | 若 RDP=0，可直接读出/改写 Bootloader，任何软件校验都是装饰 |

所以"Bootloader 自身不验签"的正确补法不是加一段自校验代码，而是让**别人读不到、也写不进**它。

## 二、实测（2026-11-05 晚，同一块 STM32F407ZGT6）

| 步骤 | 命令 | 结果 |
|---|---|---|
| 基线（RDP=0xAA） | `-u 0x08000000 0x400 baseline-loader.bin` | **Data read successfully**，1024 B，sha256 `7b1e4f0c…`，sp=0x20020000 / reset=0x0800A969（= loader_wd） |
| 上锁 | `-ob RDP=1` | `Option Bytes successfully programmed`（0.803 s），**没有 mass erase 记录** |
| 确认等级 | `-ob displ` | `RDP : 0x1 (Level 1, read protection of memories)` |
| 再读同一段 | `-u 0x08000000 0x400 rdp1-loader.bin` | **Error: Data read failed**，且**没有生成文件** |

即：同一个读命令，RDP0 下能完整取出固件，RDP1 下一个字节也拿不到。

## 三、一个必须写下来的坑：RDP1 + 调试器 = 板子"看起来死了"

上锁后串口立刻静默：无横幅、`22 02 00` 超时、`-hardRst` 和 `-run` 都无效。
一度怀疑"写选项字节时把 Flash 擦了"。

判别过程：

1. 拔掉 ST-Link USB（保留另外两路供电）→ **仍然静默**。
   原因：调试器把内核 halt 住之后，探针消失并不会清 `C_HALT`，引脚复位也没清掉。
2. **三路供电全拔、等 5 秒、全部插回** → 板子**完整启动**：
   `loader: selecting boot target` → `handing off to the app at 08010000` →
   `app at 08010000: awaiting real health checks`（1043 B 横幅，EEPROM/NOR 自检全过）；
   状态 DID `IDLE / application / v20 / 45304 B / seq 246`，VIN 正常应答。

所以：**RDP1 不影响固件运行，只挡调试访问**；而"接上调试器就静默"是测量陷阱，
不是固件故障。这条对现场排障很重要——带 RDP1 的板子接上调试器会像砖头。

## 四、恢复（RDP1 是单向门，只能整片擦除）

| 步骤 | 命令/动作 | 结果 |
|---|---|---|
| 解锁 | `-rdu` | 16.265 s，`Memory Read Protection disabled successfully`，RDP 回到 `0xAA` |
| 擦除确认 | `-u 0x08000000 0x100 after-unlock.bin` | 读成功，**256 字节全 0xFF**（整片被擦） |
| 重烧 loader | `-w loader_wd.bin 0x08000000 -v -rst` | 启动横幅正常，loader 正确报告 `installed app failed its CRC or vectors` 并停在诊断 |
| 重装 App | `uds_stage_d.py --image app_v23_wd.img2` | `34` 14.32 s → 90 帧 → `37 → 77` → `31 → 71` → 安装跳转 |
| 终态 | 状态 DID + SWD 读回 | `IDLE / application / v23 / 45304 B / seq 253`；内部 Flash 逐字节 == `app_wd.bin`（sha256 `04b0aa95…`） |

关键点：**外部 NOR 与 EEPROM 完全不受影响**（它们是独立芯片），所以升级包、备份镜像
和元数据都还在，恢复只需要"重烧内部 Flash 的两个镜像"。

## 五、诚实的边界

- 只做了 **RDP Level 1**。Level 2（`0xCC`，永久锁死、JTAG/SWD 完全禁用、不可回退）
  没有做——那会让这块学习板失去调试能力。
- 没有做 **WRP**（写保护）：RDP1 挡的是"读"，WRP 挡的是"擦写"。要防"改写 Bootloader"
  应该两者结合（RDP1 + 对 Bootloader 扇区开 WRP）。本轮只验证了读保护。
- 没有测**电压毛刺/故障注入**绕过 RDP 的可行性（那是实验室级攻击，超出本工程范围）。
- RDP1 下调试器附着会让板子静默，这一条是实测现象，机制解释（`C_HALT` 残留）
  基于 Cortex-M 调试架构的推断，没有用调试寄存器逐位验证。
- 防回滚版本计数器仍未实现：RDP1 挡住的是"改固件"，挡不住"重放旧包 + 旧元数据"。
  真正的单调计数器需要 OTP 或安全芯片，属于下一阶段。
