# 修好"App 坏了就再也装不进新包"：READY 分支不该被 active 校验拦住

- 日期：2026-11-05
- 类型：缺陷
- 关键词：`boot` `metadata` `sane` `image_validate_app` `ota`
- 验证等级：主机模型、实机

## 1. 一句话

`active_size!=0` 但内部 App 已经损坏时，`boot_select()` 在 READY 分支里先做备份校验、失败就 `goto done`，于是合法的待安装包永远装不进去——这正是"最需要恢复"的场景被卡死。

## 2. 背景与约束

约束是不能让"读不到"和"内容坏"混为一谈：内部 Flash 读失败时不能当作"镜像空片"继续擦写，否则会把一次通信故障升级成数据丢失。

## 3. 现象

把内部 App 的一个字改掉（`0x08011000` 处 `4F` → `4E`，整镜像 CRC32 由 `0x32EC9BA9` 变成 `0x137FF3F6`），外部下载槽里放一个完全合法的签名包，复位：

- 串口停在 `loader: staying in the loader`；
- 元数据仍是 `state=READY`、`pending_version` 有效；
- 反复复位结果相同，包永远不装。

## 4. 排查过程

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 包不合法 | 用 `tools/make_image.py` 重新签一个并核对 CRC | 包合法，签名校验通过 |
| 2 | READY 分支根本没进 | 在 `boot_select()` 加 `probe=%d` 打印 | `probe=-1`，进入了 READY 分支但被 `create_backup()` 挡住 |
| 3 | 先清 active 再说 | 写 `next.active_version=0`、`active_size=0`、`active_crc=0` 后 `metadata_save()` | **仍然停在 loader**：`sane()` 拒收 |
| 4 | `sane()` 为什么拒 | 读 `metadata.c` 的语义校验 | `active_size==0` 时要求 `min_version` 也必须为 0 |

**转折点**：第 4 步。`min_version` 和 `active_version` 是绑定的，只清 active 三个字段会造出一条自相矛盾的记录，`metadata_save()` 直接拒绝，于是状态机原地打转。调试打印第一次只给了 `probe=%d`，前两次误判（`metadata_active_crc()` 未定义、`active_ok_pub` 未定义）都是因为打印给的信息不够。

## 5. 根因

READY 分支把"要不要备份旧镜像"和"能不能安装新镜像"写成了同一条前置条件：`create_backup()` 内部先校验 active 镜像，只要 active 不是 VALID 就返回非 0，调用处 `goto done` 退出。结果是"active 坏了"反而变成"不许修"。

## 6. 修复

- 文件：`include/ota/image.h`（返回值改成三态：`IMAGE_APP_VALID` / `IMAGE_APP_CORRUPT` / `IMAGE_APP_UNREADABLE`）
- 文件：`src/core/image.c`（`validate_payload()` 里读失败返回 `UNREADABLE`，向量表或 CRC 不对返回 `CORRUPT`）
- 文件：`src/core/boot.c`（新增 `probe_active()`；`UNREADABLE` 仍然停住，`CORRUPT` 时把 active 四个字段——含 `min_version`——一起清零并落盘，然后照常进 `INSTALLING`）
- 备选方案与放弃原因：把"备份失败"改成可忽略——放弃，那会让`RESTORING`分支在真的需要旧镜像时没有旧镜像；必须区分"没有旧镜像可用"和"读不到存储"。

## 7. 验证

```
python run_tests.py --cc <tcc.exe> --capacity 1024 --in-memory
```

| 项 | 期望 | 实际 |
|---|---|---|
| 主机模型两容量 | 全过 | 3421 断言，0 失败（新增 `broken_active_with_pending()`） |
| 实机：破坏一个字后复位 | 不再卡死 | 打印 `loader: staying in the loader`，元数据被清成空片 |
| 实机：随后装 v3 | 安装并跳转 | `0x37`→READY→`0x11`→loader 擦 960 KiB、安装、TRIAL、跳 `0x08010000`，App 首行打印 |
| 内部镜像读回 | 与构建产物同 | SHA256 一致 |

未验证：真实断电发生在安装过程中的情形（仍是模型 + 手工复位）。

## 8. 学到的东西

1. 恢复路径的前置条件不能依赖"待恢复对象是好的"——那等于只在不需要恢复的时候才允许恢复。
2. "读不到"和"内容坏"必须分成两个返回值，否则要么误删数据，要么永远不修。
3. 一次调试打印要给足上下文（进没进分支、判据值、决策结果），否则会用三次误判换一个 `probe=%d`。

## 9. 简历表述

> 把 Bootloader 的镜像校验从二态改成三态（有效/内容坏/读不到），使"内部 App 已损坏"从"拒绝安装"变成"清空 active 后照常安装"，并在实机上用破坏一个字复现再修复（`0x32EC9BA9` → `0x137FF3F6`）。

> 难点：只清 active 三个字段会被元数据语义校验拒收，`min_version` 必须同步清零。

## 10. 边界与未解决

- `min_version` 与 `active_version` 的绑定关系由 `sane()` 隐式维护，容易在别处再踩一次；没有把这条不变量写进头文件注释以外的检查。
- 断电模型覆盖 420 个场景，但物理断电仍未做。
