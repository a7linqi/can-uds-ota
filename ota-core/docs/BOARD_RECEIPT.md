# 探索者 F407 板卡接收记录

状态：购买凭证指向 V3 + ZGT6；2026-11-02 已读到系列 ID 0x413、1 MiB 内部 Flash，ZG 固件内部回环 16 帧通过，NOR RDID 为 EF 40 18、EEPROM 256 字节读取及 CRC 核对通过。完整 MCU/存储丝印、PCB 修订和物理 CAN 仍待核对。购买年份不用于自动选择 BSP。

## 书面证据（2026-10-31，卖家旗舰店购买凭证）

卖家提供的闲鱼购买凭证截图指向正点原子官方旗舰店订单，可作板卡型号的书面证据，但**不等于实物核验**：

- 商品 ID `609294673401` 是官方探索者 V3 购买链接（openedv V3 资料帖所给）。
- 缩略图带"升级版 / 2022 全新真人出镜视频"，为 V3 独有话术；V2 已停产。
- SKU 为"探索者 + 3.5 寸屏 320×480"；价格自洽（裸板 ¥498 + 屏 ≈ ¥626，83 折实付 ¥519.58）。
- 官方教程、《STM32 应用技术项目实践》教材、华秋商城板载资源三处一致写明主芯片为 STM32F407ZGT6（LQFP144，1 MiB Flash）；探索者无 ZET6 版本。
- 结论：书面证据确定 **V3 + ZGT6 + 3.5 寸屏**；卖家标题里的"ZET"为误标。

以上只锁定型号与代际。实物批次（NOR 型号、PCB 修订版、EEPROM 页大小）仍需上电读芯片确认，见下表"待填"项。

| 项目 | 实物或读回结果 | 对应依据 |
|---|---|---|
| 板名与 PCB 版本 | 书面证据指向探索者 V3；实物修订号未读出 | 购买凭证截图（书面证据），非实物核验 |
| MCU 完整丝印 | 屏幕遮挡，尚未读出；书面证据指向 ZGT6（1 MiB），非 ZET6 | 官方教程/教材/华秋一致为 ZGT6；实物按丝印抄录 |
| 调试器读出的 Device ID / Revision ID | Device ID 0x413；DBGMCU_IDCODE=0x10076413；REV_ID=0x1007，工具显示 Rev 2.0 | 2026-11-02 实物 ST-LINK/SWD 只读日志；该系列 ID 不唯一确定完整型号 |
| 读出的内部 Flash 容量 | 0x1FFF7A22 读出 0x0400，即 1024 KiB；工具报告 1 MBytes，匹配 ZG 容量配置 | 已实读；完整型号仍以丝印为准 |
| 对应官方原理图文件及版本 | 待填 | 见 [官方资料入口](ATK_EXPLORER_F407.md) |
| 供电接口与电压 | ST-LINK 报告目标参考电压 3.25 V；具体供电入口待填 | 连接日志读数，不能代替整板供电路径核对和独立电压测量 |
| 外部晶振与系统时钟配置 | reference 使用 8 MHz HSE；HCLK/PCLK1/PCLK2 读回为 168/42/84 MHz；晶振丝印和独立频率测量待做 | Phase 2 配置读回，不等于示波器测量 |
| CAN 收发器完整型号 | 待填 | 原理图与芯片丝印 |
| CAN 控制器、RX/TX 引脚与复用 | 待填 | 原理图及对应 HAL 例程 |
| CAN 跳线/终端电阻配置 | 待填 | 实物位置、跳线连接、总线两端配置 |
| SPI NOR 完整型号、供电、JEDEC ID | 实读 0x00EF4018，状态 0；完整丝印和供电参数待填 | Phase 2 原结构，不能据此推定完整后缀或写入配置 |
| SPI 实例、SCK/MISO/MOSI、CS | 待填 | 原理图及对应 HAL 例程 |
| NOR 容量、页/扇区大小与命令手册版本 | 待填 | 具体器件数据手册 |
| EEPROM 完整型号、7 位地址与页大小 | 地址 0x50 读到 256 字节 00..FF，CRC32 0x29058C73；型号和页长待填 | 实读和快照；图案来源未知 |
| EEPROM 总线和 SDA/SCL | 待填 | 原理图及例程；区分软件 I²C 与硬件 I²C |
| LED、按键、日志串口连接 | 待填 | 原理图及最小例程 |
| 原有内部 Flash / NOR / EEPROM 备份 | 内部 Flash 双读备份已完成，目录 `backups/board-original/20261102_204315_470/`，两份各 1048576 字节且 SHA256 相同；EEPROM 已有 Phase 2 快照和三次复位后的相同 256 字节快照；完整 NOR 备份待做 | 内部 Flash 验收记录、workspace `stages/app-bringup/evidence/phase2-*` 及 `stages/board-lab/evidence/loopback-retest/` |
| USB-CAN 型号、固件、接口和通道 | 待填 | 实际适配器配置，区分物理总线与虚拟后端 |

未完成项保持“待填”。记录 Chip ID、JEDEC ID 或读到 EEPROM 应答，不会自动开放擦写、App 跳转或看门狗。

## 首次实物识别（2026-11-02）

用户在本机启动只读检查，19:58:16（Asia/Shanghai）通过 STM32CubeProgrammer 2.23.0 / ST-LINK SN `I`、固件 `V2J47S7` 建立 SWD Hot Plug 连接；请求 1000 kHz，工具报告 950 kHz，CLI 退出码为 0。

原始日志和结构化记录保存在 `evidence/hardware/2026-11-02/`。这次完成的是物理 SWD 通信与内部 Flash 容量识别；尚未下载或执行本项目固件，也未验证 CAN、外部 NOR、EEPROM 或断电恢复。

20:43（Asia/Shanghai）已完成内部 Flash `0x08000000..0x080FFFFF` 的两遍实际只读导出，维护端重新计算长度和 SHA256 均一致：`a919efd8201a2d86b479baeb7ab327a90c28eaefd443b937364f42562b7e367c`。原始目录保留；这份备份不包含外部 NOR 或 EEPROM。

21:32（Asia/Shanghai）Phase 2 已执行：指定 ZG loopback 下载/校验/复位成功；RAM stage=3 / passed=16，NOR EF 40 18，EEPROM 状态 0 且 CRC32 一致。维护端复核原日志和二进制结构通过。原程序入口已覆盖，完整原备份保留；本次没有写 NOR/EEPROM。

下一步按根目录 `PHASE3_EXECUTOR_WORK.md`：执行端负责串口/USB-CAN 清点、复位复测、真实 EEPROM 离线验收、完整 NOR 只读备份工具和有条件的物理 CAN 诊断。完整存储型号、页长和真实升级/恢复仍待验收。

后续验收目标：LED 心跳 → CAN 内部回环 → USB-CAN 收发 → 只读 UDS。存储擦写、安装恢复和断电测试另按 [硬件验收顺序](HARDWARE_ACCEPTANCE.md)执行。
