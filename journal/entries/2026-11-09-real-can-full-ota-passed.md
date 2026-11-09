# 真实 CAN 上第一次跑完整 OTA：89 块全部接受，18.55 秒，读回逐字节相同

## 1. 一句话

2026-11-09 晚在真实 CAN 总线（PCAN-USB 500 kbit/s）上把 v24 签名包完整刷进 STM32F407 并安装、TRIAL 确认、跳转 App，内部 Flash 读回与构建产物逐字节相同——项目最后一块「真实 CAN 完整 OTA 未验证」的空白补上了。

## 2. 背景

11-07 真实 CAN 只打通了只读诊断；完整 `0x34…0x37` → 安装一直在 UART 台架验证。11-09 白天备好可写 CAN loader/app 与 `REAL_CAN_OTA_RUNBOOK.md`；当晚 22:00 首次接线执行失败（A/B 对照已排除固件），最后发现**只是板子电源开关没开**。

## 3. 现象

电源开关打开后一切对齐：`USB-SERIAL CH340 (COM3)` 枚举、`ST-Link 目标电压 3.24 V`（关电时 2.87 V）、开机横幅 `CAN: normal physical 500 kbit/s`、只读验收 `status=passed` 并收到 `0x7E8 10 14 62 F1 90 4C 4F 54`。

## 4. 排查（留弯路）

1. **差点用了现成的 v14 包**。读 `status` 发现 EEPROM 里 `active_version=23 / min_version=23`，而 `0x37` 要求 `h.version > min_version && > active_version`（`uds.c:429`）——v14 会被 `7F 37 31` 拒掉，14 s 的擦除白费。改成用 `make_image.py --version 24 --sign` 现场生成 v24。
2. **`uds_tool.py inspect` 报 `signature_verified: false`**，一度以为包没签好。查 `inspect_package()` 发现那是**硬编码常量**，工具根本不验签；用纯 Python Ed25519 对 demo 公钥独立验 v24 和 v14，都是 `OK`。MCU 侧自己验签。
3. **`0x11` 之后 CAN 上 `status` 超时**，第一反应是「安装失败」。查 `firmware/app/main.c:22` 才发现 `ota_bootloader_can_rx` 是**空实现**：App 只保留 UART 诊断用于交棒（`app/main.c:43` 调 `ota_stm32_diag_init_uart`）。CAN 上没人应答是设计行为。改用 `--backend uart --serial-port COM3` 读状态 DID。
4. SWD 用 `hotplug` 读不回 App 区（`Unable to get core ID`），改 `mode=normal` 复位一次才读到——历史坑复现，记录在案。

## 5. 根因

前一晚链路不通的根因是**板子电源开关没开**：2.87 V 是 ST-Link 寄生供电，CAN 收发器缺 5 V。`USB_232` 同时负责供电与日志，所以 CH340 也不在线。除此之外固件、构建、门控、驱动、波特率常量全部清白（A/B 对照已证）。

## 6. 修复

无代码修复。真正生效的操作只有一个：**打开板子电源开关**。随后按 runbook 执行：备份 → 烧可写 loader（`48172 B`，`0f14d783…`）→ `status` 确认门开 → 生成 v24 包 → `flash --log` → 复位 → 读回比对。

## 7. 验证（全部实测）

| 项 | 结果 |
|---|---|
| 传输日志 | `can-ota-01.jsonl`，296 事件、`request_started` 96、**89 个 `block_accepted`、零负响应/零错误** |
| 总耗时 | **18.552 s**；`0x34` 擦除 14.197 s、89 块 `0x36` 3.757 s、`0x37` 0.321 s、`0x31` 0.269 s、`0x11` |
| 保活 | **一次 `3E 00` 都没发**——印证源码结论「S3 是空闲超时，同步 job 不计时」（`uds.c:595/626`、`stm32_diag.c:38/175`） |
| 看门狗 | 该变体 `OTA_ENABLE_WATCHDOG=0`；18.552 s 远小于此前实测 32.341 s 超时 |
| 安装后横幅 | `loader: handing off to the app at 08010000` → `app at 08010000: awaiting real health checks` |
| SWD 读回 | `installed-app.bin` 44824 B，sha256 `47f604c1…`，与 v24 payload **逐字节相同**；CRC32 `0xD6B888DB` |
| 96 B 凭证 | `0x080FFFA0` 处 32 B 头 + 64 B 签名与包内逐字节一致（签名 `ec40408a…`），version=24 |
| 终态 | `IDLE / application / v24 / min_version=24 / size=44824 / crc32=0xD6B888DB / boot_attempts=0 / seq 260` |
| 状态机前后 | v23 IDLE → v24 IDLE，`metadata_sequence` 253 → 260，`job_error=false`、`requests_dropped=0`、`service_deadline_misses=0` |

## 8. 学到

- **先 `status` 再选包**。元数据里的 `min_version` 才是版本门槛，不是「比当前 App 大」这种模糊印象；现场生成包比翻历史包可靠。
- **工具输出的字段名不等于它做了那件事**：`signature_verified=false` 是硬编码占位符，看到反直觉结果要去读实现，而不是急着重打包。
- **「请求超时」要先问「这个角色有没有这个端点」**。App 的 CAN 回调是空实现，超时是设计；把预期行为当故障会误导整轮判断。
- **A/B 对照之外还要 A/B 环境**：同一份固件、同一套脚本，只差一个电源开关，结论完全相反。`ST-Link 目标电压 3.24 V vs 2.87 V` 是比「灯亮不亮」硬得多的判据。
- 11-07 写的「不需要 `3E 00` 保活」这次在真实 CAN 上被实测证实，不再只是源码推断。

## 9. 简历表述

- 「在真实 CAN 总线（PCAN-USB + STM32F407，500 kbit/s）上跑通完整 UDS 固件升级：`0x10 02`→Seed/Key→`0x34`→**89 个 `0x36` 块**→`0x37` 验签→`0x31`→`0x11`，18.55 s 零负响应；复位后自动安装并通过 TRIAL 确认，内部 Flash 读回 44824 B 与构建产物逐字节相同，96 B 签名凭证核对一致，终态 `IDLE/application/v24`。」
- 「用 Ed25519 签名包 + 双副本事务元数据实现升级；在真实总线上验证 ISO-TP 多帧流控与 500 kbit/s 时序，全程无需 TesterPresent 保活。」

## 10. 边界

- **已验证**：真实 CAN 物理层 + ISO-TP 多帧 + UDS `0x10/0x27/0x34/0x36/0x37/0x31/0x11` + 复位后安装 + TRIAL 确认 + 逐字节读回 + 签名凭证。
- **仍未验证**：真实 CAN 上的断电恢复（三种切点只在 UART 台架做过）、总线负载/错误帧/长时间稳定性、故障注入与 bus-off 恢复、**从运行中的 App 经 CAN 发起的完整 OTA**（本次从 loader 起步；App 只保留 UART 诊断端点）、OTP 单调版本计数器、WRP/RDP2。
- 版本号 24 是演示用的单调版本，非产品防回滚机制；签名用的是 demo seed，非生产密钥。
- 终端电阻实测 160 Ω（拨码在 `120R`），非阻塞项，未调成 60 Ω。
