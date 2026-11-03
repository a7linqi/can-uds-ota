# 升级包签名校验（L4）

适用版本：v0.13。本页说明签名校验的目标、设计、密钥、格式、验证流程与边界；本轮结果见 [核心复核记录](CORE_REVIEW_20261102.md)。

## 1. 为什么需要签名

工程原本只有 **CRC32 + 版本策略**。CRC32 是公开算法，任何人重算一遍就能让篡改后的固件"看起来完好"，因此只能发现**意外损坏**，不能发现**恶意篡改**。

签名校验用**非对称密钥**补上这个缺口：

- **私钥**只在 PC 打包端，用于签名（"盖章"）；绝不进入 MCU。
- **公钥**硬编码进 Bootloader，用于验证（"验章"）。
- 攻击者能拿到固件和公钥，但没有私钥，无法伪造出能通过验证的签名。

## 2. 算法：Ed25519

选 Ed25519（EdDSA over Curve25519，RFC 8032，SHA-512）而不是 ECDSA / RSA，理由：

- 公钥 32 字节、签名 64 字节，小；
- 本工程采用流式验签，MCU 耗时仍待实测；
- **验证端不需要随机数**（MCU 无 TRNG 也不影响安全）；
- 使用可审阅的公开实现，相关许可证随源码保留。

## 3. 密钥

| 项 | 位置 | 说明 |
|---|---|---|
| 私钥 seed | PC 打包工具（`tools/make_image.py` 的 `--sign` / `--seed`） | 绝不入固件、不随包分发 |
| 公钥 | `include/ota/pubkey.h`（硬编码） | Bootloader 用它验证 |

**当前公钥来自演示 seed `00 01 02 ... 1f`，任何人可复现，只提供"篡改检测"演示价值，不构成真实权威。** 正式使用前必须换一套新 seed，替换 `pubkey.h` 的 32 字节并重新构建固件。

## 4. 镜像格式

IMG2 头保持不变（32 字节，8 个 32 位小端字段），新增两个约定：

- `flags` bit0 = `IMAGE_FLAG_SIGNED`：表示"带签名"；
- 签名追加在 payload 之后：

```
[32B 头][payload (image_size 字节)][64B 签名]
```

签名覆盖 **32 字节头 + payload**。`header_crc`（头内 CRC）在签名前已 finalize，签名覆盖的是含正确 `header_crc` 的头。

`OTA_REQUIRE_SIGNATURE`（默认 1）开启后，**无签名镜像一律拒绝**（flags 不带 `IMAGE_FLAG_SIGNED` 或签名无效都失败）。

## 5. 验证流程

`image_validate`（下载区）在安装前按序检查：

1. `image_read_header`：magic / version / size / load_address / flags / header_crc；
2. `validate_payload`：payload CRC32 + 向量表范围对齐；
3. 若带签名：`image_signature_verify`（`src/core/signature.c`）做 Ed25519 验证。

关键实现：验证**流式**进行（`sha512_init/update/final` 分块喂 `R ‖ 公钥 ‖ 头 ‖ payload`），镜像 1 MB 也无需一次性载入 RAM——payload 通过 `flash_port_t` 分块读 NOR。最后 `sc_reduce` + `ge_double_scalarmult_vartime` 检查 `R + h·A == S·B`，并用常量时间比较。

所有入参在任何读操作**之前**校验：签名指针非空、偏移只能是下载区或备份区、`8 ≤ image_size ≤ IMAGE_MAX_PAYLOAD`、且标量 `S` 必须**规范**（`S < L`，见 [RFC 8032 §5.1.7、§8.4](https://www.rfc-editor.org/rfc/rfc8032.html)）。旧实现只检查高三位，会同时接受 `S` 和 `S+L`；其中 `S+L` 不符合标准编码要求。这不是对另一份固件生成有效签名。

**备份区例外**：`create_backup` 生成的备份镜像只有 CRC（`image_validate_backup`，无签名）。因为备份内容是"已通过签名验证安装的内部 App"的副本，恢复时 MCU 也没有私钥重新签名。

### 三条验收路径的接入点（2026-11-02 复核）

签名不是"加了个函数没人调用"，三处入口都会触发：

| 路径 | 入口 | 验签点 |
|---|---|---|
| 下载 | UDS `0x34 / 0x36 / 0x37` | `0x37` 先核对 `32 + image_size + (signed ? 64 : 0) == 下载总量`，再调 `image_validate`（含签名）；通过后才置 `READY` |
| 安装 | `boot_select` 的 `READY` / `INSTALLING` | `pending_ok()` 调 `image_validate`（含签名）；`install()` 只搬 `image_size` 字节，签名不会被写进内部 Flash |
| 恢复 | `boot_select` 的 `RESTORING` | `backup_ok()` 用 `image_validate_backup`（只 CRC） |

`0x36` 传输阶段只写不验是有意的：流式校验在 `0x37` 一次做完，避免半包误判。

**备份例外的边界**：`image_validate_backup` 只接受 `BACKUP_OFFSET`，且要求 `flags == 0`，其余偏移在读任何字节前就返回失败。因此"备份区免签"只对真正的备份槽开放，不能被拿去验下载区，也就不会变成整体绕过签名的后门。

## 6. 依赖

- `third_party/ed25519/`：orlp/ed25519（标准 Ed25519 / SHA-512），Zlib 许可；`sha512` 来自 LibTomCrypt（public domain）。仅使用 verify/sign/keypair，详见 `third_party/ed25519/NOTICE.md`。
- `tools/ed25519.py`：纯 Python Ed25519（RFC 8032，仅标准库 hashlib），用于 PC 端签名与密钥生成。

## 7. 已验证 vs 未验证

| 项 | 结果 |
|---|---|
| RFC 8032 测试向量（Python TEST 1 / TEST 2） | 通过，纳入 22 项工具测试 |
| Python 包 → C 验证（交叉） | 8 组：有效包接受；坏头、坏数据、坏签名、非规范 S、无签名、错误密钥、截断签名拒绝 |
| 主机 C 核心回归 | 3138 断言 / 420 断电场景，0 失败（ZG、ZE 各一遍） |
| 篡改 payload / 篡改签名 / 无签名 | 均被拒绝 |
| **三条路径全链路**（`0x37` → 安装 → 恢复） | 通过：重算 CRC 后的 payload 篡改、伪造版本号、错误密钥签名、签名翻位、无签名容器均被拒绝，且不改变运行中的 App 或启动区 |
| **备份例外不可绕过** | 通过：`image_validate_backup` 接受备份槽的无签名容器，但拒绝下载区与任何带 `flags` 的容器 |
| **非规范 S（`S+L`）** | 被拒绝（规范标量检查） |
| Cortex-M4 编译链接（GNU 四目标） | 通过，均在 64 KiB 引导区 |
| 实机验证耗时 / 真板刷写 | **未做** |

## 8. 边界（不要夸大）

- verify 路径使用 `*_vartime` 点运算；签名与公钥均为公开数据、验证不涉私钥，固件签名场景可接受。**量产抗侧信道安全启动应换 constant-time 实现或硬件加速**。
- 公钥硬编码、EEPROM 不提供防篡改版本计数，仍**不是安全启动**。
- 演示 seed 非密码学秘密；这是"篡改检测"，不是"真实性权威"。
