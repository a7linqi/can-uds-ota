<!-- 证据原件在 stages/board-lab/evidence/secure-boot/，该目录被 .gitignore 排除。
     这里保留可复核的清单、原始串口输出与结论。 -->

# 安全启动：让「CRC 全对但没人签过」的镜像也起不来

日期：2026-11-05
板子：STM32F407ZGT6 + W25Q128（16 MiB）+ AT24C02，UART 台架 115200（COM9）
固件：`build-temp/stage-d/loader/loader.bin`（48692 B，sha256 `eaf660e9297b31e4…`）
应用：`build-temp/stage-d/app/app.bin`（44824 B，sha256 `47f604c135166a0d…`）

## 一句话

在这之前，loader 只看 CRC32 就跳转，所以一个拿着 ST-Link 的人可以写一个自洽镜像、
把元数据里的 `active_crc` 改对，板子就跑了。现在 App 区末尾有 96 字节的**签名凭证**，
启动前必须验签通过；拿不到私钥的人改得动每一个字节，也改不动这个结论。

## 设计：为什么凭证放在 App 区末尾

`include/ota/flash_layout.h`：

```c
#define APP_TICKET_SIZE       96u   /* 32B 容器头 + 64B Ed25519 签名 */
#define APP_MAX_SIZE          (APP_SIZE - APP_TICKET_SIZE)
#define APP_TICKET_OFFSET     (APP_OFFSET + APP_MAX_SIZE)
```

三个方案里选了这个：

| 方案 | 为什么不选 |
|---|---|
| 凭证存 NOR 的独立扇区 | 需要新一套双副本 + 序号 + CRC 的事务，而且下载槽被擦时会牵连 |
| 凭证存在 EEPROM（剩 128 B） | 只剩一份，做不了双副本；还多一种存储格式要跟安装事务保持同步 |
| **凭证在 App 区尾部 96 B** | **一次擦除同时带走镜像和它的证明**，不可能出现「旧凭证配新镜像」 |

代价是 App 最多只能用 `APP_SIZE - 96`（1 MiB 器件上 960 KiB - 96 B）。实际镜像是 44 KiB，
这不是容量决策。三处边界都改了口径：`IMAGE_MAX_PAYLOAD`、`tools/make_image.py`、
`tools/pc/uds_client.py`，并各有一条断言钉住。

凭证内容 = 容器头 32 B（就是当初签的那样）+ 签名 64 B。它是**不可信输入**：头在签名覆盖的
消息里，改头只会让签名失败。启动时先比对 metadata 也知道的那几个字段（version / size / crc /
load_address），再验签；`image_size` 必须等于 `active_size`，否则「签了短前缀、跑了长镜像」。

## 连带改掉的一件事：备份也必须带签名

旧设计里备份槽故意不签名——理由是设备没有私钥，恢复时没法重新签名，所以
`image_validate_backup()` 只验 CRC、且要求 `flags==0`。

这个理由在凭证出现后失效了：**安装时凭证里已经有一份签名**，`create_backup()` 把当前
App 的凭证签名抄进备份容器，恢复时是**重放**一份已有证明，不是**制造**证明。

所以现在 `image_validate_backup()` 要求 `flags==IMAGE_FLAG_SIGNED` 并验签。留着「无签名备份也接受」
会成为一个绕过口：攻击者造一个备份 → 触发回滚 → 安全启动永远看不到。

## 实测：三段判决，同一块板子

### 1. CRC 完好的旧镜像，没有凭证 → 拒绝

烧入新 loader（此前的 v13 App 还在内部 Flash 里，CRC 完好、复位前还在跑）：

```
loader: selecting boot target
loader: installed app has no signed ticket
loader: staying in the loader
```

### 2. 伪造镜像，所有能重算的 CRC 都改对 → 拒绝

用一个临时固件（`build-temp/stage-d/attack_main.c`，占 loader 位置）扮演攻击者，
它做完了攻击者能做的全部事情：

| 步骤 | 值 |
|---|---|
| 翻转 payload 中间一个字节 | `^(size/2)` ^= `0x5A` |
| 重算 payload CRC32 | `0xCD8D35C1`（原来是 `0xD6B888DB`） |
| 改凭证头的 `payload_crc`，重算头 CRC32 | `0xDFA147D8` |
| 写回 EEPROM 的 `active_crc` | 与上面一致 |

烧回真 loader、复位：

```
loader: selecting boot target
loader: ticket signature does not verify
loader: staying in the loader
```

**这是整件事的关键**：CRC32、头部 CRC32、元数据三者全部自洽，
`image_validate_app()` 返回 VALID，唯一对不上的只有签名。

### 3. 正常签名包 → 跳转

```
loader: selecting boot target
loader: handing off to the app at 08010000
...
app at 08010000: awaiting real health checks
```

## 为什么加了拒绝原因

原来只有一句 `staying in the loader`，而它对应六种完全不同的情况。没有调试器时，
「板子不肯启动」和「板子拒绝了一个伪造镜像」在串口上一模一样，只能靠猜。
现在 `boot_select()` 把原因写进 `boot_context_t.last_reason`，loader 翻成一句人话：

| 原因 | loader 输出 |
|---|---|
| `BOOT_REASON_NO_METADATA` | `no usable metadata record` |
| `BOOT_REASON_IMAGE_DAMAGED` | `installed app failed its CRC or vectors` |
| `BOOT_REASON_NO_TICKET` | `installed app has no signed ticket` |
| `BOOT_REASON_TICKET_MISMATCH` | `ticket describes a different image` |
| `BOOT_REASON_SIGNATURE` | `ticket signature does not verify` |
| `BOOT_REASON_UNREADABLE` | `internal flash could not be read` |

## 证据文件

| 文件 | 内容 |
|---|---|
| `boot-no-ticket.txt` | 场景 1 的串口原始输出（526 B） |
| `install-v14/stage-d.json` | 从 loader 走完整 OTA 装 v14，含每步耗时 |
| `install-v14/reset-console.bin` | 安装 → TRIAL → 跳转的串口时间线 |
| `attack-run.txt` | 攻击固件输出：`forged_payload_crc` / `forged_header_crc` |
| `refuse-forged.txt` | 场景 2 的串口原始输出 |
| `recover-v15/stage-d.json` | 恢复安装（v14 被版本策略拒，改 v15 成功） |

## 关键数字

| 项 | 值 |
|---|---|
| loader | 48692 B（安全启动前 47524 B，+1168；含验签与原因日志） |
| app | 44824 B（前 44480 B，+344） |
| `0x37` 验签 44920 B | 0.34 s |
| `0x34` 擦 1 MiB 槽 | 13.7 s |
| `0x36` 每帧 510 B | 0.33 s |
| 安装到跳转 | 约 8 s |
| 伪造后的 payload CRC | `0xD6B888DB` → `0xCD8D35C1` |

## 不成立的说法

- **「CRC32 能防篡改」**：它能发现传输损坏，不能发现伪造。场景 2 里 CRC 全对。
- **「备份可以只验 CRC，因为设备没有私钥」**：凭证出现后这句话过时了，备份现在是签名重放。
  留着旧逻辑等于给回滚开一扇后门。
- **「`staying in the loader` 能说明问题」**：不能。加原因之前，六种情况共用一句话。

## 边界

- 演示公钥对应 seed `00 01 … 1f`，私钥在 PC 上、也在 `tools/ed25519.py` 里可复现。
  它提供**篡改检测**，不提供真实授权。换真密钥只需替换 `include/ota/pubkey.h` 的 32 字节并重签。
- 只验 App 镜像。**Bootloader 自身没有验签**——能改写 0x08000000 的人仍然可以为所欲为。
  这是安全启动的经典边界：信任必须锚在硬件（RDP / 安全启动 / 写保护）上，本项目的锚是「不去写它」。
- 场景 2 里攻击者能改 EEPROM 和内部 Flash，是因为它有 ST-Link。真实威胁模型下
  应该配合 RDP Level 1+ 和 SWD 关闭。
- 回滚路径（备份恢复后仍能启动）本次没有单独跑一遍；主机模型有 79 个断电切点覆盖，
  实机只在之前的 TRIAL 回滚实验里验证过「回滚后逐字节相同」，但那次备份还没有签名。
  这一条是**未实测**。
