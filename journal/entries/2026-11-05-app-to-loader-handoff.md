# 给运行中的 App 一条进入编程会话的路：`boot_request` 一次写入、一次消费

- 日期：2026-11-05
- 类型：功能
- 关键词：`uds` `boot` `metadata` `handoff` `stm32`
- 验证等级：主机模型、实机

## 1. 一句话

在此之前，App 跑起来之后没有任何办法请求"下次复位请停在 Bootloader"，第二次升级只能靠清空元数据把板子装成空片——这一轮把这条入口做出来，并且不让它变成"永远停在 loader"的陷阱。

## 2. 背景与约束

约束有三条：元数据记录必须仍是 64 字节（EEPROM 页 8 字节、双副本布局都已固化）；旧记录要能被解码（不能因为加字段就让已有板子变成空片）；Bootloader 自己不能重复写这个旗标，否则一次升级做完下次复位又被拉回来。

## 3. 现象

阶段 D 走完整 OTA 之后，板子复位直接跳 App。想再升一次，手上唯一的办法是用 `wipe` 变体把两份元数据都作废，让 loader 当成空片停下。这既不是产品行为，也没法在面试里讲成"ECU 的编程入口"。

## 4. 排查过程

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 元数据里还有空闲字段 | 读 `metadata_t` | `reserved[3]` 12 字节，编码/CRC 窗口都覆盖它 |
| 2 | 用 `reserved[0]` 存旗标 | 改 encode/decode 并跑主机测试 | 记录长度、字段偏移、CRC 窗口都不变；旧记录解码为 0 |
| 3 | App 根本没有诊断栈 | 读 `firmware/app/main.c` | `ota_user_uart_rx` 是空桩，字节到不了 ISO-TP 队列 → `10 02` 没人接 |
| 4 | 接上诊断栈就该能落盘 | 在 App 里初始化 `ota_stm32_diag_init_uart()` 并把 RX 接到 `ota_stm32_uart_rx_isr()` | `10 02` 回 `50 02`，EEPROM seq 从 42 变成 43 |
| 5 | 复位后 loader 会不会照旧跳 App | 在 loader 里加 `boot_consume_boot_request()` | 第一次仍跳 App：`metadata_load()!=1` 时该函数返回 0（当作"无请求"） |

**转折点**：第 5 步。同一个"读元数据失败"，`boot_select()` 的处理是停住不跳，`boot_consume_boot_request()` 的处理却是"当作没有请求"然后跳 App。两处对同一失败给出相反的动作，等于把一次 EEPROM 读故障变成"请求被丢掉"。

## 5. 根因

入口缺失是两层：App 侧没有诊断传输（写不了旗标），loader 侧没有消费动作（读了也不认）。而消费动作的实现细节——读失败时返回什么——直接决定这条链路在 EEPROM 抖动时是"保守停住"还是"静默跳走"。

## 6. 修复

- 文件：`include/ota/metadata.h`（`reserved[3]` → `boot_request` + `reserved[2]`；记录仍是 64 字节）
- 文件：`src/core/metadata.c`（encode/decode 带上新字段；`sane()` 拒绝 `boot_request>1` 或非零 `reserved[]`；`save` 不清 `boot_request`，它是状态不是缓存）
- 文件：`include/ota/boot.h` / `src/core/boot.c`（`boot_request_loader()` 落盘返回是否成功；`boot_consume_boot_request()` 读不到记录时返回 1 = 保守停在 loader）
- 文件：`src/core/uds.c`（`10 02` 里先调 `request_bootloader` 落盘、再回 `50 02`，保证"看到正响应就已经持久化"）
- 文件：`platform/stm32f407/src/stm32_diag.c`（钩子 `diag_request_bootloader()` 只在 `ota_bsp_is_application()` 为真时才写旗标）
- 文件：`firmware/app/main.c`（接 UART 诊断栈，只为一个目的：让 `10 02` 能落到 loader）
- 文件：`firmware/bootloader/main.c`（启动时消费旗标并打印）
- 备选方案与放弃原因：用 RTC 备份寄存器或 `noinit` RAM 存请求——放弃，掉电就丢，而"请求下次复位"必须跨掉电；另考虑让 App 直接跳 loader 的复位向量——放弃，App 不知道 loader 是否可信，走复位最干净。

## 7. 验证

```
python run_tests.py --cc <tcc.exe> --capacity 1024 --in-memory
python host/uds_app_handoff.py --port COM9 --out evidence/<dir>
```

| 项 | 期望 | 实际 |
|---|---|---|
| 主机模型两容量 | 全过 | 3421 断言，0 失败（新增 `app_to_loader_handshake()`） |
| 实机：App 收到 `10 02` | `50 02` 且旗标落盘 | EEPROM seq 42 → 43，`boot_request=1` |
| 实机：复位 | loader 停住不跳 | 打印 `loader: stop requested by the application` |
| 实机：随后装 v5/v6 | 正常安装跳转 | 成功 |
| loader 自己收 `10 02` | 不写旗标 | 由 `gate_checks()` 断言 `boot_request_accepted==0` 且 EEPROM 写次数不变 |

未验证：`boot_request` 与"安装中掉电"叠加的时序（模型覆盖，物理断电未做）。

## 8. 学到的东西

1. 一次"请求"必须是一次写入 + 一次消费；只写不消费就是永久拉回 loader，只消费不写就是没有入口。
2. 同一个底层失败在两个调用点给出相反动作，是这类状态机的典型坑：要按"最坏情况还能不能修"来定，而不是按"看起来像没有"来定。
3. 测试替身不能自己实现协议的一部分：`boot_hook()` 最初只返回常量、不落盘，测试假通过；改成直接调用 `boot_request_loader()` 后，替身和板级钩子走同一条路径。

## 9. 简历表述

> 为运行中的 App 增加 UDS 编程入口：`10 02` 把一次性 `boot_request` 持久化进 64 字节元数据（复用保留字段，旧记录兼容），Bootloader 启动时消费并停在诊断态，实机验证 EEPROM seq 42→43、复位后 loader 停留、随后完成一次完整 OTA。

> 难点：旗标既不能丢（掉电后仍要生效）也不能重（否则装完永远回 loader）。

## 10. 边界与未解决

- App 侧只接了 UART 诊断，真实 CAN 通道未在 App 上验证。
- 若 `10 02` 的正响应在旗标落盘后丢失，主机重发是安全的（旗标幂等），但没有专门的用例覆盖"重发到第二次会话"。
- `boot_request` 目前只有 App 会写；如果将来 App 也支持 CAN，需要再确认 `ota_bsp_is_application()` 在两种通道下都取到正确值。
