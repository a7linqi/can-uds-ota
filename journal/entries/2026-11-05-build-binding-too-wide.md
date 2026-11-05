# 硬件闸门把主机工具脚本也算作固件输入，改一行 Python 就要重跑构建

- 日期：2026-11-05
- 类型：环境 / 决策
- 关键词：`hardware-gate` `stage-hashes` `build-record` `reproducible-build`
- 验证等级：主机模型（哈希比对）+ 实机（闸门放行后探针与短程写入成功）

## 1. 一句话

板子连好、预检 17 项全绿，硬件闸门却拒绝上板；原因不是固件变了，而是昨晚新增的一个环境自检 Python 文件被算进了"固件构建输入"。

## 2. 背景与约束

`stages/board-lab/host/nor_restore.py --hardware` 在连板前会跑 `hardware_gate()`，校验：构建记录 `status=ready_for_review`、本阶段输入哈希、冻结核心哈希、mailbox 地址、以及三个产物（elf/bin/map）的 sha256 全部匹配。这是"板上跑的固件确实由这份源码产出"的唯一证据链，不能绕过。

约束：整目录拷贝到寝室电脑而不是 clone（`evidence/` 被 `.gitignore` 忽略，构建记录不在仓库里）；今天要在同一块板子上做诊断探针、短程验证、全程演练，中间会多次改主机驱动。

## 3. 现象

```
stage_hashes count= 30   record match= False
core_frozen count= 119   record match= True
```

`hardware_gate` 会因第一条直接抛 `stage inputs changed: rebuild the restore image`。

## 4. 排查过程

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 今天合入的三个产品补丁改到了冻结核心 | 分别干跑 `stage_hashes()` 与 `check_core_frozen()` | 核心 119 个哈希**完全匹配**（产品代码在 `Can-learning\ota-core`，绑定读的是 `Can\ota-core`，两者是不同副本），排除 |
| 2 | 某个 stage 源文件被改 | 逐键 diff 记录的 29 个文件与当前 30 个 | 唯一差异：`ADDED tools/check_environment.py`（昨晚写的换电脑预检），没有 CHANGED、没有 REMOVED |
| 3 | 固件产物是否也变了 | 对 elf/bin/map 逐个算 sha256 与记录比对 | 三个全部 OK，`bin` = `1f09cb0c…` = 板上固件 |

**转折点**：第 2 步。绑定失败与固件、芯片、板子都无关，纯粹是 `stage_hashes()` 把 ROOT 下所有 `.c/.h/.py/.ld/.ps1` 都当输入，连一个只检查环境的工具脚本也算。

**弯路**：第一次重跑 `build_restore.py` 刷新记录后，我以为闸门问题结束了，接着改 `host/nor_restore.py` 修握手 → `stage_hashes` 又变 → 闸门再次失效。这才意识到只要今天还会动主机脚本，就得在**最后一次代码改动之后**再重跑一次构建，否则白做。今天一共重跑了 3 次。

## 5. 根因

`build_restore.py::stage_hashes()` 用 `ROOT.rglob("*")` 扫描整个 `stages/board-lab/`，只排除 `build/`、`evidence/`、`__pycache__`。它把"参与固件链接的输入"与"本阶段的主机侧工具/驱动"混在一起，于是任何主机脚本的增删都会让一份其实完全没变的固件镜像失去绑定。

## 6. 修复

- **本次采用**：在最后一次代码改动之后重跑
  `python build_restore.py --gcc <arm-gcc> --dfp <DFP>`，只刷新哈希快照，不动源码。
  重跑后把 elf / bin / map 与事先备份（`build-temp/build-record-backup-20261105/`）逐字节 `cmp`，三份全部一致，因此**板上的固件无需重烧**，闸门恢复 `PASS`。
- 诊断脚本 `probe.py` / `short_run.py` / `apply_fix.py` 一律放在 `build-temp/` 而不是 `stages/board-lab/tools/`，避免再触发同一问题。
- 备选方案与放弃原因：把 `host/`、`tools/` 加进 `stage_hashes()` 的排除集合，或在记录里单列 `host_driver_sha256`。放弃（本轮），因为演练正在跑，此时改动绑定语义会引入未经回归的新变量；留作后续改进项，已写进任务清单。

## 7. 验证

```
python build_restore.py --gcc ... --dfp ...
cmp build/nor_restore_WRITE/nor_restore_WRITE.bin <backup>
python -c "import nor_restore as nr; nr.hardware_gate(0x200003b0)"
```

| 项 | 期望 | 实际 |
|---|---|---|
| 冻结核心 | 匹配 | 119 个哈希，`record match=True` |
| 本阶段文件 | 找出唯一差异 | 29 → 30，仅 `tools/check_environment.py` |
| 重跑后产物 | 字节不变 | elf / bin / map 三份 `cmp` 全部一致 |
| 闸门 | 放行 | `hardware_gate PASS`，`bin=1f09cb0c…` |
| 放行后的实机 | 能跑 | 单块时序探针 + 3 块短程写入成功 |

未验证：没有验证"排除 host/tools"这个长期方案；本轮没有改 `build_restore.py`。

## 8. 学到的东西

1. 可复现构建的绑定范围要对齐"真正进入镜像的文件"，范围过宽会让无关改动制造假故障，过宽和过松一样危险。
2. 绑定失败时先分开看"核心哈希"和"阶段哈希"，能立刻把问题域缩小一半。
3. 长任务启动前先把所有代码改动收口，再去刷新一次性快照，否则会反复返工。
4. 被 `.gitignore` 忽略的 `evidence/` 里存着闸门必需的构建记录——换电脑必须整目录拷贝，clone 出来的仓库过不了硬件闸门。

## 9. 简历表述

> 排查并恢复 SWD 固件的可复现构建绑定：定位到阶段输入哈希把主机工具脚本误算为固件输入，通过重跑链接并逐字节比对 elf/bin/map 证明镜像未变、板上固件无需重烧，恢复硬件闸门后完成实机探针与恢复演练。

## 10. 边界与未解决

- 这是**权宜之计**，不是设计修复：以后每改一次 `stages/board-lab/` 下的 `.py/.c/.h` 仍要重跑构建记录。
- `core_hashes` 绑定的是 `D:\Project\Can\ota-core`（原版副本），而产品代码在 `D:\Project\Can-learning\ota-core`；两个副本当前一致，但这是一处潜在的镜像漂移风险。
- 512 块全程演练正在运行，本条只覆盖上板前的闸门恢复。
## 11. 后续（同日 10:45，设计修复已落地）

上面第 10 节写的"权宜之计"当天就应验了第二次：新增主机脚本 `host/uds_write_probe.py` 之后，
上板闸门再次报 `stage inputs changed: rebuild the restore image`，而板上跑的固件一个字节都没变。

这次不再重跑构建记录，直接改 `build_restore.py:stage_hashes()`：只哈希 `firmware/` 下的
`.c/.h/.ld` 加 `build_restore.py` 自身，产品源码仍由 `check_core_frozen()` 单独绑定。
重建后 `bin_sha256` 仍是 `5b4b81d7…`（与之前逐字节相同），因此**没有重烧**板子，
闸门随后放行。

结论修正：第 10 节那条"以后每改一次 .py 都要重跑构建"不再成立。
闸门现在回答的是"这个二进制是不是这份固件源码产出的"，而不是"目录里有没有文件动过"。
