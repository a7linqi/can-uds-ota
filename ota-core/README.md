# 基于 CAN/UDS 的车载诊断与可靠固件更新平台

当前交付 **v0.13**：在 C 升级核心、PC 工具、STM32 适配层和探索者参考 BSP 上，完成升级包签名边界及下载/安装/恢复路径复核，补齐实际 python-can 虚拟后端的签名回归。2026-11-02 已通过实物 SWD 识别系列 ID 0x413、1 MiB 内部 Flash，匹配 ZG 容量配置。本项目固件尚未上板运行，没有真机刷写速度、物理 CAN 或断电恢复数据。默认 Bootloader/App 仍使用未配置 BSP；四个只读参考目标的写入、跳转和看门狗门控关闭。四套 GNU ELF/BIN 已重新构建并验证；Keil 链接器在本环境仍有启动错误。

## 从这里运行

需要 Python 3.10+ 和可用的 C99 编译器。完整验证不会打开真实 CAN：

~~~powershell
python verify_project.py --cc "你的编译器完整路径"
python tools/demo_roundtrip.py --cc "你的编译器完整路径"
python tools/demo_recovery.py --cc "你的编译器完整路径"
python tools/uds_tool.py info --cc "你的编译器完整路径"
python tools/uds_tool.py status --cc "你的编译器完整路径"
~~~

TinyCC 自动使用 -run，在编译器进程中执行原生 C；GCC/Clang 会生成并运行本机程序。仿真 ECU 使用实际 C 核心，Python 负责 PC 客户端。每次 CLI 启动的仿真存储都是全新的；跨升级演示使用 demo_roundtrip.py。

追加 --arm-bin、--dfp、--cmsis 可以检查 Cortex-M4 C 对象和 HAL 参考语法；再追加 --gcc 可完整链接四套 GNU 只读目标，详见 [验证说明](docs/VERIFICATION.md)。本次 Windows 环境的 ARMASM/armlink 与部分生成 EXE 遇到 DLL 重定位错误，Keil 完整链接尚未完成；未运行 ASan/UBSan。

## 已实现

- App 固定执行于 **0x08010000**；内部前 64 KiB 留给 Bootloader。
- Ed25519 升级包签名覆盖 IMG2 头和 payload，默认拒绝无签名包；严格检查签名标量编码。CRC-only 备份接口只接受备份槽及 flags=0，详见 [本轮核心复核](docs/CORE_REVIEW_20261102.md)。
- 外部 NOR 保存新包和旧版备份。内部 App 只装载 payload，32 字节 IMG2 包头不占向量表位置。
- AT24C02 两份 64 字节元数据：序号、CRC、提交标记最后写、读回；跨页写按 8 字节拆分。
- 旧版备份验证成功、INSTALLING 持久化后才擦内部 App。安装/恢复可从头重做。
- 新 App 最多试启动 3 次，只允许运行中的 App 健康钩子确认；未确认则恢复旧镜像。可选看门狗策略已通过模型验证，默认关闭，实际 IWDG 复位/恢复尚未验证。
- ISO-TP 分帧、流控、超时、WAIT 上限、发送失败清理、计时回绕和未读报文保护。
- UDS 下载只写外部下载区；处理重复块、块序号回绕、错误块、版本策略和会话超时。
- STM32 长操作先发送 0x78，等待队列/硬件邮箱空闲后执行，操作期间持续提供协作服务点。
- 自定义只读 DID 0x0200 查询升级状态、版本、长度和 CRC，便于调查提交应答丢失。
- PC 的 install 操作匹配 READY 状态后验证并安装，不重复下载；提交/复位应答丢失均停止自动重试。
- 可选 W25Q128JV-IQ/JQ 驱动检查 JEDEC、页边界、WEL/WIP、写保护和读回。实物型号、引脚、供电与时钟仍需核对。

## 当前验证

探索者只读入口：回环主机模型 **51 项断言 / 14 个场景**通过；四个参考目标各通过 43 个 ARM C 对象编译，并用 GNU Arm GCC 完成每目标 45 个对象的汇编链接及 ELF/BIN 地址校验。本项目固件实际执行仍未验证；SWD 芯片识别记录见 [板卡接收记录](docs/BOARD_RECEIPT.md)。

| 项目 | 结果 |
|---|---|
| 原始 L1 基线 | 236 个 C 断言，0 失败 |
| F407ZG / 1 MiB 核心 | 3138 个 C 断言，0 失败 |
| F407ZE / 512 KiB 核心 | 3138 个 C 断言，0 失败 |
| 每种容量的存储故障场景 | 420 个主机模型场景 |
| PC→C 与客户端边界 | 45 个 unittest 用例，0 失败 |
| 实际 python-can 4.6.1 虚拟总线→C | 7 个用例，0 失败；包括签名升级和拒绝路径，不连接 USB-CAN |
| SPI 命令及 C 核心联合验证 | 720 个断言，含 31 个 SPI 传输失败注入点 |
| STM32 端口，每种容量 | 默认门控 177 个断言；开启门控 243 个断言 |
| App 真实入口与虚拟看门狗 | ZG/ZE 各 35 个断言、8 个场景 |
| Cortex-M4 C 对象 | ZG/ZE × Bootloader/App，4 组各 27 个对象 |
| Python 打包/签名工具 | 22 个用例；另有 8 组 Python 包→C 校验互操作检查 |

420 个场景包括元数据 69、应用确认 69、下载 76、安装 130、回滚 76。Flash 在每次持久化调用的开始/中间/末尾采样，没有遍历全部镜像的每个字节。断言数、Python 用例数、注入点数不是同一种口径，不能合计成统一“用例数”。这些都不代表物理断电次数。

原始日志和总报告在 evidence/。验证报告绑定源码 SHA256；源码改变后需要重新验证再打包：

~~~powershell
python tools/package_project.py
~~~

默认生成工程上级 deliverables/ota-core-L1.zip，文件名保留早期名称，内容版本以本 README 为准。

## 工程入口

新增 [探索者参考 BSP 与只读上板入口](docs/EXPLORER_REFERENCE.md)：已依据 V2 原理图和 V3 手册核对共用引脚；书面证据确定探索者 V3 + ZGT6（1 MiB），实物 PCB 修订版与 NOR 型号仍待上电核验，NOR 写入驱动仍需按实际芯片选择。

正点原子探索者 F407 的板卡资料见 [V2/V3 官方资料与移植顺序](docs/ATK_EXPLORER_F407.md)；书面证据见 [板卡接收记录](docs/BOARD_RECEIPT.md)，实物版本、器件、时钟和跳线仍以到板核验为准。

| 入口 | 用途 |
|---|---|
| [存储与恢复](docs/STORAGE_MODEL.md) | 固定 App、新旧外部镜像、事务元数据 |
| [签名校验](docs/SIGNATURE.md) | Ed25519 升级包签名、公钥、验证流程与边界 |
| [PC 工具](docs/PC_TOOL.md) | 包预检、刷写、显式 python-can 后端、日志 |
| [状态查询](docs/UPDATE_STATUS.md) | DID 0x0200 格式及提交不确定时的调查 |
| [恢复安装](docs/STAGED_INSTALL.md) | 已提交包的显式安装及复位不确定状态 |
| [协议边界](docs/PROTOCOL_EDGES.md) | 流控、异常帧、错误恢复、计时回绕 |
| [等待应答](docs/RESPONSE_PENDING.md) | 0x78 调度与 BSP 服务期限 |
| [SPI NOR](docs/SPI_NOR.md) | 芯片命令驱动、可选 HAL 绑定及验证范围 |
| [看门狗](docs/WATCHDOG.md) | 试运行健康门控、确认失败与卡死恢复模型 |
| [板卡接入](docs/STM32_BRINGUP.md) | Keil、ZG/ZE 布局、CAN 读版本和接线核对 |
| [硬件验收顺序](docs/HARDWARE_ACCEPTANCE.md) | 到板后的前置核对、真实镜像与测量记录 |
| mdk/bootloader.uvprojx、mdk/app.uvprojx | 各有 ZG/ZE 目标，默认使用未配置 BSP |
| tools/make_image.py | 将链接于 0x08010000 的真实 App BIN 打包为 IMG2 |

## 接下来

板到手后，核对实物芯片/容量、SPI Flash、EEPROM 页大小和地址、CAN 引脚与时钟，先点灯 → CAN 回环 → USB-CAN 收发，再绑定真实存储。0x78 和服务点已做模拟验证，实际擦写时限、看门狗及 App 跳转仍需实板验证。

已有 CRC32 + 版本策略 + Ed25519 升级包签名校验（见 [签名校验](docs/SIGNATURE.md)），但**没有安全启动或完整 OTA 链路**。公开的演示 Seed/Key 不提供密码学安全；EEPROM 不提供防恶意篡改的硬件版本计数；签名公钥硬编码、演示 seed 非生产密钥。示例 payload 只有向量与填充字节，不能写入 MCU 当作 App。
