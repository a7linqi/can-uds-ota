# 从运行中的应用连续升级三次，并读回内部 Flash 逐字节比对

- 日期：2026-11-05
- 类型：功能（实机闭环）
- 关键词：`uds` `ota` `handoff` `ed25519` `readback` `stm32f407`
- 验证等级：实机

## 1. 一句话

把"运行中的 App → loader → 下载 → 验签 → 安装 → 跳转 → App 确认"整条链放进**一个串口会话**，
连续做了三轮（v9/v10/v11），并用 ST-Link 读回内部 Flash 与构建产物做 SHA-256 比对，结果逐字节相同。

## 2. 背景与约束

上一轮补了 `boot_request` 这个 App→loader 入口，但只在"手工分两步"的情况下验证过。
本轮先发现关串口会复位板子（见另一篇），据此把交棒和下载合并成一次连接，才算真实流程。

约束：
- 不得为通过测试而放宽校验（签名、CRC、`sane()` 一律不动）；
- 判决必须来自器件自身或读回比对，不能只看固件自报的状态字。

## 3. 现象

初始状态（读回确认）：silicon `device_id=0x413`、`flash_bytes=0x00100000`、Rev 2.0；
元数据 `IDLE / active v5 / 44168 B / 0x4786A682`，但内部 Flash 里是一份 44288 B 的新镜像
（ST-Link 直接烧的）——**元数据与实物不一致**。

## 4. 排查过程

| 步骤 | 目的 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 确认板子在线 | 重枚举 PnP | ST-Link（`USBDevice` 类）+ CH340（COM9）都在 |
| 2 | 确认芯片 | `STM32_Programmer_CLI -c port=SWD mode=UR` | F407xx、1 MiB、Device ID 0x413、3.24 V |
| 3 | 看旧固件行为 | 读 `22 02 00` | 40 字节记录（旧固件），`IDLE / v5 / 44168 / 0x4786A682 / seq 46` |
| 4 | 换成新固件 | 烧 loader 47324 B + app 44288 B | 校验通过；复位后 loader 打印 `loader: staying in the loader` |
| 5 | 从这里装 v8 | `uds_stage_d.py` | `37→77`、`31→71 01 00 01 00`、跳转成功 |
| 6 | 从 App 起装 v9/v10/v11 | `uds_stage_d.py --handoff` | 三次全部成功，`active_version` 依次 9/10/11 |

**转折点**：第 4 步。元数据说 v5/44168、Flash 里是 44288，loader 没有卡死也没有乱跳，
而是打印 `loader: staying in the loader` —— 这是上一轮"READY 卡死"修复在实机上的正面证据，
且它**没有**擅自把元数据清空（状态仍是 IDLE，等一次真正的升级）。

## 5. 根因

不需要"根因"的轮次：这是把已有能力在实物上跑通。
真正需要解释的是第 4 步的现象——`boot_select` 的 IDLE 分支用 `active_ok()` 判定，
校验失败就返回 `BOOT_TARGET_LOADER`，不改元数据；只有 READY 分支才会清 active 四字段。
两条路径的差别正好对应两种现实：**"元数据过时"**（等新包，别动记录）与**"要装新包但 active 坏了"**（先退休 active）。

## 6. 修复

无固件改动（本轮固件只把状态 DID 第 42 字节换成 `requests_dropped`）。
工具改动见 `2026-11-05-port-close-resets-the-board.md`：`uds_stage_d.py` 增加 `--handoff`。

## 7. 验证

```
python host/uds_stage_d.py --port COM9 --handoff --image <app_vN.img2> --out evidence/stage-d/<dir>
```

| 项 | 期望 | 实际 |
|---|---|---|
| v8（从"元数据过时"状态起） | 装好并跳转 | `37→77`、`31→71 01 00 01 00`、`loader: handing off to the app at 08010000` |
| v9 交棒升级 | `active_version=9` | 9，`active_size=44288`，`active_crc=5F966357`，seq 71 |
| v10 交棒升级 | `active_version=10` | 10，seq 79 |
| v11 交棒升级 | `active_version=11` | 11，seq 87 |
| 读回比对（v10） | 与构建产物相同 | `sha256 bed181e9ea2b4089`、`crc32 0x5F966357`、44288 B，**逐字节相同** |
| `34` 擦除 | — | 13.82 / 14.26 / 14.24 / 13.90 s |
| `37` 验签 | — | 0.53 s（44384 B 包） |
| `31` 安装 | — | 0.48 s |
| 每帧 `36` | — | 0.05–0.34 s（超时修好前是 0.43–2.47 s） |

主机侧同时全绿：C 核心 3421 断言 × 2 容量、420 个断电模型场景、STM32 适配层 258/325 × 2 容量、
PC 47 项、四套 GNU 只读镜像完整链接；`evidence/verification.json` `status=passed`，197.3 s，136 个源码哈希。

未验证：真实拔电中断；物理 CAN（USB-CAN 未到货）；硬件 IWDG（固件里 `OTA_ENABLE_WATCHDOG=0`）。

## 8. 学到的东西

1. "元数据与实物不一致"是必然会发生的状态（换固件、回滚、产线烧录），必须定义清楚两种处理：
   等新包时不动记录，要装新包时才退休坏 active。
2. 升级成功的最终证据是**读回比对**，不是状态字。状态字可以自洽地错。
3. 三次连续升级比一次成功更有说服力：它排除了"某次靠残留状态碰巧成功"。

## 9. 简历表述

> 在 STM32F407 上实现并实测可重复 OTA：应用运行态经 UDS `10 02` 请求重启进入 Bootloader，
> 单连接完成 `34/36/37/31` 下载、Ed25519 验签、安装与跳转，连续三轮升级（v9→v11）全部成功，
> 读回内部 Flash 与构建产物 SHA-256 一致（44288 B，CRC32 0x5F966357）。

> 边界：断电中断、真实 CAN 总线与硬件看门狗尚未验证。

## 10. 边界与未解决

- 备份区恢复路径（`RESTORING`）本轮未触发（三次升级 active 均有效，走的是 `create_backup`）。
- 每轮升级都会把当前 App 拷到 NOR 备份区，备份区容量与擦写寿命未评估。
- `--handoff` 依赖"关端口复位"这一硬件事实：换一块不带 DTR→NRST 的板子，流程需要显式 `11 01` 也可工作，但未实测。
