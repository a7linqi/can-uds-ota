# 硬件看门狗证据（实机）

目标：把 `OTA_ENABLE_WATCHDOG` 从"默认 0 且被编译期拒绝"变成**有实机数字支撑的开关**。
板子：STM32F407ZGT6 + W25Q128 + 24C02，与断电实验同一块。

## 结论先说

| 项 | 实测 |
|---|---|
| 看门狗是否会真的复位 MCU | **会**，3 个周期 **32.341 / 32.381 / 32.337 s**（中位数 32.341 s） |
| 反推 LSI | **32.4 kHz**（标称 32 kHz，超时 32.768 s 的推算值） |
| 寄存器回读 | `IWDG_PR=0x6`（256 分频）、`IWDG_RLR=0x0FFF`、`IWDG_SR=0` |
| 正常 OTA 期间是否误咬 | **不咬**：90 帧 + `34` 14 s 擦除 + 安装跳转，全程无复位 |
| App 健康时是否持续喂狗 | **是**：连续 70 s（2.2× 超时）零 UART 输出，状态与 seq 不变 |
| App 不健康时是否被咬 | **是**：sick 镜像 3 次复位（32.62 / 32.61 / 32.38 s）→ 回滚 |
| 回滚耗时 | **8.71 s**（擦 960 KiB + 写回 45304 B + 验签） |

## 一、代码侧改了什么

1. `platform/stm32f407/examples/board_hal_binding.c` **早就有** `HAL_IWDG` 实现
   （分频 256 / 重装 4095 → 标称 32.768 s），但 explorer 目标从来没用过它。
2. `explorer_reference.h` 原来有一条无条件硬闸门
   `#if OTA_ENABLE_WATCHDOG → #error "no watchdog yet"`。改成显式 opt-in：
   必须同时定义 `OTA_EXPLORER_WATCHDOG_CAPABLE=1`，否则仍然编译失败。
   **产品 mdk 目标保持 0**，默认行为一字未变。
3. 没有改任何 C 核心逻辑；喂狗策略（`watchdog_policy`）在主机模型里早就验证过。

## 二、实测一：看门狗咬不咬（`wd_bite`）

一次性固件 `build-temp/stage-d/wd_bite_main.c`：启动 IWDG 后**永不刷新**，只打一行横幅。
每复位一次就打一行，行间距就是真实超时。

```
[   0.071] wd_bite: watchdog started, no reload from here
[  32.412] wd_bite: watchdog started, no reload from here
[  64.793] wd_bite: watchdog started, no reload from here
[  97.130] wd_bite: watchdog started, no reload from here
periods: 32.341 / 32.381 / 32.337 s   → median 32.341 s, implied LSI 32.4 kHz
```

工具：`host/watchdog_period.py`。注意：pyserial 打开端口会把 DTR/RTS 拉高，
这块板子把两线接到 NRST/BOOT0——**必须先拉低再读**，否则板子被按在复位上、串口一片空白
（第一次跑就踩了，80 秒零输出）。

## 三、实测二：真实 OTA 期间不误咬（`loader_wd` + `app_wd`）

同一套电源/串口路径，把 loader 与 app 都换成 `OTA_ENABLE_WATCHDOG=1` 构建：

- 完整升级 v20（watchdog 版自身作为载荷，45304 B / 45400 B 包）：
  `34` 14.04 s 擦除 → 90 帧 `36` 全正响应 → `37 → 77` 验签 → `31 → 71` → `11 01`
  → 安装跳转。**全程没有一次复位**，说明长擦除/长安装的喂狗点覆盖住了。
- App 稳定性：装载后连续 70 s 采样，串口 **0 字节**输出，`22 02 00` 前后
  `state/role/version/size/seq` 完全一致（`IDLE/app/v20/45304/seq 169`）。

## 四、实测三：看门狗驱动回滚（`sick_wd` v22）

`sick_wd` = 能启动、能应答状态、但 `ota_user_app_healthy()` 永远返回 0 的镜像。
它不确认自己 → 不喂狗 → 只能靠 IWDG 推进 TRIAL 计数。

时间线（`host` 侧逐行时间戳，`evidence/watchdog/rollback-watchdog-timeline.txt`）：

| t (s) | 事件 |
|---|---|
| 0.58 | loader 启动，`selecting boot target` 后 **24.2 s 静默**：上一次安装被脚本关端口打断，这里幂等重做 |
| 24.75 | 交棒给 sick v22（TRIAL 第 1 次启动） |
| 57.37 | **IWDG 复位 #1**（+32.62 s）→ 第 2 次启动 |
| 89.98 | **IWDG 复位 #2**（+32.61 s）→ 第 3 次启动 |
| 122.36 | **IWDG 复位 #3**（+32.38 s）→ 计数耗尽 |
| 122.36–131.07 | RESTORING：擦 960 KiB + 写回 v20 + 验签，**8.71 s** |
| 131.07 | 交棒给恢复后的 v20，此后稳定（捕获窗口余下 44 s 无复位） |

判决：内部 Flash 读回 **逐字节等于 `app_wd.bin`**；状态 `IDLE / application / v20 /
45304 B / seq 202`。也就是说：**没有任何外部复位参与**，看门狗自己把三次试启动烧完、
让 loader 回滚到上一个可用镜像。

## 五、过程里的两个坑（都写进日志了）

1. 两次 v21 安装"第 N 帧开始全部 NRC `0x22`"，一度以为是看门狗把 loader 咬了。
   真相：命令漏了 `--handoff`，板子还在 App 里，而 App 按设计对 `34/36/37/31` 一律回 `0x22`。
   判据：控制台没有第二次启动横幅 → 根本没复位。**NRC ≠ 复位**，与断电实验里
   "NRC ≠ 断电"是同一条教训的第二次出现。
2. 第一次"看门狗版 OTA"其实没装成：探针脚本只发了 `34/36`，没发 `37`，
   板子仍是会自我确认的 v20，所以 150 s 内当然不复位。

## 六、边界（诚实）

- 没有示波器：超时是"横幅行间距"测出来的，精度约 ±0.1 s；LSI 随温度/电压漂移，
  这组数字是**当前室温、当前供电**下的值。
- `DBGMCU` 的 IWDG 冻结位没有设置：调试器把核 halt 住时看门狗**仍在计数**，
  长时间 halt（>32 s）会被复位。这是刻意的，`docs/WATCHDOG.md` 已写明。
- 产品 Keil 目标仍然 `OTA_ENABLE_WATCHDOG=0`；板上跑的是验证变体
  （`loader_wd` / `app_wd`），要进产品还得改目标定义并重跑同一套验收。
- 没有测"喂狗调用本身失败"（`HAL_IWDG_Refresh` 在 F4 HAL 里没有失败分支）。
- 没有测低温/低压下的超时漂移。
