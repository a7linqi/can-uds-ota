# 把合法签名包装进内部 Flash 并跳转过去

- 日期：2026-11-05
- 类型：功能
- 关键词：`uds` `bootloader` `ed25519` `handoff` `stm32f407` `w25q128`
- 验证等级：主机模型（3385 断言 ×2 容量）、目标构建（64 位 GNU Arm GCC 链接）、**实机**

## 1. 一句话

前三个阶段都只用故意非法的包验证"拒绝"，这一轮第一次让设备收下真正的签名包，走完
`0x34 → 0x36 → 0x37 → 0x31 → 0x11`，由 loader 自己擦内部 Flash、安装、进 TRIAL、跳到
0x08010000，App 确认后元数据转 COMMITTED。

## 2. 背景与约束

- 项目卖点是"升级中断后仍能恢复"，但在此之前**从未真正安装过一次**。所有回滚/恢复逻辑
  都只在主机模型里跑过，"能装"这件事本身没有实物证据。
- 不能动的东西：`src/core/metadata.c` 的双副本 + 序号 + CRC + commit 末写；
  `src/core/` 不得引入 HAL；固定布局（Bootloader 前 64 KiB，App 固定 0x08010000）。
- 板级绑定里原本有一条硬拒绝：`#if OTA_ENABLE_BOOT_HANDOFF || OTA_ENABLE_WATCHDOG \n #error`。
  这条是 T4 故意留的，不能简单删掉，否则"误开"就没保护了。
- 板上内部 Flash 前 64 KiB 当时跑的是 explorer 双开镜像（诊断台，永不跳转）。

## 3. 现象

改造前，任何一次复位都停在诊断循环：

```
loader: selecting boot target
loader: staying in the loader
```

因为元数据停在 `UPDATE_DOWNLOADING`（阶段 B 的副作用）而 `active_size=0`，
`boot_select` 找不到可跳转的 App——这是对的，但也就停在这里了。

改造后同一条链路（第二遍，version 2）的实测：

| 阶段 | 耗时 |
|---|---|
| `34 00 44 08010000 00004F9C` 擦 1 MiB 外部槽 | 13.78 s |
| `36` × 40 帧（510×39 + 490） | 13.2 s（每帧 0.31–0.34 s） |
| `37` 验签 20380 B | 0.22 s |
| `31 01 00 01` | 0.16 s |
| `11 01` → 复位，擦 960 KiB 内部 + 装 20284 B | **8.13 s** |
| App 打印首行 | 复位后 8.27 s |

复位后串口：

```
loader: selecting boot target
8.19  loader: handing off to the app at 08010000
8.23  ATK Explorer reference pins; update engine disabled
8.27  app at 08010000: awaiting real health checks
```

再复位一次：`loader: selecting` 0.08 s、`handing off` 0.08 s、`app at 08010000` 0.14 s，
**没有第二次安装**。

## 4. 排查过程

这一轮不是修 bug，是把"没法装"这件事一层层拆开。过程中踩的坑都是真坑。

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 缺的是 App 镜像 | 写 `firmware/linker/app_boot.ld`，链接到 0x08010000 | `make_image.py` 的向量校验通过，reset=0x0801447D |
| 2 | 板级绑定拦着 | 试着直接开 `OTA_ENABLE_BOOT_HANDOFF=1` | 命中 `#error`，正是 T4 留的门 |
| 3 | 加一个开关就行 | 新增 `HANDOFF_CAPABLE` / `STORAGE_TRUSTED` / `BOOT_ROLE` | 编译变体 8→14，逐个拒绝路径都有 |
| 4 | App 和 loader 用同一套开关 | 只给 App 开 STORAGE_TRUSTED、关 FLASHING | 编译通过；App 启动日志确认 "update engine still gated" |
| 5 | 打包 | `make_image.py --sign --version 1` | 20380 B，flags=1，payload_crc 0x32EC9BA9 |
| 6 | 第一次全流程 | 发 0x34…0x11 | `0x37`→`77`，`0x31`→`71`，复位后跳转成功 |
| 7 | App 到底确认了没有 | 复位再看 `eeprom_crc32` | 三次都是 0x316AB2D3，误判成"没写进 EEPROM" |
| 8 | 是不是 loader 没写 | 烧诊断固件读 `22 02 00` | `state=IDLE`、`active_version=1`、`active_crc=0x32EC9BA9`——**写了** |
| 9 | 诊断字段本身不可信 | 直接 dump EEPROM 256 B | 两份记录 seq 9/10 与 12/13 都自洽，CRC 却相同 |
| 10 | 是不是自己算错 | 手写 CRC32 位运算复核 | 四种 seq 组合都算出 0x316AB2D3，翻转记录内 CRC 字节才变 |

**转折点**：第 9、10 步。主机上手写 CRC 与 zlib 一致、四组不同内容给出同一个值，说明不是
读写问题，而是这个字段天生没有判别力（见另一条 journal）。于是改用 `22 02 00` 的 40 字节
状态和"从 0x08010000 读回 20284 字节比 SHA256"作为证据，两条都硬。

第 6 步还有个隐性坑：**重新构建 loader 后 sha256 没变**（6070b719…），说明构建可复现，
否则"改了代码但板上是旧的"这种事故根本发现不了。

## 5. 根因

不是缺陷。原来"装不了"是三件事叠在一起：没有链接在 0x08010000 的镜像、板级绑定无条件
拒绝 handoff、角色 hook（`ota_user_force_loader` / `ota_user_app_healthy`）硬编码成诊断台行为。
三件事都解开，链条就通了。

## 6. 修复

- `firmware/linker/app_boot.ld`（新增）：App 链接布局，`ASSERT(ADDR(.isr_vector) == 0x08010000)`。
- `platform/stm32f407/board/explorer_reference/explorer_reference.h`：
  - `OTA_EXPLORER_STORAGE_TRUSTED`（默认 = `UPDATE_CAPABLE`）：把"存储可信"和"允许擦写"拆开，
    因为 App 要读元数据、要提交 TRIAL，却没有传输层接下载。
  - `OTA_EXPLORER_HANDOFF_CAPABLE`（默认 0）：handoff 的独立开关，三扇门任一单开都 `#error`。
  - `OTA_EXPLORER_BOOT_ROLE`（0 诊断 / 1 loader / 2 app）。
- `explorer_reference.c`：两个 hook 由角色宏决定；`storage_verified` 改看 `STORAGE_TRUSTED`。
- `firmware/bootloader/main.c`：`boot_select` 前后加日志。第一次安装会静默 8 秒，
  没有这两行会把正常安装当成死机。
- `firmware/app/main.c`：补 `ota_bootloader_can_rx` 空实现（板级绑定要求这个契约符号）。
- `run_explorer_checks.py`：新增 6 个编译变体，逐个证明单开任一门都被拒。
- `build-temp/build_stage_d.py`：一次性构建 loader / app（外加 dump / wipe 两个诊断变体），
  不在正式目标里。

- 备选方案与放弃原因：**让 App 也开 `UPDATE_CAPABLE`**，省掉 `STORAGE_TRUSTED` 这个宏。
  放弃了，因为那等于对 App 说"你可以擦写外部 Flash"，而它根本没有接 UDS；语义上不诚实，
  也削弱了 T4 那道门的意义。
- 另一个放弃的方案：**给 App 加 UDS 服务**，让它自己接下载。范围太大，而且 loader 存在的
  理由就是 App 坏了也能升级。

## 7. 验证

```powershell
python verify_project.py --cc <tcc路径> --arm-bin <ARMCLANG> --dfp <DFP> --cmsis <CMSIS> --gcc <gcc>
python build-temp/build_stage_d.py
python tools/make_image.py build-temp/stage-d/app/app.bin build-temp/stage-d/app_v1.img2 --version 1 --sign
python D:/Project/Can/stages/board-lab/host/uds_stage_d.py --port COM9 --image build-temp/stage-d/app_v1.img2 --out <证据目录>
```

| 项 | 期望 | 实际 |
|---|---|---|
| 核心断言（ZG / ZE） | 各 3385、0 失败 | 各 3385、0 失败 |
| 存储断电场景 | 420 | 420 |
| 四套 GNU 只读镜像 | 完整链接 | 完整链接 |
| `37` 对签名包 | `77` | `77`（0.22 s） |
| `31 01 00 01` | `71 01 00 01 00` | 一致 |
| 内部 Flash 读回 20284 B | = 构建产物 | SHA256 `654eb54c…` 相同 |
| 元数据终态 | IDLE / v1 / 20284 / 0x32EC9BA9 | 完全一致 |
| 第二次复位 | 直接跳转，不重装 | 0.08 s 选择、0.14 s App |
| 第二遍升级（v2） | 同上 | 同上，安装 8.13 s |

未验证：真实拔电中断（IWDG 与硬断电都没试）、真实 CAN 总线（USB-CAN 未到货）、
App 侧发起下一次升级的入口（不存在）。

## 8. 学到的东西

1. **"能装"和"装对了"是两件事。** 跳转成功只说明向量表和栈顶没写错；真正的判据是
   从 0x08010000 读回 20284 字节与构建产物比 SHA256。
2. **诊断字段也要被诊断。** `eeprom_crc32` 看着像内容指纹，实际是结构指纹；用它当证据
   差点让我把"成功"写成"失败"。
3. **静默的 8 秒会骗人。** 安装期间总线上没有任何流量，没有日志就只能猜。加两行 log 的成本
   远低于误判一次。
4. **假诊断比没诊断更糟。** 一度以为"读到了陈旧快照"，还照着这个判断改了产品读法；
   幸好先用主机模型算了 CRC 才没把错改进去。

## 9. 简历表述

> 在 STM32F407 + W25Q128 上打通 CAN/UDS 完整固件升级链：设备接收合法 Ed25519 签名包，
> 擦写 960 KiB 内部 Flash 安装并跳转，8.13 s 完成、镜像 SHA256 与构建产物逐字节一致，
> 元数据由 TRIAL 自动提交为 COMMITTED。

> 边界：真实拔电中断与物理 CAN 总线尚未验证；loader 与 App 的会话入口还没做。

## 10. 边界与未解决

- **没有从 App 回 loader 的入口**：元数据回到 IDLE 后每次复位都直接跳 App，而 App 不带 UDS，
  第二次升级没有起点。本轮靠清零元数据模拟空片才做了第二遍。
- **`active_size!=0` 但内部镜像损坏时，READY 永远前进不了**：`create_backup` 先 `active_ok`，
  失败就 `goto done`，即使待装的新包完全合法。旧版本既然没了，正确做法是跳过备份直接安装。
- EEPROM 6 次复位里 2 次读失败（`eeprom_status=1`），原因未定位；两次都安全地停在 loader。
- IWDG 与真实断电、物理 CAN、Keil 链接仍未做。
- `build-temp/build_stage_d.py` 里的 dump / wipe 变体是实验工具，不进正式目标。
