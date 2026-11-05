# RDP 才是信任锚：上锁之后，SWD 一个字节也读不出来

- 日期：2026-11-05
- 类型：验证（实机）/ 安全设计
- 关键词：`rdp` `option-bytes` `swd` `trust-anchor` `secure-boot` `mass-erase`
- 验证等级：实机

## 1. 一句话

把"Bootloader 自身不验签"这条边界推到终点：自校验没有意义（能改写它的人也能改校验），
真正的信任锚是硬件读保护——实测 **RDP Level 1 让同一段固件从"能完整读出"变成
`Data read failed` 且不产出文件**，而板子照常启动、照常应答 UDS。

## 2. 背景与约束

项目已有的安全链是：升级包 Ed25519 签名 → App 区尾部 96 B 凭证（擦除即失效）→
启动前验签 → TRIAL 回滚。缺的一环是"Bootloader 自己凭什么可信"。
约束：不能把学习板锁成砖；RDP 只能升到 Level 1（Level 2 不可回退，不做）；
本轮不引入 OTP 版本计数器。

## 3. 现象

RDP0（`0xAA`）：`-u 0x08000000 0x400` → `Data read successfully`，1 KiB 落盘。

`-ob RDP=1` → `Option Bytes successfully programmed`（0.803 s，无 mass erase 记录）
→ `-ob displ` 显示 `RDP : 0x1 (Level 1)`。

同一读命令再执行 → **`Error: Data read failed`，文件根本没生成**。

## 4. 排查过程（含一次误判）

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 上锁后板子该照常跑 | 读串口 | **完全静默**，UDS 超时 |
| 2 | 写选项字节把 Flash 擦了 | `-hardRst` / `-run` | 都没用 |
| 3 | 调试器把内核按住 | 拔掉 ST-Link USB（保留供电） | **仍然静默**（探针走了不会清 `C_HALT`） |
| 4 | 只有掉电才能清调试 halt | 三路供电全拔 5 s 再插回 | **完整启动**：loader→handoff→App，状态 v20/seq 246，VIN 正常 |

**转折点**：第 4 步。如果没做整板断电，就会把"调试器按住内核"误判成"Flash 被擦/芯片损坏"。

## 5. 根因

- RDP Level 1 的语义是**禁止调试访问主 Flash**（读、写、擦都禁），CPU 自己取指不受影响。
- 调试器附着时，内核已经被 halt；RDP1 下调试口又无法写 `DHCSR` 把它放出来，
  于是"板子看起来死了"。整板断电重启即可恢复运行。
- "Bootloader 自校验"无效的原因：敌人若有 SWD 写权限，可以直接把校验代码一起改掉。
  可信的根只能是硬件属性（RDP / WRP / 安全启动熔丝）。

## 6. 修复

- 不改代码：加一段"Bootloader 验自己"的代码是安全剧场。
- 实机演练并记录 **RDP Level 1**：上锁 → 验证读不出 → 验证照常运行 → 解锁（mass erase）→ 恢复镜像。
- 备选方案与放弃原因：
  - RDP Level 2：不可回退，学习板会永久失去调试能力，不做。
  - 只加 WRP 不加 RDP：挡写不挡读，固件仍可被提取做逆向，不作为唯一手段。
  - 在 EEPROM 里做"防篡改版本计数器"：EEPROM 可被重写，没有硬件单调性，
    挡不住"重放旧包 + 旧元数据"，不冒充解决。

## 7. 验证

```
STM32_Programmer_CLI -c port=SWD mode=HOTPLUG freq=1000 -u 0x08000000 0x400 baseline-loader.bin
STM32_Programmer_CLI -c port=SWD mode=HOTPLUG freq=1000 -ob RDP=1
STM32_Programmer_CLI -c port=SWD mode=HOTPLUG freq=1000 -u 0x08000000 0x400 rdp1-loader.bin
STM32_Programmer_CLI -c port=SWD mode=HOTPLUG freq=1000 -rdu
```

| 项 | 期望 | 实际 |
|---|---|---|
| RDP0 能否读出固件 | 能 | 1024 B，sha256 `7b1e4f0c…` |
| RDP1 能否读出固件 | 不能 | `Data read failed`，无文件 |
| RDP1 下能否运行 | 能 | 整板断电重启后完整横幅 + `IDLE/app/v20/seq 246` + VIN |
| 解锁后 Flash | 空白 | 256 B 全 `0xFF`，RDP 回 `0xAA` |
| 恢复后终态 | 可开发、镜像一致 | `IDLE/app/v23/45304 B/seq 253`，内部逐字节 == `app_wd.bin` |

未验证：RDP2、WRP、电压毛刺/故障注入；`C_HALT` 残留的机制解释是架构推断。

## 8. 学到的东西

1. **安全边界要分清"读"和"写"**：RDP 挡读，WRP 挡写，两者不是一回事。
2. **测量陷阱比缺陷更危险**：RDP1 + 调试器 = 板子静默，差点被记成"芯片被擦"；
   一次整板断电就把结论翻过来了。
3. **"自己验自己"是伪需求**：可信根必须来自代码之外（硬件属性）。
4. 恢复路径要提前想清楚：RDP1 是单向门，解锁必然整片擦除，但外部 NOR/EEPROM 不受影响，
   所以恢复只需重烧两个内部镜像 + 一次 OTA。

## 9. 简历表述

> 为 OTA 安全启动补上信任锚：在 STM32F407 上实测 RDP Level 1——同一段固件从
> "SWD 可完整读出"变为 `Data read failed` 且不产出文件，同时验证受保护固件仍能
> 正常启动并响应 UDS；完成解锁（mass erase）后的镜像恢复（v23，内部 Flash 逐字节一致）。

> 难点：区分"读保护"与"调试器把内核 halt 住"两种静默，用整板断电把误判翻过来。

## 10. 边界与未解决

- 只做 RDP1，不做 RDP2（不可回退）；未做 WRP；未做故障注入。
- 防回滚版本计数器仍未实现：RDP 挡"改"，不挡"重放"。
- 板子最终留在 RDP0（学习板需要 SWD），生产设备应当 RDP1 + WRP 并关闭调试口。
