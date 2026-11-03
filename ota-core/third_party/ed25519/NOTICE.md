# third_party/ed25519

## 来源与用途

- 来源：https://github.com/orlp/ed25519 （master 分支，下载于 2026-11-01）
- 用途：升级包 **Ed25519 签名验证**（L4 签名校验）。本工程仅使用 `ed25519_verify`、`ed25519_sign`、`ed25519_create_keypair`；未引入 `key_exchange` / `add_scalar` / `create_seed` 等无关能力。
- 算法：标准 Ed25519（RFC 8032，SHA-512），与通用生态（openssl、RFC 测试向量）互通。

## 许可证

- 主实现（ed25519 接口及 fe/ge/sc/keypair/sign/verify）：**Zlib License**，宽松许可，允许任意用途（含商用）与再分发；须保留版权声明、不得冒称原创、修改版本须明确标注。
- `sha512.h` / `sha512.c`：来自 **LibTomCrypt**（public domain，Tom St Denis）。

## 文件清单

- `ed25519.h`：签名 / 验证 / 密钥对接口
- `keypair.c` / `sign.c` / `verify.c`：密钥对生成、签名、验证
- `sha512.h` / `sha512.c`：SHA-512 哈希（LibTomCrypt）
- `fe.*` / `ge.*` / `sc.*` / `fixedint.h` / `precomp_data.h`：域运算、点运算、标量运算与预计算表

## 验证与边界

- 已通过 **RFC 8032 第 7.1 节 TEST 1 / TEST 2** 测试向量自检：密钥对推导、签名验证、篡改签名拒绝、签名回环全部通过（用主机 TinyCC 编译运行）。
- 边界说明：本实现的 verify 路径使用 `*_vartime` 点运算。签名与公钥均为公开数据、验证不涉及私钥，vartime 对固件签名验证场景可接受；若将来用于对侧信道敏感的量产安全启动，应替换为 constant-time 验证实现（例如 monocypher 的 EdDSA 或硬件加速）。
