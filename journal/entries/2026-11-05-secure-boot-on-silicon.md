# 给 App 区末尾装一块签名凭证，然后雇一个攻击者去打它

- 日期：2026-11-05
- 类型：功能（安全启动）+ 实机验证
- 关键词：`secure-boot` `ed25519` `ticket` `crc32` `rollback` `boot`
- 验证等级：主机模型 / 目标构建 / 实机（三者都做了）

## 1. 一句话

在这之前 loader 只看 CRC32 就跳转，所以有 ST-Link 的人写一个自洽镜像、把元数据里的 `active_crc`
改对，板子就跑了。现在启动前必须验签，而攻击者能重算的每一个 CRC 都帮不上他。

## 2. 背景与约束

阶段 D 之后项目能完整升级、能回滚，但签名只保护**NOR 里的包**。包在安装时被验证、被解压进内部
Flash，之后 loader 每次启动只对内部 App 做 CRC32。

缺口很具体：CRC32 不是真实性的度量。它可以被任何能写 Flash 的人重算。

约束：
- 用户不在现场（远程操作），所以任何需要按键、拔插、断电的验证都做不了。
- 不能破坏已经验证过的恢复路径（TRIAL 回滚、READY 重建备份）。
- 不能让元数据记录变长（64 B 是 EEPROM 8 字节页 × 8 的整数倍，改长度要动双副本事务）。

## 3. 现象

引入前，安全启动在文档里的状态是「无」。更准确地说，是**有签名但只在包上**：
`0x37` 验签、安装时再用 `image_validate()` 验一遍，然后就把「已验证」这件事忘掉了。
下一次复位，loader 面对 0x08010000 只有 CRC32 可用。

## 4. 排查过程

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 凭证放哪最省事 | 比较 NOR 独立扇区 / EEPROM 剩余 128 B / App 区尾部 | 选 App 区尾部：一次擦除同时带走镜像和证明 |
| 2 | 备份能不能继续只验 CRC | 读 `image_validate_backup()` 的注释 | 注释说「设备没有私钥，恢复时没法重签」——这句话凭证出现后不成立 |
| 3 | 新用例真的能区分 CRC 与签名吗 | 去掉验签跑红（red probe） | **只有 1 条红，而且不是我以为的那条** |
| 4 | 为什么「改 payload」这条用例不红 | 打印 `boot_select()` 的返回值 | 它被凭证头里**旧的** `payload_crc` 拦住了，签名根本没机会参与 |
| 5 | 那真实的攻击者会怎么做 | 重写用例：同时改凭证头 CRC32 和元数据 | 现在 red probe 下有 2 条红，才是真的验签断言 |
| 6 | 恢复时为什么装不上同版本 | 看 `0x37` 返回 | NRC `0x31`：`h.version<=m.active_version`，版本策略正常工作 |

**转折点**：第 4 步。我原本以为「改 payload + 改元数据 CRC」就是攻击者能做到的全部，
但它仍然被一个我没想到的字段挡住。真正需要构造的是「**把每一个能重算的校验和都改对**」——
少了这一步，测试是在验证字段比对，而不是在验证签名。

## 5. 根因

「验证过的结论」在安装完成的那一刻被丢掉了。包验签的结果没有随镜像一起落到内部 Flash，
于是下一次启动只能退回 CRC32，而 CRC32 对伪造无感。

## 6. 修复

- `include/ota/flash_layout.h`
  - 新增 `APP_TICKET_SIZE 96`、`APP_MAX_SIZE (APP_SIZE - 96)`、`APP_TICKET_OFFSET`
  - `IMAGE_MAX_PAYLOAD` 从 `APP_SIZE` 改成 `APP_MAX_SIZE`
  - 编译期断言凭证偏移四字节对齐（内部 Flash 按字编程）
- `src/core/signature.c` / `include/ota/signature.h`
  - 新增 `image_signature_verify_split()`：头和 payload 不连续时的验签
  - 原 `image_signature_verify()` 改成「读头 → 转调 split 版本」，逻辑只有一份
- `src/core/image.c` / `include/ota/image.h`
  - 新增 `image_ticket_read()` / `image_ticket_write()`（只搬 96 字节，写后读回比对）
  - `image_validate_backup()` 改为**必须签名并验签**
- `src/core/boot.c`
  - `signed_image_ok()`：CRC → 凭证存在 → 凭证头字段与 metadata 逐项相等 → 验签
  - `active_ok` / `probe_active` / TRIAL 分支 / `boot_confirm` 全部改用它
  - `create_backup()` 把当前凭证的签名抄进备份容器（备份 = 签名重放）
  - `install()` **先读凭证再擦**：源容器不是签名包就不擦，避免「擦掉好 App 装进无法担保的镜像」
  - 凭证最后写，且在 payload 读回校验之后
- `include/ota/boot.h` / `src/core/boot.c`
  - `boot_context_t` 加 `last_reason`，六种拒绝原因
- `firmware/bootloader/main.c`：把原因打成一句人话
- `tools/make_image.py`、`tools/pc/uds_client.py`：容量上限减 96

**放弃的方案**：把凭证放 NOR 的独立扇区。理由是需要再发明一套「双副本 + 序号 + CRC + commit 末写」
的事务，而这套事务的每一条不变量都得跟安装事务保持同步；App 区尾部不需要任何新机制。

**放弃的方案二**：备份继续只验 CRC。这会留下唯一一条把未验证代码写进内部 Flash 的路，
等于安全启动在回滚路径上失效。

## 7. 验证

**主机模型**（TinyCC，两种容量）：

```
python run_tests.py --cc <tcc> --in-memory --capacity 1024
python run_tests.py --cc <tcc> --in-memory --capacity 512
```

| 项 | 期望 | 实际 |
|---|---|---|
| ZG / ZE 断言 | 全过 | **3502 / 0** ×2，429 断电场景 |
| `secure_boot_paths()` 5 个拒绝场景 | 全部 LOADER 且原因正确 | 一致 |
| red probe：删掉验签行 | 用例 1、2 变红 | 746/770 两行 FAIL，其余 3495 通过 |

**目标构建**：loader 48692 B、app 44824 B，GNU Arm GCC 链接通过。

**实机**（COM9，UART 台架）：

| 场景 | 串口原文 |
|---|---|
| v13 镜像 CRC 完好、无凭证 | `installed app has no signed ticket` |
| 伪造镜像、CRC 全改对 | `ticket signature does not verify` |
| 合法签名包 | `handing off to the app at 08010000` |

伪造实验由临时固件 `build-temp/stage-d/attack_main.c` 完成：翻转 `payload[size/2]`、
重算 payload CRC（`0xD6B888DB` → `0xCD8D35C1`）、改凭证头并重算头 CRC（`0xDFA147D8`）、
写回 EEPROM。烧回真 loader 后复位即得到第二行判决。

未验证：回滚路径在「备份已带签名」这个新前提下没有单独做实机演练（主机模型有 79 个断电切点）。
Bootloader 自身不验签——能改写 0x08000000 的人不受任何约束。

## 8. 学到的东西

1. **「改内容 + 改 CRC」不是攻击者能做的最强操作**。他还能改凭证头、重算头 CRC、改元数据。
   少改一样，测试就只证明了字段比对有效，没证明签名有效——而这两件事在被攻破时的差别是零。
2. **red probe 必须看着它红**。我写了五条安全启动断言，第一次只有一条会红；如果直接收工，
   另外四条就是装饰。
3. 一个存储位置的取舍可以消掉一整类缺陷：凭证和镜像同擦同写，就不可能出现「旧凭证配新镜像」。
4. 一句日志值一次板子会话。`staying in the loader` 对应六种情况，加原因之前每次都要猜。

## 9. 简历表述

> 为 STM32 Bootloader 设计并实现安全启动：在 App 区尾部放置 96 字节签名凭证（容器头 + Ed25519 签名），
> 启动前先比对元数据字段再验签，使「CRC32 全部自洽的伪造镜像」无法执行；用自建攻击固件在实机上
> 完成对抗验证（篡改 payload、重算 payload CRC 与头 CRC、改写 EEPROM 元数据），loader 以
> `ticket signature does not verify` 拒绝启动，正常签名包仍可在 0.34 s 验签后跳转。

## 10. 边界与下一步

- 演示密钥不是生产密钥；换真密钥只需替换 `include/ota/pubkey.h` 并重签。
- Bootloader 自身无验签，信任锚在「不写 0x08000000」这个假设上；真正的锚应是 RDP + 关闭 SWD。
- 备份签名重放只在主机模型里被 79 个断电切点覆盖过，实机回滚尚未重跑。
- 下一步候选：硬件 IWDG、回滚路径的实机复验、真实 CAN 总线。
