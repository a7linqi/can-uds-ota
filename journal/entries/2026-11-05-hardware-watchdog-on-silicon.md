# 硬件看门狗第一次真的咬人：32.341 秒，然后把 sick 镜像回滚掉

- 日期：2026-11-05
- 类型：验证（实机）/ 功能
- 关键词：`iwdg` `watchdog` `lsi` `trial` `rollback` `board-lab`
- 验证等级：实机（外加整体自检：3502×2 断言、86.71 s、138 哈希）

## 1. 一句话

把"看门狗只跑过主机模型"这件事终结掉：实测 IWDG 超时 **32.341 s**（LSI≈32.4 kHz），
证明正常升级期间不误咬、App 健康时持续喂狗、App 不健康时 3 次咬合后由 loader
用 8.71 s 回滚到上一镜像——全程没有外部复位参与。

## 2. 背景与约束

`OTA_ENABLE_WATCHDOG` 一直是 0，而且 `explorer_reference.h` 里有一条无条件
`#error "Explorer reference bring-up targets have no watchdog yet"`。
文档 `docs/WATCHDOG.md` 写得很清楚：必须先测真实 LSI 与最长无服务窗口，
才允许打开门控。约束：不改 C 核心逻辑；产品 mdk 目标保持关闭；
板上验证用一次性变体，不污染产品构建。

## 3. 现象

- `wd_bite`（启动 IWDG 后永不刷新）每 32.3 s 复位一次，连打四行横幅：
  `32.341 / 32.381 / 32.337 s`；寄存器回读 `PR=6`、`RLR=0x0FFF`、`SR=0`。
- `loader_wd + app_wd` 跑完整 OTA（`34` 14.04 s → 90 帧 → `37` → 安装跳转）
  全程零复位；App 装载后 70 s 内串口 0 字节、状态与 seq 不变。
- `sick_wd` v22：三次复位（32.62 / 32.61 / 32.38 s）→ RESTORING 8.71 s →
  回滚到 v20，之后 44 s 稳定。

## 4. 排查过程

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 闸门是安全限制，不能乱删 | 读 `explorer_reference.h` 与 `board_hal_binding.c` | 绑定层**早已实现** HAL IWDG；闸门是纯策略性的，HAL 模块也早已启用 |
| 2 | 直接删闸门会不会让 bring-up 目标误开 | 改成显式 opt-in：`OTA_EXPLORER_WATCHDOG_CAPABLE=1` | 带 `-Werror` 编译通过，产品目标零变化 |
| 3 | 测周期时串口一片空白 | 读 80 s 无输出 | pyserial 打开端口拉高 DTR/RTS，板子被按在复位上；打开后先拉低两线 |
| 4 | 两次 v21 安装"第 N 帧起全 NRC 0x22" | 看控制台是否有第二次启动横幅 | 没有 → 根本没复位；真相是命令漏了 `--handoff`，App 按设计拒绝写服务 |
| 5 | 以为"看门狗版 OTA 已成功" | 读内部 Flash 与候选镜像 | 不匹配——探针只发了 `34/36` 没发 `37`，从没安装过 |
| 6 | 回滚后内部 Flash 是 `app_wd` 而不是 sick | 读状态 + 内部逐字节比对 | 说明回滚确实发生；时间线里 4 次启动 = 3 次咬合 + 1 次 RESTORING |

**转折点**：第 4 步——把"NRC 0x22"和"看门狗复位"分开，靠的是"有没有第二次启动横幅"。
一个字节的横幅差异，决定了是查喂狗还是查命令。

## 5. 根因

不是缺陷，是缺口：硬件看门狗的实现一直在绑定层里，缺的是
"测出真实超时 → 显式开门 → 实机验证三件事（不误咬 / 会喂 / 必咬）"。
之前所有"看门狗已实现"的说法都只到主机模型。

## 6. 修复

- `platform/stm32f407/board/explorer_reference/explorer_reference.h`
  - 把无条件 `#error` 改成 `OTA_ENABLE_WATCHDOG && !OTA_EXPLORER_WATCHDOG_CAPABLE` 才报错。
- `build-temp/build_stage_d.py`
  - 新增 `loader_wd` / `app_wd` / `sick_wd` / `wd_bite` 四个变体。
- `build-temp/stage-d/wd_bite_main.c`（新增，一次性）
  - 启动看门狗后永不刷新，用横幅行间距测真实超时。
- 备选方案与放弃原因：直接改 mdk 目标打开 `OTA_ENABLE_WATCHDOG=1`。
  放弃：产品目标一旦默认开启，任何一次 bring-up 都会带着看门狗跑，
  和文档里"先测后开"的顺序相反；保留开关、用变体验证更安全。

## 7. 验证

```
python build-temp/build_stage_d.py loader_wd app_wd sick_wd wd_bite
python host/watchdog_period.py --port COM9 --seconds 110
python host/uds_stage_d.py --port COM9 --handoff --image app_v20_wd.img2 --out ...
python host/uds_stage_d.py --port COM9 --handoff --image app_v22_sick_wd.img2 --out ...
```

| 项 | 期望 | 实际 |
|---|---|---|
| 看门狗是否复位 MCU | 会 | 3 个周期 32.341 / 32.381 / 32.337 s |
| 反推 LSI | ~32 kHz | 32.4 kHz |
| 正常 OTA 是否误咬 | 不咬 | 90 帧 + 14 s 擦除 + 安装，零复位 |
| App 健康时 | 持续喂狗 | 70 s 零输出、状态不变 |
| App 不健康时 | 被咬并回滚 | 3 次复位（32.6/32.6/32.4 s）→ 8.71 s 回滚 → v20 |
| 回滚结果 | 逐字节等于 v20 镜像 | 内部 Flash == `app_wd.bin` |

未验证：低温/低压漂移；喂狗调用失败路径（F4 HAL 无失败分支）；产品目标开箱即用。

## 8. 学到的东西

1. **"实现了"和"验证过"是两件事**：绑定层代码一直在，但没有一次实机证据，
   文档就只能写"未验证"。
2. **NRC ≠ 复位，第二次出现**：断电实验的教训是"NRC ≠ 断电"，这次是"NRC ≠ 复位"。
   判据都是传输层/控制台层面的事实，不是协议层的一句话。
3. **探针脚本也会撒谎**：只发 `34/36` 不发 `37` 的脚本让"安装成功"看起来成立，
   只有 SWD 逐字节比对能戳穿。
4. 看门狗 + TRIAL 组合的真实价值：**不需要外部复位**就能推进试启动计数，
   这对现场设备（没人去按复位键）才是可用的失败恢复。

## 9. 简历表述

> 在 STM32F407 OTA 平台上完成硬件看门狗（IWDG）实机验证与启用：实测真实超时
> 32.341 s（LSI≈32.4 kHz），验证升级长擦除/长安装期间不误咬、应用健康时持续喂狗、
> 应用不健康时由看门狗自身三次复位推进试启动计数并在 8.71 s 内回滚到上一可用镜像。

> 难点：把"绑定层已实现"与"实机验证过"分开，用一次性 red probe 固件证明
> 看门狗真的会复位，而不是把配置当成结论。

## 10. 边界与未解决

- 超时用横幅行间距测，精度约 ±0.1 s，没有示波器；LSI 随温度/电压漂移。
- `DBG_IWDG_STOP` 未设置：调试器 halt 内核超过 32 s 会被复位。
- 产品 Keil 目标仍为 0；要进产品需改目标定义并重跑同一套验收。
- 没有测"喂狗调用本身失败"（F4 HAL 的 refresh 没有失败分支）。
