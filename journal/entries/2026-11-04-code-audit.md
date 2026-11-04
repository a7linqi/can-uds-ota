# 代码巡检：静态分析 + 属性模糊 + 严格模型 + 补测一个没测过的驱动

- 日期：2026-11-04
- 类型：质量检查
- 关键词：`fanalyzer` `fuzz` `page-boundary` `spi_nor` `metadata`
- 验证等级：主机模型 + 目标构建（无实机）

## 1. 一句话

用四种手段扫了一遍核心代码：静态分析零告警、解码器缺陷量化到 92.3%、元数据写入在严格 EEPROM 页模型下零违规、并给一个**从未被任何测试引用**的 NOR 驱动补了 18 条断言。

## 2. 背景与约束

产品路径要接外部 NOR，但仓库里没有任何测试引用 `nor_spi_*`；同时主机测试用的 EEPROM 模型比真实 AT24C02 宽松（不校验 8 字节页）。这意味着"3164 条断言全绿"并不能证明硬件路径可用。约束：不动 `src/`，检查工具与新测试都放 `build-temp/`。

## 3. 现象与结果

| 检查 | 范围 | 结果 |
|---|---|---|
| `arm-none-eabi-gcc -fanalyzer` | `src/core/*.c`、`src/driver/*.c` | **0 warning / 0 error**（⚠ 当时的命令带了 `-fsyntax-only`，分析器实际没运行；结论在正确模式下重扫仍为 0，见 [fanalyzer 空跑](2026-11-04-fanalyzer-silent-noop.md)） |
| 属性模糊（`fuzz_invariants.c`） | `can_uart_decoder_feed`，20 000 组随机试验 | 旧实现 **92.3%** 丢掉假头后的合法帧；补丁 **0%**；两版均 0 畸形帧 |
| 严格页模型（`strict_page_check.c`） | `metadata_save/load` | 20 次 EEPROM 写、**0 次跨页**；副本 A 损坏后正确回退到 B |
| NOR 驱动测试（`test_spi_nor.c`） | `src/driver/spi_nor.c` | **18 条断言全通过**（probe/边界/先擦后写/回读/保护/超时） |

## 4. 查出来的三个问题

1. **`src/driver/spi_nor.c` 不在 Keil 工程里** —— 头文件、实现都在，但没人链接；接线后第一次构建直接
   `undefined reference to nor_spi_init`。这是"以为驱动现成"的代价。
2. **驱动没有任何测试** —— 全仓库 `grep nor_spi tests/` 为空。已补 18 条。
3. **两个沉默的常量会让升级白做**：`ota_user_force_loader()` 恒 `1`（永远进 Bootloader）、
   `ota_user_app_healthy()` 恒 `0`（永远判不健康 → 必回滚）。它们不报错、不影响点服务。

## 5. 根因

问题 1/2 同源：**构建清单与测试清单都是手工维护的列表**，新增文件时容易漏。
问题 3 是"占位实现返回了一个安全但语义错误的值"：`0` 对"健康"是拒绝，对"未实现"也是拒绝，两者无法区分。

## 6. 修复/产物

- `build-temp/uart-resync-package/fuzz_invariants.c`：安全性/吞吐/活性三条属性，可对两版实现直接对比
- `build-temp/write-enable-package/tests/test_spi_nor.c`：18 条断言，带 `SPI_NOR_STANDALONE` 可独立跑，仓库模式编译通过
- `build-temp/strict_page_check.c`：把真实 EEPROM 的 8 字节页约束搬进测试
- `build-temp/write-enable-package/`：接线补丁（四目标链接通过）+ `apply_package.py`（幂等）

## 7. 验证命令

```text
tcc -I include src/core/can_uart.c            -run build-temp/uart-resync-package/fuzz_invariants.c
tcc -I include src/core/metadata.c src/core/crc32.c -run build-temp/strict_page_check.c
tcc -DSPI_NOR_STANDALONE -I include src/driver/spi_nor.c -run build-temp/write-enable-package/tests/test_spi_nor.c
```

## 8. 学到的东西

1. **测试替身比真实器件宽松时，"全绿"是假的安全感**。主机 EEPROM 允许任意写，真实芯片不允许跨页；差异只会在板上出现。
2. 静态分析能证明"没有它认识的那几类错"，不能证明逻辑对；它和模糊测试互补，都不能替代上板。
3. 占位实现要"显式失败"：`return 0` 表示健康，等于把"没实现"伪装成"不健康"，排查时会被误导。

## 9. 简历表述

「对 OTA 核心做一次系统性质检：GCC `-fanalyzer` 零告警；用属性模糊测试把解码器缺陷量化为 92.3% 丢帧；用严格 EEPROM 页模型验证元数据写入零违规；发现 NOR 驱动未被工程引用且无任何测试，补齐 18 条断言使其可被安全合入。」

## 10. 边界与未解决

- `-fanalyzer` 只覆盖 `src/core` 与 `src/driver`，未覆盖 `platform/`（HAL 依赖多）。
- 模糊测试只针对 `can_uart`；`isotp`/`uds` 仍靠既有用例。
- NOR 驱动测试是**模型**，擦除时序、真实 WIP 与电压裕度仍需上板。
