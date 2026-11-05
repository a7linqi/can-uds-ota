# 把三个已验证补丁合入产品仓库，回归从 3164 涨到 3328

- 日期：2026-11-05
- 类型：功能 / 决策
- 关键词：`isotp` `uds` `can-uart` `merge` `red-green`
- 验证等级：主机模型（TinyCC + GNU Arm GCC 只读链接），**无实机**

## 1. 一句话

把 11-04 打包但一直没合的三个补丁（保留 STmin、NRC 0x14、UART 半帧重同步）真正落进 `can-uds-ota-learning`，并让验证证据、文档、简历口径一起跟上。

## 2. 背景与约束

三个包在 `D:\Project\Can\build-temp\` 里躺了一天，各自都有四组合构建证明，但产品仓库 `src/` 一直是零改动。交接文档里写着「均未合入产品仓库」，而 `evidence/verification.json` 里是 3164 条断言 —— 只要合入，这两处立刻变成假话。

约束：
- 分工上维护端改 `src/` + `platform/`，本 harness 原本只改 `stages/board-lab/`、`board-docs/`、`build-temp/`、`journal/`；这次是用户明确要求「执行现在能做的」，才动学习仓库的产品代码。
- `src/core/metadata.c` 的双副本+序号+CRC+commit 末写不得简化；`src/core/` 不得引入 HAL。三个包都不碰这些。
- 改源码/测试必须保留仓库原有的 **CRLF** 行尾。
- 数字只用当场实测，分清主机模型 / 目标构建 / 实机。

## 3. 现象

合入前产品仓库干净（`6a50def`），`run_tests.py --capacity 1024 --in-memory` 为 **3164 / 0**（第一个包合入后变 3309 / 0）。三个包各自有 `.diff` 与 README，但合并顺序、冲突、行尾都没在真实仓库上验过。

## 4. 排查过程

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 打包期哈希可能已失效 | 对 `isotp.c` / `uds.c` / `can_uart.c` / `test_uds.c` 逐个 sha256 比对包内记录 | 4 个全部一致，可以安全 apply |
| 2 | 三包都要改 `test_main.c`，可能冲突 | 对两个 `git apply --check` 干跑 | 都通过；第一个包加的行不在同一 hunk，**打包期预言的冲突本次没发生** |
| 3 | 直接 apply 完再跑测试，无法证明测试有效 | 改成「每包先只加测试跑红，再打源码跑绿」 | 拿到 3315/4、3327/1 两个红态 |
| 4 | 新测试文件行尾不一致 | 用 Python 逐字节检查 | 包内两个新测试都是 LF，产品树全是 CRLF |
| 5 | 改完就提交 | 提交前逐个检查 4 个改动文件的行尾 | 发现 `test_input_edges.c` 被编辑工具整体转成 LF，diff 变成 638 行重写 |

**转折点**：第 3 步。如果只是 apply 完看到 0 失败就提交，`test_uds_bounds.c` 和 `test_uart_fuzz.c` 到底测没测到那个行为是无从判断的；先跑红态才把「测试不是空跑」变成证据。

另一处弯路：想用 `git apply --dry-run`，git 只认 `--check`，第一条命令就报错；因为命令链用了 `&&`，后面什么都没执行，而 shell 返回的却是 0。差点误以为补丁已应用，是回头看 `git status --porcelain` 为空才发现的。

## 5. 根因

三个缺陷彼此独立，但共同点是**旧测试把错误行为写成了契约**（`test_protocol_edges.c:82` 的 STmin、`test_uds.c:194` 的 0x72），或者根本没有边界测试（UART 半帧）。所以源码补丁必须连测试一起改，否则改完反而变红；而只改测试不打源码，也必须先变红。

## 6. 修复

- 文件（学习仓库 `ota-core/`）：
  - `src/core/isotp.c`：保留 STmin 退化为 `0x7F` 而非中止；`tests/test_input_edges.c` 新增 135 条边界断言；`tests/test_protocol_edges.c` 改断言；`tests/test_main.c` 注册。
  - `src/core/uds.c`：新增 `NRC_RESPONSE_TOO_LONG 0x14`，把 `append_did()` 的 `-2`（容量）与 `-3`（存储）拆开；`tests/test_uds.c` 改断言并补「43 字节就正常」的反向断言；新增 `tests/test_uds_bounds.c`（4067 次带哨兵调用）；把 `test_input_edges.c` 里过时的 `NOTE` 软断言升级成 `TEST_ASSERT` 硬断言（checks 数不变，但从此能防回归）。
  - `src/core/can_uart.c`：COLLECT 固定长度缓冲改成滑动窗口 `parse_front()` + `drop_front()`；新增 `tests/test_uart_fuzz.c`；`tests/test_main.c` 注册。
- **产品化处理**：包源码头部原本写着 `PATCH PROPOSAL - do not build into the product until reviewed` 和 `see NOTES.md`（产品仓库里没有这个文件），合入时改写成正式设计说明；README 声称「已知限制已写进源码注释」，实际文件里没有 `flush()` 那句，一并补上。
- 备选方案与放弃原因：直接 `cp` 包里 `patched/` 的完整文件覆盖。放弃，因为那样无法逐行审阅改动，也会把打包期的临时注释和 LF 行尾一起带进来；用 `git apply` + 逐文件行尾转换，diff 小且可核对。

## 7. 验证

```
python run_tests.py --cc <tcc.exe> --capacity 1024 --in-memory
python run_tests.py --cc <tcc.exe> --capacity 512  --in-memory
python build-temp/static-check/check.py
python verify_project.py --cc <tcc.exe> --gcc <arm-gcc.exe> --dfp <DFP> --cmsis <CMSIS> --python-can-virtual
```

| 项 | 期望 | 实际 |
|---|---|---|
| 只加 `uds-nrc` 测试、不改源码 | 红 | **3315 checks / 4 failures** |
| `uds-nrc` 源码 + 测试 | 绿（两套容量） | **3319 / 0** × 2 |
| 只加 `uart-resync` 测试、不改源码 | 红 | **3327 / 1**（`lost == 0`） |
| `uart-resync` 源码 + 测试 | 绿（两套容量） | **3328 / 0** × 2 |
| 420 个存储断电模型场景 | 不变 | 420，两次都全过 |
| `-fanalyzer`（`-c -o` 模式） | 0 诊断 | 10 文件 / 0 诊断 |
| `-Wall -Wextra -Werror` | 0 诊断 | 24 文件 / 0 诊断 |
| Python unittest | 0 失败 | 28 / 0 |
| `verify_project.py` 全量 | passed | **76.0 s**，129 个源码哈希，7 项 virtual CAN，四套 GNU 只读镜像链接通过 |

提交：`192514b` / `c894314` / `f1a5294` / `7394619`，已 push 到 `can-uds-ota-learning`。

未验证：没有重烧固件，没有用真实 tester 发过超长 `0x22`，没有在真实串口上制造半帧。`hardware_verified=false`、`keil_target_linked=false` 保持不变。

## 8. 学到的东西

1. 「先加测试跑红、再改源码跑绿」在**合并**阶段同样要做，不能因为包里已经验过四组合就跳过 —— 基线已经从 3164 涨到 3309，包里的 B/C/D 数字不能直接当合并树数字用。
2. 一次性判别脚本一旦指向产品树，合入后就会失去判别力（详见另一条记录），证据要区分「打包期」和「合并后」。
3. 打包文档里写的「已写入注释」「patch 会冲突」都是当时那棵树的结论，在新树上必须重新核，不能照抄。
4. Windows 上编辑工具会静默把 CRLF 转 LF，提交前逐文件 `CRLF / bare_LF` 计数是必要的最后一道检查。

## 9. 简历表述

> 将三个独立验证的诊断协议补丁（ISO 15765-2 保留 STmin、ISO 14229-1 `responseTooLong`、UART 半帧重同步）合入产品仓库，采用「先加回归测试跑红、再改源码跑绿」的合并流程，使 C 核心回归从 3164 增至 3328 条断言（× 2 容量）、0 失败，并通过 `-fanalyzer` / `-Werror` 双静态闸门与 420 个断电场景回归。

> 全部为主机模型验证，含 129 个源码哈希的验证快照与四套 GNU 只读镜像链接；未重烧固件、未接真实 tester，实机行为未声称。

## 10. 边界与未解决

- `metadata-page-package` 是主机端口的页规则守卫，**仍未合入**，不能算进这次的 3328。
- UART 滑动窗口没有 `flush()`：若假头声称 18 字节、只剩 17 字节且线路此后静默，要等下一个字节才能推翻。串口持续流下不触发，但严格意义上仍是限制，已写进源码注释。
- 三个补丁都没有改「响应放不下时全或无」的策略，也没有引入分段响应。
- T1（512 块整片恢复演练）仍停在 477/512；这次全是纯主机工作，板子未上电。
