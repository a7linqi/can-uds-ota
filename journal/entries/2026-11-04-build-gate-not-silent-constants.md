# 那两个"沉默常量"不是缺陷，是编译期闸门

- 日期：2026-11-04
- 类型：决策（推翻旧判断）
- 关键词：`explorer_reference` `#error` `boot-handoff` `board-config`
- 验证等级：目标构建（语法层，`-fsyntax-only`，不产出 .o、不链接、不上板）

## 1. 一句话

`ota_user_force_loader() { return 1; }` 和 `ota_user_app_healthy() { return 0; }`
被我记成"两个沉默常量待提案"，实际它们是只读 bring-up 模式的安全默认值，
外面还套着一道 `#error` 闸门——真正的坑不在它们身上，而在"改动顺序"上。

## 2. 背景与约束

- 交接清单里长期挂着一条："`ota_user_force_loader()` 恒 1、`ota_user_app_healthy()` 恒 0，
  两个沉默常量未做提案"。
- 我的隐含判断是：这是没实现完的桩，会导致"App 起不来 / 一定回滚"，应该改。
- 约束：现在板子在跑 NOR 恢复演练，不能碰 SWD 和串口，只能做纯主机侧/构建侧核查。

## 3. 现象

`platform/stm32f407/board/explorer_reference/explorer_reference.c:195-196`：

```c
int ota_user_force_loader(void) { return 1; }
int ota_user_app_healthy(void) { return 0; }
```

单看这两行，语义是"永远强制进 loader + 永远认为 App 不健康"，
听起来像是整条 App 路径被焊死了。

## 4. 排查过程

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 这两个钩子在 boot 流程里被调用 | `grep -rn` 全仓库 | 只被 `board_hal_binding.c` 包了一层，实际调用点在 `bootloader/main.c:52` 和 `firmware/app/main.c:35,44` |
| 2 | 调用点无条件执行 | 读 `bootloader/main.c` 上下文 | 整块被 `#if OTA_ENABLE_BOOT_HANDOFF` 包着（第 51 行） |
| 3 | 也许某个 target 打开了 handoff | `grep OTA_ENABLE_BOOT_HANDOFF` 扫所有 `uvprojx` | **8 处定义，全部 =0**（app/bootloader/explorer_readonly × ZG/ZE × 两种传输） |
| 4 | 那想打开会怎样 | 读 `explorer_reference.h` 顶部 | 第 14-15 行有 `#error`：三个开关任意一个为 1 就编译失败 |
| 5 | 也许 `#error` 只是注释性质的 | 用 `arm-none-eabi-gcc -fsyntax-only` 实跑四种组合 | `0/0/0` 通过；`1/0/0`、`0/1/0`、`0/0/1` 全部 `#error`，定位 `explorer_reference.h:15:2` |

**转折点**：第 3 步——8 处 `OTA_ENABLE_BOOT_HANDOFF=0`。
一旦确认这两个钩子根本没被编译进去，"沉默常量"这个判断就站不住了：
它们不是"实现错了"，是"还没轮到它们上场"。

## 5. 根因

我对"常量返回值"做了局部语义判断，没有先确认**它是否在编译单元里**。
`explorer_reference` 这个板级实现的定位是"只读 bring-up"，
它用一道 `#error` 把三个更新开关全锁死，再用"方向安全"的默认值兜底：

- `force_loader()=1` → 即使有人绕过 `#error` 打开 handoff，也停在 loader，不会跳到空 App；
- `app_healthy()=0` → 即使 App 跑起来，也宁可多回滚一次，不会误确认坏镜像。

这是纵深防御，不是遗漏。

## 6. 修复

不改代码。改的是文档里的**顺序**：

- 文件：`board-docs/06-升级实现/真实升级-最后一段改动清单.md`
  - 第 1 节表格补一行：`explorer_reference.h:14-15` 的 `#error` 闸门；
  - 表格下补一段：说明这两个钩子是死代码 + 为什么默认值方向是安全的；
  - 第 3 节表格前补"顺序陷阱"：第 4 项不能直接把 `OTA_ENABLE_FLASHING` 改成 1，
    会当场被 `#error` 拦下；必须先换板级实现/新增 target。
- 新增可复跑证据：`build-temp/build-gate-probe/`（`probe.c` + 假 HAL 头 + `probe.cmd` + `README.md`）。

- 备选方案与放弃原因：
  - **直接把两个钩子改成 `return 0` / `return 1`**：在 handoff 关闭时毫无效果（死代码），
    打开 handoff 时又绕过了闸门，属于"看起来修好了"，放弃。
  - **删掉 `#error`**：等于把"只读模式"的门拆了，风险最大，放弃。

## 7. 验证

```bat
cd D:\Project\Can\build-temp\build-gate-probe
probe.cmd
```

| 项 | 期望 | 实际 |
|---|---|---|
| FLASHING=0 HANDOFF=0 WATCHDOG=0 | 编译通过 | COMPILES |
| FLASHING=1 | 被 `#error` 拦下 | BLOCKED，`explorer_reference.h:15:2` |
| HANDOFF=1 | 被 `#error` 拦下 | BLOCKED |
| WATCHDOG=1 | 被 `#error` 拦下 | BLOCKED |
| 所有 uvprojx 的 HANDOFF | 全 0 | 8 处全 0 |

未验证：没有真的改 `uvprojx` 去打开开关（那要动产品构建配置）；
`-fsyntax-only` 只证明预处理/语法层会停，不证明链接行为。

## 8. 学到的东西

1. **判断一个常量是不是"缺陷"，先确认它有没有被编译进去**。
   `#if` 关掉的代码里，任何返回值都无害；把它们列进"待修清单"是浪费。
2. **"看起来不合理的默认值"可能是刻意选的失败方向**。
   `app_healthy()=0` 的代价是多回滚一次，`=1` 的代价是把坏镜像确认成好的——
   后者不可接受，所以默认值只能往 0 偏。
3. **改动清单必须带顺序**。列了 8 项不等于可以按 1..8 做；
   第 4 项会撞 `#error`，第 5/6 项在第 4 项之前改了也没用。

## 9. 简历表述

> 排查板级 OTA 启动链时发现"永远进 Bootloader"并非实现遗漏，而是
> `#error` 构建闸门 + 方向安全默认值组成的纵深防御；用 `arm-none-eabi-gcc -fsyntax-only`
> 对 4 种开关组合实测定位闸门，并修正了升级改动清单里会导致编译失败的步骤顺序。

## 10. 边界与未解决

- 只做了语法层验证，没有链接、没有上板。
- `firmware/app/main.c` 的两处 `ota_bsp_app_healthy()` 调用**没有** `#if` 保护，
  意味着 App 一旦被跳转过去就会用 `=0` 的值。当前 App 从不运行（handoff=0），
  等真正开 handoff 时这两处要一起改，属于第 6 项的一部分。
- 没有评估 `force_loader()` 改成"读 BOOT 引脚"后的抖动/去抖需求——那是上板后的事。
