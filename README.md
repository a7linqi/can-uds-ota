# CAN/UDS Reliable Firmware Update Platform

基于 STM32F407 的**车载风格固件更新（OTA）链路**：PC 通过 CAN 总线发送 UDS 诊断刷写命令，新固件先落到外部 SPI NOR 的下载区，校验 CRC32 与 Ed25519 签名后，由 Bootloader 备份旧版本、安装新版本，App 自检健康才提交，否则自动回滚。

核心目标不是"能升级"，而是**升级中断后不变砖**。

---

## 1. 为什么需要这套东西

普通开发用 ST-Link 直接烧录就行。但真实产品（车上、现场设备）不可能拆机接线，必须做到：

- 设备通过网络（此处是 CAN）自己接收固件
- 任何时刻断电，复位后能判断"上次做到哪一步"，重做或回滚
- 新固件跑不起来时自动退回旧版本
- 固件不能被伪造替换

本工程针对这四点做完整设计，并把失败路径作为一等公民测试。

---

## 2. 硬件配置

| 项 | 型号 / 参数 |
|---|---|
| MCU | STM32F407ZGT6，Cortex-M4，168 MHz，**1 MiB Flash / 192 KiB RAM** |
| 外部 NOR | **W25Q128 家族，16 MiB**，SPI1（JEDEC `EF 40 18`） |
| EEPROM | AT24C02，256 B，I²C1 地址 `0x50` |
| CAN | CAN1，PA11/PA12，**500 kbit/s，采样点 85.7%**，诊断 ID `0x7E0`/`0x7E8` |
| 开发板 | 正点原子探索者 STM32F407（PCB `ATK-DNF407 V3.3`） |

### 存储布局

```
内部 Flash（1 MiB）
0x08000000  Bootloader      64 KiB
0x08010000  App（固定地址）  960 KiB

外部 NOR（16 MiB，本工程只用前 2 MiB）
0x000000    下载区 Download  1 MiB   ← 新固件先存这里
0x100000    备份区 Backup    1 MiB   ← 旧固件搬这里当退路

EEPROM（256 B）
偏移 0 / 128  两份事务元数据副本
```

**为什么用外部 NOR 而不是内部 A/B 双槽**：内部只有 1 MiB，双槽意味着每个槽 ≤ 480 KiB，装不下 960 KiB 的 App。改用"固定 App 地址 + 外部新旧镜像"后，内部空间利用率更高，回滚只需搬运而不用改向量表偏移。

---

## 3. 一次升级的完整路径

```
① PC 上位机
   打包（32 B IMG2 头 + payload）→ Ed25519 签名 → ISO-TP/UDS 客户端 → USB-CAN
                              ↓
② CAN 总线   经典 CAN 2.0B，500 kbit/s，ID 0x7E0 / 0x7E8
                              ↓
③ UDS 收包（MCU）
   0x34 设定总长 → 0x36 分块写入 NOR 下载区 → 0x37 校验 CRC + 签名 → 置 READY
                              ↓ 复位
④ Bootloader 决策
   READY      校验通过 + 备份旧 App 到 NOR 备份区 → INSTALLING
   INSTALLING 擦内部 App 区、从 NOR 装新 App → TRIAL
   TRIAL      试运行计数 +1（先持久化）→ 启动新 App
   RESTORING  试运行 3 次仍失败 → 从备份区搬回旧 App
                              ↓
⑤ 两种结局
   App 健康自检通过 → boot_confirm → 提交定稿
   失败             → RESTORING → 回到旧版本
```

---

## 4. 关键设计点

### 4.1 事务型元数据：把掉电当常规情况

EEPROM 里存的不是简单一个 `version`，而是一条带提交语义的记录：

```
两份副本（偏移 0 / 128）
  + 序号递增        防止读到旧副本
  + 整条 CRC       防止半写
  + commit 最后写   0xA55A5AA5，防止"以为提交成功了"
```

读取时先验 `commit`，不匹配就整条作废、回退另一副本。`commit` 必须最后写，这样任意时刻掉电都能判断状态。

### 4.2 Ed25519 流式验签（1 MiB 镜像 vs 192 KiB RAM）

CRC32 只防意外损坏，防不了伪造（攻击者可重算 CRC）。因此引入 Ed25519：

- 签名 64 B、公钥 32 B，追加在 payload 之后，`flags` bit0 标记
- **流式验证**：通过 `flash_port_t` 分块读 NOR 喂 `sha512_update`，内存占用恒定
- 检查 `S < L`（RFC 8032 规范），拒绝 `S+L` 这类非规范编码
- `OTA_REQUIRE_SIGNATURE=1`，无签名一律拒绝

### 4.3 备份区例外：一个必须自我约束的安全边界

备份镜像只做 CRC、不做签名——因为备份内容来自"已通过签名验证的 App"，且恢复时设备没有私钥重签。

但这会开一个口子：如果验证器接受任意偏移，攻击者就能拿它去验下载区，等于绕过签名。因此 `image_validate_backup` 严格限定：

```
只接受 BACKUP_OFFSET
且 flags == 0
```

### 4.4 core 不依赖 HAL

`src/core/` 通过 `flash_port_t` / `eeprom_port_t` 两个函数表访问存储，**一行 HAL 代码都没有**。

所以同一份 `boot.c` 既能在 PC 上用 RAM 模拟存储跑完整回归，也能编到 Cortex-M4 上运行——这是可验证性的来源。

---

## 5. 目录结构

```
ota-core/
├── src/core/          可移植核心（无 HAL 依赖）
│   ├── boot.c           上电决策、备份、安装、回滚
│   ├── metadata.c       事务元数据（双副本 + commit）
│   ├── image.c          镜像头与 payload 校验
│   ├── signature.c      Ed25519 流式验签
│   ├── uds.c            UDS 服务
│   ├── isotp.c          ISO-TP 分帧 / 重组
│   └── crc32.c
├── src/driver/        SPI NOR 驱动（0x9F / 0x03 / 页编程 / 扇区擦除）
├── src/port/host/     PC 侧 RAM 模拟存储
├── platform/stm32f407/  HAL 适配与板级支持
├── firmware/          四个入口：bootloader / app / loopback / diagnostics
├── include/ota/       公共头文件与内存布局常量
├── tools/             PC 工具：打包签名、CAN 客户端
├── tests*/            主机测试（TinyCC 直接编译运行）
└── docs/              架构、存储模型、签名设计、检查点

board-docs/            板卡资料包（引脚表、原理图解析、LCD 驱动）
CAN_UDS_HANDOFF.md     项目交接总览
```

---

## 6. 构建与测试

### 主机测试（不需要硬件）

```bash
python run_tests.py --cc <path-to-tcc> --capacity 1024 --in-memory
python run_driver_checks.py --cc <path-to-tcc>
python run_pc_checks.py     --cc <path-to-tcc>
python run_stm32_checks.py  --cc <path-to-tcc> --in-memory
python -m unittest discover -s tests_tools
```

### 目标构建（GNU Arm GCC）

```bash
python tools/build_explorer_gnu.py \
  --gcc  <path-to-arm-none-eabi-gcc> \
  --dfp  <path-to-STM32F4xx_DFP> \
  --cmsis <path-to-CMSIS>
```

产出四套只读固件（ZG 1 MiB / ZE 512 KiB × loopback / diagnostics），含 ELF/BIN/MAP。

### 全量验证

```bash
python verify_project.py --cc <tcc> --gcc <gcc> --dfp <dfp> --cmsis <cmsis>
```

验证结果写入 `evidence/verification.json`，包含每个编译输入的 SHA256，确保"报告对应的就是当前源码"。

---

## 7. 验证状态（诚实标注）

| 层面 | 状态 |
|---|---|
| 主机 C 回归 | **3138 条断言 × 2 种容量（ZG/ZE），0 失败** |
| 存储断电场景 | **420 个模型场景**（RAM 模拟 + 故障注入，非物理断电） |
| PC 集成 / NOR 驱动 / 适配器 | 45 / 720 / 177+243 断言，全部通过 |
| 目标构建 | 四套 GNU 只读固件完整链接，ARMClang `-Werror` 零警告 |
| **实机已完成** | 芯片识别（`0x413` / 1 MiB）、时钟 168/42/84 MHz、**内部 CAN 静默回环 16 帧（三次复位复测）**、NOR JEDEC 识别、EEPROM 读取与 CRC 校验、内部 Flash 1 MiB 双遍备份一致、LCD（NT35310）从零点亮 |
| **实机未完成** | **真实 CAN 总线通信**（缺 USB-CAN 适配器）、**真实升级 V1→V2**、**升级中断后重做**、**试运行失败回滚**、**真实断电恢复**、NOR 实际擦写、擦写耗时测量 |

**当前可复现的上限是"主机模型 + 目标构建 + 部分实机识别"。完整升级与断电恢复尚未在真实硬件上执行。**

---

## 8. 边界与免责

- **不是安全启动**：没有 BootROM 级信任根
- **没有硬件防篡改版本计数器**：回滚保护依赖 EEPROM 的 `min_version`，可被物理改写
- **演示密钥**：`include/ota/pubkey.h` 中的 Ed25519 公钥由确定性演示种子 `00 01 02 ... 1f` 派生，任何人都能复现。它提供**篡改检测**的演示价值，**不是生产密钥**
- **演示 Seed/Key**：UDS `0x27` 安全访问用的是演示值，不代表密码学安全
- **非量产实现**：量产需引入 HSM/安全启动、防回滚计数器、CAN FD、AUTOSAR NM 集成

---

## 9. 第三方组件

| 组件 | 位置 | 许可 |
|---|---|---|
| orlp/ed25519 | `ota-core/third_party/ed25519/` | Zlib |

详见该目录下 `NOTICE.md`。

---

## 10. License

MIT（本项目自身代码）。第三方组件遵循其各自许可。
