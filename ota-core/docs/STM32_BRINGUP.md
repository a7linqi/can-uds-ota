# L2 准备版：STM32F407 接入入口

使用正点原子探索者 F407 时，先查看 [V2/V3 官方资料与移植顺序](ATK_EXPLORER_F407.md)，填写 [板卡接收记录](BOARD_RECEIPT.md)。2023 年购买不能直接确定板卡修订版。

当前交付完成工程结构、端口适配和主机验证，尚未完成硬件移植。默认 BSP 不选引脚、不假定晶振、不报告存储器就绪；默认刷写与安装跳转关闭。

## 工程入口与内存

| 工程 | 目标 | Flash | 执行区 |
|---|---|---|---|
| mdk/bootloader.uvprojx | bootloader_ZG_UNCONFIGURED | 1 MiB | 0x08000000 / 64 KiB |
| mdk/bootloader.uvprojx | bootloader_ZE_UNCONFIGURED | 512 KiB | 0x08000000 / 64 KiB |
| mdk/app.uvprojx | app_ZG_UNCONFIGURED | 1 MiB | 0x08010000 / 960 KiB |
| mdk/app.uvprojx | app_ZE_UNCONFIGURED | 512 KiB | 0x08010000 / 448 KiB |

四个 scatter 文件位于 firmware/linker/。均使用主 SRAM 0x20000000 / 128 KiB；ST 启动模板的栈改为 4 KiB、堆设为零。没有将 CCM 算成连续主 SRAM。

工程按 ARM Compiler 6 和 Keil.STM32F4xx_DFP 2.16.0 组织。若本地注册的 Compiler 6 版本不同，请在 Keil 选择实际安装版本。tools/generate_mdk.py 可重新生成默认骨架，会覆盖默认项目配置，因此不要用它覆盖已接好实物的工程。

默认未配置下载算法，也不生成可直接使用的固件 HEX。C 文件已完成 Cortex-M4 对象编译；本机命令行 armasm/armlink 遇到 Windows DLL 重定位兼容错误，**没有完成启动汇编、完整链接、下载或真实跳转验证**。旧 uVision XSD 与所装 pack 的 Compiler 6 示例字段不兼容，本轮只记录 XML 解析、源路径和内存配置语义检查，不声称通过完整 XSD/Keil 构建。

## 已提供的接缝

| 文件 | 用途 |
|---|---|
| platform/stm32f407/include/ota_board.h | 实物 BSP 的函数契约 |
| platform/stm32f407/board/board_unconfigured.c | 默认失败返回，保护未配置的板子 |
| platform/stm32f407/src/stm32_internal_flash.c | 扇区边界、App 范围、4 字节编程和加锁 |
| platform/stm32f407/src/stm32_spi_flash.c | 外部区域检查、页拆分、逐扇区擦除 |
| platform/stm32f407/src/stm32_eeprom.c | AT24C02 范围和 8 字节页约束 |
| platform/stm32f407/src/stm32_can.c | RX 中断队列、主循环 TX 队列 |
| platform/stm32f407/src/stm32_diag.c | ISO-TP/UDS 主循环与复位排空 |
| platform/stm32f407/src/stm32_handoff.c | 再验元数据和 App 后准备跳转 |
| platform/stm32f407/src/stm32_arch.c | 中断清理、VTOR、MSP/CONTROL、复位 |
| platform/stm32f407/examples/board_hal_binding.c | HAL 绑定参考，不在默认工程中 |
| platform/stm32f407/examples/board_hal_user.h | 实物工程要提供的句柄/配置钩子 |

SPI NOR 芯片协议仍是用户钩子，需要按实际型号实现 WREN、WIP、读、擦除、页编程和有界超时。没有假设板载一定是某个 W25Q 型号。EEPROM 地址、FLASH 电压范围、CAN/I2C 句柄等同样由实物 BSP 提供。

目前目标 main 是裸机结构，尚未引入 FreeRTOS。所有存储和 UDS 处理都在同一主循环；CAN ISR 只入队。若以后 App 使用 RTOS，不能直接从任务/ISR 调用当前裸机跳转入口。

## 板子到手后的接入顺序

1. 核对芯片丝印、器件 ID 和 Flash 容量，以及外部 Flash、EEPROM 型号和原理图。
2. 用 CubeMX 建立正确的时钟、GPIO、串口、CAN1、SPI、I2C；先独立点灯和串口，再测 CAN 回环。
3. 实现 ota_bsp_init 或替换为 HAL 参考绑定，保持 storage_verified=0。先跑 CAN 诊断，不开启刷写。
4. 实现 ota_user_can_rx，使其调用 bootloader main 提供的 ota_bootloader_can_rx。已有 HAL FIFO0 回调内调用 ota_hal_can_fifo0_callback，不定义两个同名回调。
5. CAN 接收通知要在诊断对象初始化后开启：本 main 调用 ota_bsp_can_start 的位置已经安排好。
6. 独立验证三种存储驱动的读写、页/扇区边界、超时和返回值后，才标记 storage_verified=1。
7. 实物端继续完成长擦写的 P2/P2* / NRC 0x78、看门狗、启动路径和镜像安装联调，再启用刷写门控。

保留且仅保留一套 main、启动文件、SystemInit、HAL 中断回调。默认 system_minimal.c 只给骨架设置 VTOR/FPU，不提供完整 HAL 的 SystemCoreClock 管理。接入真实 CubeMX 工程时保留生成的 system_stm32f4xx.c，移除 system_minimal.c，并保证 App 的 VECT_TAB_OFFSET=0x10000；不能让生成代码把 VTOR 改回 Bootloader。

## 第一个硬件验收：CAN 读版本

请求 ID=0x7E0，响应 ID=0x7E8，标准数据帧。500 kbit/s 的位时序要根据实际 APB1/CAN 时钟计算，不从骨架猜晶振。

先用单帧读当前会话：

| 方向 | ID | 数据 |
|---|---|---|
| Tester → ECU | 0x7E0 | 03 22 F1 86 00 00 00 00 |
| ECU → Tester | 0x7E8 | 04 62 F1 86 01 |

再读 F195 演示核心版本 0.2.0，验证多帧和流控：

| 方向 | ID | 数据 |
|---|---|---|
| Tester → ECU | 0x7E0 | 03 22 F1 95 00 00 00 00 |
| ECU → Tester | 0x7E8 | 10 08 62 F1 95 30 2E 32 |
| Tester → ECU | 0x7E0 | 30 00 00 00 00 00 00 00 |
| ECU → Tester | 0x7E8 | 21 2E 30 |

响应短帧可使用实际 DLC，末尾不属于 UDS 的填充字节不作为有效载荷。F195 当前是核心演示版本，不是实际 App 镜像版本。

## 默认门控与失败行为

- OTA_ENABLE_FLASHING=0：0x34/0x36/0x37/0x31 返回条件不满足，读版本不依赖存储器就绪。
- OTA_ENABLE_BOOT_HANDOFF=0：不执行启动安装及 App 跳转。
- 容量不匹配、几何参数未知或 storage_verified=0：存储端口返回失败。
- 内部 Flash 永不通过这些端口擦写前 64 KiB；4 字节对齐和扇区边界不满足则拒绝。
- 软件 TX 队列空还不够：ECUReset 要同时等 ISO-TP 完成和硬件邮箱真正排空。
- App 只有通过真实健康检查后才调用 boot_confirm；默认健康钩子不报成功。
- 可选 IWDG 与健康喂狗策略已提供，见 `WATCHDOG.md`；默认门控为 0。模拟已覆盖卡死/确认失败后的复位模型，真实 LSI 时限、安装余量和硬件自动恢复仍待验证。
- 当前 UDS 核心含同步存储调用，真实长擦除尚未满足 P2/P2*；响应等待/调度已完成模拟验证，仍需实测 BSP 服务间隔，不要把默认门控打开当成完成了实机刷写。

## App BIN 打包

真实 App 必须链接到 0x08010000，BIN 第一项是 MSP、第二项是 Thumb 复位入口。打包工具检查范围、长度和版本，生成带 32 字节头的 IMG2：

~~~powershell
python tools/make_image.py app.bin app-v1.img --version 1 --flash-kib 1024
~~~

app.bin 是原始执行镜像，app-v1.img 是传输容器。不能把带头的 img 直接从 0x08010000 烧入。工具不提供签名，也不能证明 App 指令会正确执行。

## 验证范围

- 主机适配检查：ZG/ZE 各跑默认门控 177 项、启用门控 243 项，全部通过。
- ARM C 对象编译：ZG/ZE × Bootloader/App 四组，每组 19 个 C 对象，通过 -Wall -Wextra -Werror，并包含启用门控的分支。
- HAL 绑定参考：针对本机 ST 头文件通过语法检查，未链接完整 HAL。
- Python 打包工具 14 项测试；打包结果交给真实 C image_validate，正确包被接受，坏头和坏数据被拒绝。
- 原 L1 的 3050 项检查和 420 个主机存储故障场景仍保留。

日志在 evidence/stm32-* 与 evidence/image-packaging*。这些结果都不是实际 CAN 收发、MCU 跳转或物理断电记录。


长操作等待应答框架已补齐，见 `RESPONSE_PENDING.md`；请先测量 BSP 服务间隔，再考虑启用写入。当前没有真机时延证据。
