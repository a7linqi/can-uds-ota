# 把 NOR 擦写能力接进参考 BSP，同时把"能写"和"允许写"拆成两道门

- 日期：2026-11-05
- 类型：功能 / 决策
- 关键词：`spi_nor` `wren` `explorer_reference` `uvprojx` `#error` `storage_verified`
- 验证等级：**目标构建**（4 个 ARM C 对象编译 + 4 套 GNU 完整链接）、**主机模型**（720 条驱动检查未变）、**未上板**

## 1. 一句话

产品仓库里的 `src/driver/spi_nor.c` 第一次被真正接进 BSP 的三个 `ota_user_nor_*` 钩子：读和探测无条件可用，页编程与 4 KiB 擦除编进来但升级引擎仍被 `storage_verified=0` 整体拒绝，唯一能真写一个扇区的路径要显式打开一个新开关并给出槽外地址。

## 2. 背景与约束

此前的分层是：`spi_nor.c` 是芯片协议层，只在主机上用 31 个 SPI 注入点验证过；`examples/w25q128_hal_user.c` 是可选的 HAL 接法示例，但**从来没被编进任何目标**；`explorer_reference.c` 的三个钩子一律 `return -1`，`ota_user_config()` 把 `storage_verified` 钉在 0。

硬约束：
- 实机确实擦写过 NOR，但那是 `stages/board-lab/firmware/w25q_bench.c`——它自己的注释就写着 "NOT the product flash path"。所以产品驱动**一次都没碰过真实硅片**，T3 之前只能算模型正确。
- `src/core/` 不动，四套"只读"固件的门控（`OTA_ENABLE_FLASHING/BOOT_HANDOFF/WATCHDOG=0`）不动，`storage_verified` 的语义不动——它是"授权"，不是"能力"。
- 512 块 NOR 恢复演练正在后台跑（写的就是外部 NOR），这轮只能改代码，不能烧板子、不能发任何 SPI 写命令。

## 3. 现象

接线前 `explorer_reference.c` 里三行钩子全是 `-1`，`read_storage_identity()` 只发 `0x9F` 读 ID。把驱动接上后，`run_explorer_checks.py` 四个目标全部通过（45 个 C 对象，`-Wall -Wextra -Werror`），看起来一切正常。

然后 `tools/build_explorer_gnu.py` 一链接就红：

```
explorer_reference.c:173:(.text.nor_open+0x9c): undefined reference to `nor_spi_probe'
explorer_reference.c:176:(.text.nor_open+0xb2): undefined reference to `nor_spi_read'
collect2.exe: error: ld returned 1 exit status
```

同一个源文件，编译器说没问题，链接器说这两个符号根本不存在。

## 4. 排查过程

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 是不是 `spi_nor.c` 里被 `#if` 掉、或者 C99 声明顺序有问题 | 直接看 `spi_nor.c`：函数都是普通外部链接 | 不是编译条件问题 |
| 2 | 是不是没加 `-Iinclude` 导致找不到头 | 编译日志显示 `ota/spi_nor.h` 正常包含，且没有隐式声明告警 | 头文件没问题 |
| 3 | 换个角度：查目标里到底编了哪些源文件 | `grep FilePath mdk/explorer_readonly.uvprojx` | **`src/driver/spi_nor.c` 不在文件清单里** |
| 4 | 那为什么编译阶段没报错 | 编译检查是**逐文件** `-c`，链接才需要符号；缺文件永远只在 link 阶段暴露 | 解释了"编译全绿、链接全红" |
| 5 | 手工把文件加进 uvprojx？ | 不行，工程由 `tools/generate_explorer_mdk.py` 生成，手改下次重生成就丢 | 改生成器 |
| 6 | 自检的 `#error` 级联太吵 | 打开自检但不给地址时，一次报了 5 条错（缺地址 → 地址被判 0 → 又触发对齐/越界） | 改成 `#if/#elif` 链，一次只报一条 |

**转折点**：第 3 步。前两步都在"代码写错了"这个方向上找，实际是**构建输入清单缺一项**。而构建检查分成"编译"和"链接"两段，第一段天然看不见缺文件——这正好是"检查项之间互补"的又一个例子。

## 5. 根因

`generate_explorer_mdk.py` 的 `sources` 字典把"只读 BSP"当成一份封闭清单：core + adapter + ed25519 + 3 个板级文件 + 入口 + ST HAL/startup。`spi_nor.c` 从来没被列进去，因为以前这个工程规定"不碰写入"，驱动就没必要编。我把钩子接上以后，代码引用了新符号，但生成器没同步。

## 6. 修复

- 文件：`platform/stm32f407/board/explorer_reference/explorer_reference.c`（新增 `spi_profile_ok()` 总线体检、`nor_spi_bus_t` 四个回调、`nor_open()` 只读探测 + 偏移 0 处 256 字节干读、可选 `nor_write_selftest()`；三个 `ota_user_nor_*` 钩子按 `OTA_EXPLORER_NOR_WRITE` 分支）
- 文件：`explorer_reference.h`（新增 `OTA_EXPLORER_NOR_WRITE` 默认 1、`OTA_EXPLORER_NOR_SELFTEST` 默认 0，以及"缺地址 / 非 4 KiB 对齐 / 落在镜像槽内 / 没开写钩子"四条 `#error`；探针结构体加 `nor_bytes/nor_read_crc32/nor_status/nor_read_status`）
- 文件：`tools/generate_explorer_mdk.py`（新增文件组 `SPI NOR chip driver`，把 `src/driver/spi_nor.c` 编进四个 explorer 目标）
- 文件：`run_explorer_checks.py`（新增 4 个编译变体：写钩子关闭必须过；自检指向 `0x00FFF000` 必须过；自检指向 `0x00001000` 必须被拒；自检缺地址必须被拒）
- 备选方案与放弃原因：把 `examples/w25q128_hal_user.c` 直接编进 explorer 工程——它和 `explorer_reference.c` 定义同名钩子，重复符号；而且它的 `ota_hal_nor_probe()` 没人调用，`storage_verified` 也不会自动变真。放弃。

刻意保留的设计：`storage_verified` 仍是 0，`nor_bytes/nor_page_bytes/nor_erase_bytes` 在探测成功后如实填进去。这样"能力"和"授权"是两个独立事实，下一步放开时只需要改一行，而且这一步不会顺带改写任何扇区。

## 7. 验证

```
python run_explorer_checks.py --arm-bin "D:/Keil/Keil5/ARM/ARMCLANG/bin" \
  --dfp "D:/Keil/Keil5/ARM/PACK/Keil/STM32F4xx_DFP/2.16.0" \
  --cmsis "D:/Keil/Keil5/ARM/PACK/ARM/CMSIS/5.0.1/CMSIS/Include"
python tools/build_explorer_gnu.py --gcc ".../arm-none-eabi-gcc.exe" --dfp ... --cmsis ...
python verify_project.py --cc ".../tcc.exe" --arm-bin ... --dfp ... --cmsis ... --gcc ...
```

| 项 | 期望 | 实际 |
|---|---|---|
| 四目标 ARM C 对象 | 每个 46 个、0 告警 | 46 个，通过 |
| 写路径编译变体 | 2 过 2 拒 | 过 2、`#error` 拒绝 2 |
| GNU 完整链接 | 4 套、地址/SP/Reset 校验通过 | ZG 回环 16176 B、ZG 串口 43912 B、ZE 回环 16172 B、ZE 串口 43912 B |
| 主机 C 断言 | 3385/容量、0 失败 | 3385/3385，0 失败 |
| 断电场景 | 420 | 420 |
| SPI NOR 驱动 | 720 项、31 个注入点 | 720，0 失败 |
| 完整验证快照 | `status=passed` | passed，130 哈希，159.8 s |

未验证：
- 真实 NOR 上程序/擦除（本轮代码路径一次都没执行；`hardware_verified=false`）
- 探针日志里 `nor_status/nor_bytes/nor_read_crc32` 的实际取值（要等演练结束、烧进去才能看）
- `keil_target_linked=false` 未变：Keil 工程只做了源文件计划与 ARM 对象，仍受 armlink DLL 问题影响

## 8. 学到的东西

1. **"编译通过"不等于"能被链接"**。逐文件编译的检查天然无法发现"目标里少了某个源文件"，只有链接才是这项判据。以后给固件新增模块，必须同一条命令里跑完 link，不能只看对象生成。
2. **能力与授权应该分开表达**。把"钩子能写"和"引擎允许写"拆成 `OTA_EXPLORER_NOR_WRITE` 与 `storage_verified`，就能在不动 `src/core` 的前提下逐级放开，而且每一级的状态都能在日志里读出来。
3. **破坏性自检要用编译期约束而不是文档约定**。地址不 4 KiB 对齐、落在镜像槽内、忘了给地址——三种情况都让编译失败，比在注释里写"请勿"可靠得多。
4. 生成器里的清单就是构建事实。手工改生成物的时间只有到下次重生成为止。

## 9. 简历表述

> 把 W25Q128 协议驱动接进 STM32F407 参考 BSP：以 SPI 总线适配层承载 4 个注入式回调，启动只做读 ID/状态 2/干读校验，擦写钩子与"是否授权"解耦；新增 4 个编译变体（2 个必须被 `#error` 拒绝），四套 GNU 固件由 12.7–22.3 KB 增至 16.2–43.9 KB 后仍全部通过 ELF/BIN/地址校验，主机 3385 条断言零失败。

> 定位并修掉"编译全绿、链接全红"的构建输入缺失：驱动源码从未进入工程文件清单，只有链接阶段能暴露。

## 10. 边界与未解决

- 探针通过只证明片选、时钟、模式 0、ID 与读通路，**不证明擦写可用**，更不证明芯片是 W25Q128JV 而不是手册里提到的 NM25Q128/BY25Q128 互换件。
- 第一次真写必须挑一个槽外扇区并先写好恢复办法；整片金标 dump 的 CRC 会因为这个扇区变化而不再相等，恢复脚本要能只补这一块。
- 512 块恢复演练仍在后台运行，结果未出；本轮没有烧写、没有复位、没有发任何 SPI 写命令。
