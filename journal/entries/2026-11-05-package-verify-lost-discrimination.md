# 合并后包内 verify.py 判别力消失，以及四个打包期结论被推翻

- 日期：2026-11-05
- 类型：决策（推翻旧判断）/ 环境
- 关键词：`verify.py` `baseline` `crlf` `git-apply` `package`
- 验证等级：主机模型（脚本与文件字节检查），**无实机**

## 1. 一句话

三包合入后复跑 `uart-resync-package/verify.py`，它从「旧 92/100 丢帧 vs 补丁 0/100」变成两条分支都 9/0、明确输出 `package discriminates: NO`；由此认清一批"取证期结论"依赖的是当时那棵树，不能原样搬到合并后。

## 2. 背景与约束

`build-temp/` 里的包是一次性取证产物，README 里的数字、状态、注意事项都写于 2026-11-04，前提都是「产品仓库零改动」。今天三个包真正落进 `ota-core/`，这个前提消失了。按项目规则，**推翻自己旧判断也要单独记一条**，且第 4 节必须留弯路。

## 3. 现象

```
--- current src/core/can_uart.c ---      checks=9 failures=0
--- patched can_uart_patched.c ---       checks=9 failures=0
package discriminates: NO
```

而 README 写的是当前实现 92/100 截断切点丢帧、8 断言 1 失败，补丁实现 0/100、9 断言 0 失败。同一个脚本，隔一天结论完全变了。

## 4. 排查过程

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 补丁没打进去，或打错了 | 检查 `src/core/can_uart.c` 的 `parse_front()` / `drop_front()` 与 diff | 补丁确实在，107 增 / 48 删 |
| 2 | 脚本的 "current" 是不是一份冻结副本 | 读 `verify.py` 的路径常量 | 它直接指向 `D:\Project\Can-learning\ota-core\src\core\can_uart.c`，**没有冻结副本** |
| 3 | 那"current"现在是什么 | 看该文件头部 | 已被我改成正式设计说明，滑动窗口实现 |
| 4 | 是不是所有包都这样 | 回看另两个包的四组合设计 | `isotp-stmin` / `uds-nrc` 的 verify.py 用包内 `patched/` 与仓库原版对比，同样会在合入后失去基线 |

**转折点**：第 2 步。判别脚本的"旧实现"不是一个自带的常量，而是一个**外部可变路径**。合并动作本身就把基线擦掉了 —— 不是脚本坏了，是它的隐含前提没了。

同轮还撞到三个坑，都属于「打包期描述 ≠ 合并期事实」：

1. `git apply --dry-run` 不是合法参数（只有 `--check`）；`&&` 链首条失败后什么都没执行，shell 却返回 0，差点以为补丁已应用。
2. 编辑工具把 `test_input_edges.c` 从 CRLF 整体转成 LF（638 行），`git diff --numstat` 立刻变成全量重写；复制包内新测试时同样发现包是 LF、产品树是 CRLF。
3. README 声称「`flush()` 已知限制已写入 `src/core/can_uart.c` 注释」，实际对文件 `count(b'flush') == 0`；包头部还留着 `PATCH PROPOSAL - do not build into the product until reviewed`。
4. `merge-proof` 结论说两个 `test_main.patch` 实测会冲突；本次顺序合入只有一个包改 `test_main.c`，且第一个包新增的行不在同一 hunk，两次 `git apply --check` 都通过。

## 5. 根因

取证包把「未修复的产品源码」当作免费的基线，没有把它冻结进包内。因此包的判别力只在「产品树仍是旧版」这个窗口内成立；一旦合入，`current` 与 `patched` 指向同一份实现，两条分支必然同时变绿。文档里那些"当前实现""现为 0x72""未合入"也同理，都是相对 11-04 那棵树的状态描述。

## 6. 修复

- **没有改 `verify.py`**：`build-temp/` 是一次性取证区，合并已完成，此时去改脚本等于造一份新证据，而不是记录已经发生的事实。
- 把红态证据锚定在**合并树**上：只加测试不改源码跑出的 **3315/4**（uds-nrc）与 **3327/1**（uart-resync），以及打完源码后的 **3319/0** / **3328/0**。
- 在 `build-temp/INDEX.md`、`board-docs/07-待办/任务清单.md`、`CAN_UDS_HANDOFF.md` 三处写明：该脚本已失去判别力，输出 `package discriminates: NO` 不是回归，不能再当新证据。
- 产品化处理：`can_uart.c` 头部改写成正式设计说明，补上真实缺失的 `flush()` 限制注释；删除对产品仓库内不存在的 `NOTES.md` 的引用。
- 行尾：所有改动文件提交前逐个统计 `CRLF` / `bare_LF`，确保都是纯 CRLF；新测试复制时先转 CRLF。
- 备选方案与放弃原因：把旧实现冻结成 `baseline_can_uart.c` 放进包里，让 `verify.py` 长期可复现。放弃，因为这次的目标是合入产品代码而不是维护取证工具链；正确做法已记进边界，将来若要把包变成长期回归资产再做。

## 7. 验证

```
python build-temp/uart-resync-package/verify.py
python build-temp/static-check/check.py
git diff --numstat
```

| 项 | 期望 | 实际 |
|---|---|---|
| 合入后 `verify.py` 是否还能判别 | 不能 | `package discriminates: NO`，两分支 9/0 |
| 合并树红态（只加测试） | 红 | 3315/4、3327/1 |
| 合并树绿态（源码+测试） | 绿 | 3328/0 × 2 容量 |
| 独立闸门 | 全过 | `-fanalyzer` 10 文件 / `-Werror` 24 文件 / 3328/0 / 28 单测 |
| 行尾 | 纯 CRLF | 四个改动文件 `bare_LF=0` |
| `can_uart.c` 是否含 flush 说明 | 有 | 补写后存在，且不再有 PATCH PROPOSAL 字样 |

未验证：没有把 `flush()` 限制做成可运行的用例（线路静默场景需要超时钩子，当前实现没有）；没有在真实串口上制造半帧。

## 8. 学到的东西

1. **一次性自检脚本是有保质期的**。凡是把"当前实现"指向外部路径的对比脚本，都要在文档里写明有效期，或者自带冻结基线。
2. `package discriminates: NO` 这类输出本身是有价值的证据 —— 它说明工具前提变了，而不是代码退化了；关键是不能把它当成"通过"。
3. 打包 README 里的每一句「已写入」「会冲突」「未合入」都是对当时状态的快照，合入时必须逐条复核，不能照抄进提交信息。
4. 提交前用字节级计数检查行尾，比相信编辑器便宜得多。

## 9. 简历表述

> 在补丁合入后复核取证工具链，定位到一次性判别脚本因读取可变产品树而失去基线（两分支均 9/0、`package discriminates: NO`），改以合并树上的红/绿对照（3315/4 → 3319/0、3327/1 → 3328/0）作为回归证据，并同步交接文档、简历口径与验证快照，避免把失效自检误当成通过证明。

> 顺带修正三处打包期与合并期不一致：产品源码内残留的"勿合入"标记、README 声称存在但缺失的边界注释、以及被预言会冲突但实际未触发的补丁注册。

## 10. 边界与未解决

- `uart-resync-package/verify.py`、`isotp-stmin-package/verify.py`、`uds-nrc-package/verify.py` 现在都只能当「属性测试跑通」用，不能再当「补丁判别」用。
- `metadata-page-package` 仍未合入，它的 `verify.py` 判别力暂时还在（前提未变）。
- 若要让这些包成为长期回归资产，需要把旧实现冻结进包内并让 `verify.py` 只读包内副本；本轮没做。
- T1 的握手根因仍未定死（第 478 块写门铃到读头只差 0.877 s，而一块要 10 s），本次主机工作没有触及。
