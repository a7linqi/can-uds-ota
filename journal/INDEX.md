# 索引

一行一条，新的在最上面。**可写进简历 = Y** 的条目构成面试素材库。

| 日期 | 类型 | 标题 | 关键词 | 可写进简历 |
|---|---|---|---|---|
| 2026-11-05 | 缺陷（推翻旧判断） | [撤回「门控卡死」的判断：NRC 0x22 有两个原因，而我把它们当成了一个](entries/2026-11-05-gate-is-not-a-fuse.md) | uds, nrc-0x22, ota_enable_flashing, status-did, stm32_diag | Y |
| 2026-11-05 | 功能 | [给运行中的 App 一条进入编程会话的路：`boot_request` 一次写入、一次消费](entries/2026-11-05-app-to-loader-handoff.md) | uds, boot, metadata, handoff, stm32 | Y |
| 2026-11-05 | 缺陷 | [修好「App 坏了就再也装不进新包」：READY 分支不该被 active 校验拦住](entries/2026-11-05-broken-active-blocked-ready.md) | boot, metadata, sane, image_validate_app, ota | Y |
| 2026-11-05 | 决策（推翻旧判断） | [推翻：EEPROM 的 crc32 不是内容指纹，是结构指纹](entries/2026-11-05-eeprom-crc-is-a-structure-fingerprint.md) | crc32, metadata, eeprom, at24c02 | Y |
| 2026-11-05 | 功能 | [把合法签名包装进内部 Flash 并跳转过去](entries/2026-11-05-full-ota-on-silicon.md) | uds, bootloader, ed25519, handoff, stm32f407 | Y |
| 2026-11-05 | 缺陷 | [UDS 说能收 512 字节，BSP 只肯收页内——阶段 C 之前先修跨页写](entries/2026-11-05-cross-page-write-found-by-block-c.md) | uds, 0x36, spi-nor, page-wrap, test-double | Y |
| 2026-11-05 | 实机验证 | [第一次通过产品 UDS 路径真写 NOR，整片 CRC 与预测逐字节一致](entries/2026-11-05-first-real-uds-write.md) | uds, 0x34, 0x36, 0x37, spi-nor, crc32 | Y |
| 2026-11-05 | 功能/工具 | [给恢复协议加「范围恢复」：一次写坏→修回从 87 分钟降到 77 秒](entries/2026-11-05-range-restore-for-write-experiments.md) | nor-restore, swd-mailbox, range-restore, evidence | Y |
| 2026-11-05 | 功能/决策 | [把「能写」和「允许写」拆成双开关，上板 A/B 证明存储层只在双开时可达](entries/2026-11-05-two-switch-gate-t4.md) | uds, 0x34, storage_verified, s3, #error | Y |
| 2026-11-05 | 功能 | [512 块恢复演练通过，产品 NOR 驱动首次读通真实硅片](entries/2026-11-05-drill-passed-and-driver-on-silicon.md) | nor-restore, crc32, spi_nor, w25q128, evidence | Y |
| 2026-11-05 | 功能/决策 | [把 NOR 擦写能力接进参考 BSP，同时把「能写」和「允许写」拆成两道门](entries/2026-11-05-nor-write-path-into-bsp.md) | spi_nor, wren, uvprojx, #error, storage_verified | Y |
| 2026-11-05 | 质量检查/推翻旧判断 | [全量复核任务清单：T14 看门狗也是「已实现但被误记为未开始」](entries/2026-11-05-task-list-recheck.md) | task-list, watchdog, code-review | N |
| 2026-11-05 | 环境/缺陷 | [512 块演练卡在 49：每块删一个临时文件触发了安全删除守卫](entries/2026-11-05-bulk-delete-guard-killed-drill.md) | safe-delete, bulk-delete, long-running, tee | N |
| 2026-11-05 | 缺陷/推翻旧判断 | [定死 512 块演练停在 478 的根因：设备比主机的一次读还快](entries/2026-11-05-drill-handshake-root-cause.md) | nor-restore, swd-mailbox, race, ack | Y |
| 2026-11-05 | 环境 | [硬件闸门把主机工具脚本也算作固件输入](entries/2026-11-05-build-binding-too-wide.md) | hardware-gate, stage-hashes, build-record | N |
| 2026-11-05 | 决策（推翻旧判断） | [推翻清单判断：`0x3E` TesterPresent 与 S3 超时早就实现了](entries/2026-11-05-tester-present-already-implemented.md) | uds, 0x3E, S3, task-list | N |
| 2026-11-05 | 功能/决策 | [把三个已验证补丁合入产品仓库，回归从 3164 涨到 3328](entries/2026-11-05-three-packages-merged.md) | merge, red-green, isotp, uds, can-uart | Y |
| 2026-11-05 | 决策（推翻旧判断） | [合并后包内 verify.py 判别力消失，以及四个打包期结论被推翻](entries/2026-11-05-package-verify-lost-discrimination.md) | verify.py, baseline, crlf, git-apply | Y |
| 2026-11-05 | 功能/决策 | [实现 0x19 / 0x14 DTC：先想清楚「不做持久化」才敢动手](entries/2026-11-05-dtc-services-ram-only.md) | uds, dtc, 0x19, 0x14, ram-only | Y |
| 2026-11-04 | 缺陷 | [512 块演练在第 478 块停住（芯片内容仍等于金标，握手有竞态）](entries/2026-11-04-drill-stall-at-478.md) | nor-restore, swd-mailbox, race | Y |
| 2026-11-04 | 功能 | [把五项自检收成一条命令（27 秒），并反证它真的会红](entries/2026-11-04-verify-all-aggregator.md) | verify_all, negative-control | Y |
| 2026-11-04 | 缺陷 | [0x22 用 0x72 顶替"响应过长"（0x14），以及一条因错误原因而通过的测试](entries/2026-11-04-uds-nrc-response-too-long.md) | uds, nrc, iso14229-1, buffer | Y |
| 2026-11-04 | 质量检查/防线 | [元数据存储的真实 EEPROM 页规则守卫（超严模型反证它会变红）](entries/2026-11-04-metadata-page-guard.md) | metadata, at24c02, page-write | Y |
| 2026-11-04 | 环境/质量检查 | [静态分析一直在空跑（`-fsyntax-only` 静默关闭 `-fanalyzer`）](entries/2026-11-04-fanalyzer-silent-noop.md) | fanalyzer, false-clean, check.py | Y |
| 2026-11-04 | 决策（推翻旧判断） | [那两个"沉默常量"不是缺陷，是编译期闸门](entries/2026-11-04-build-gate-not-silent-constants.md) | explorer_reference, #error, handoff | Y |
| 2026-11-04 | 缺陷 | [ISO-TP 把保留 STmin 当致命错误（1 行修复 + 135 条边界断言）](entries/2026-11-04-isotp-reserved-stmin.md) | isotp, stmin, iso15765-2, uds | Y |
| 2026-11-04 | 质量检查 | [代码巡检：静态分析 + 属性模糊 + 严格模型 + 补测 NOR 驱动](entries/2026-11-04-code-audit.md) | fanalyzer, fuzz, spi_nor | Y |
| 2026-11-04 | 缺陷（推翻旧判断） | [串口零响应的对照实验：BOOT0 / VTOR 判据](entries/2026-11-04-boot0-vtor-experiment.md) | boot0, vtor, dtr-rts | Y |
| 2026-11-04 | 实机验证 | [第一次在真实 NOR 上擦除+编程：3 块冒烟与"中断仍等于金标"](entries/2026-11-04-first-real-nor-write.md) | w25q128, swd, 幂等, crc32 | Y |
| 2026-11-04 | 功能 | [上位机连接管理：连接/断开合一 + 后端切换门控](entries/2026-11-04-gui-connection-toggle.md) | tkinter, 状态机, serial | Y |
| 2026-11-04 | 环境/缺陷 | [上位机接真机：三个只有硬件才暴露的坑](entries/2026-11-04-uart-bringup.md) | uart, pyserial, boot0, isotp | Y |
| 2026-11-04 | 缺陷 | [UART 解码器半帧吃掉下一帧（19/300 → 300/300）](entries/2026-11-04-can-uart-resync.md) | can-uart, resync, crc16 | Y |
| 2026-11-04 | 功能 | [NOR 恢复演练工具链：擦写 + 自校验 + 断电收敛](entries/2026-11-04-nor-restore-drill.md) | nor, swd-mailbox, crc32, 幂等 | Y |

## 待补（先记着，有空补写）

- RTC/时钟树实测（2026-11-03）
- LCD NT35310 FSMC 驱动从零点亮（2026-11-03）
- NOR 全片 dump 为什么是 3 小时（3.63 KB/s 的成因）
