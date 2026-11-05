<!-- 证据原件在 stages/board-lab/evidence/stage-d/20261105/，该目录被 .gitignore 排除（体积大）。
     这里保留可复核的清单与结论；二进制快照只在本机。 -->

# 阶段 D 证据：完整升级链在实机上走通

日期：2026-11-05
板子：STM32F407ZGT6 + W25Q128（16 MiB）+ AT24C02，UART 台架 115200（COM9）
固件：`build-temp/stage-d/loader/loader.bin`（46836 B，sha256 `6070b7190d76047e…`）
包：`build-temp/stage-d/app_v1.img2` / `app_v2.img2`（各 20380 B，payload 20284 B）

## 结论

1. 设备通过产品 UDS 路径收下**合法 Ed25519 签名包**，`0x37` 验签通过，`0x31` 确认 READY。
2. `0x11` 复位后 loader 自行擦 960 KiB 内部 Flash、安装、进 TRIAL、跳转到 0x08010000。
3. App 运行 500 ms 后确认健康，元数据转 `state=IDLE`、`active_version=1`、
   `active_size=20284`、`active_crc=0x32EC9BA9`、`min_version=1`。
4. 从 0x08010000 读回 20284 字节，SHA256 `654eb54cd009d24dc136b5424b5a7e0c1a306195c9de4e8675d939eadb960ab1`，
   与构建产物逐字节相同。
5. 做了两遍（version 1 与 version 2），第二遍的安装阶段耗时 8.13 s。

## 文件

| 文件 | 内容 |
|---|---|
| `internal-64k-before-loader.bin` | 烧 loader 前内部 Flash 前 64 KiB 备份（65536 B） |
| `loader-boot.bin` | loader 首次启动的串口输出（520 B） |
| `reset-console.bin` / `.txt` | 第一遍：`0x11` 之后 1043 字节原始输出，含安装与跳转 |
| `stage-d.json`（上一级目录） | 第一遍每一步的请求/响应/耗时 |
| `installed-app.bin` | 第一遍后从 0x08010000 读回的 20284 B |
| `reset-idle-boot.txt` | 第一遍后再复位：0.06 s 选择、0.14 s App，无重装 |
| `second-upgrade/stage-d.json` | 第二遍（v2）全部步骤与串口时间线 |
| `second-upgrade/reset-console.txt` | 第二遍复位后的输出 |
| `final-boot-after-v2.txt` | v2 之后复位：0.08 s 选择、0.14 s App |
| `eeprom-dump.txt` / `eeprom-snapshot.bin` | 元数据仍是 TRIAL(seq 9)+IDLE(seq 10) 时的原始 256 B |
| `eeprom-write-probe.txt` | 写入前后对照：seq 12→13，`eeprom_crc32` 不变 |
| `eeprom-after-save.bin` / `eeprom-seq13-14.bin` | 两次快照，用于证明整块 CRC 是常数 |
| `eeprom-bus-recover.txt` | 一上电就读成功的那次（未触发恢复分支） |
| `metadata-wipe.txt` | 清零两份记录，模拟空片 |
| `eeprom-read-compare.txt` | 256 B 一次读失败的那次（`EEPROM read failed`） |

## 关键数字

| 项 | 值 |
|---|---|
| `0x34` 擦 1 MiB 外部槽 | 13.78 s |
| `0x36` × 40 帧（510 B） | 每帧 0.31–0.34 s |
| `0x37` 验签 20380 B | 0.22 s |
| `0x11` 后选择目标 | 0.06 s |
| 擦 960 KiB 内部 + 装 20284 B | 8.13 s |
| 复位到 App 首行 | 8.27 s |
| 已确认后复位到 App 首行 | 0.14 s |

## 不成立的说法

- **"`eeprom_crc32` 变了说明 EEPROM 内容变了"**：该值是结构指纹
  （`CRC32(m ‖ CRC32(m))` 为常数），四种不同元数据状态都得到 `0x316AB2D3`。
  2026-11-02 记的"`0x29058C73` 与上次一致"不构成内容一致的证据。
- **"读到了陈旧快照"**：曾经据此把 256 B 一次读改成分块读，实测分块同样会失败，已回退。
- **"安装后能自动发起下一次升级"**：元数据回到 IDLE 后每次复位都直接跳 App，App 没有 UDS，
  第二次升级靠清零元数据模拟空片才做成。
