# 发现静态分析一直在空跑（-fsyntax-only 会静默关闭 -fanalyzer）

- 日期：2026-11-04
- 类型：环境 / 质量检查
- 关键词：`fanalyzer` `gcc` `static-analysis` `-fsyntax-only` `false-clean`
- 验证等级：主机工具链（arm-none-eabi-gcc 15.2.1 目标编译）

## 1. 一句话

之前"`-fanalyzer` 扫 `src/core/*.c`、`src/driver/*.c`：0 告警 0 错误"这个结论是**空跑**得出的——
配上 `-fsyntax-only` 时分析器根本不运行，那个 0 不代表任何东西。

## 2. 背景与约束

`cppcheck` 和 `clang-tidy` 本机都没装，唯一的静态分析手段是 GCC 自带的 `-fanalyzer`。
本轮要把它固化成一条随时可跑的自检命令（准备做 `check.py`），
所以在写进脚本之前，先确认"这条命令真的会报东西"。
约束：不得修改产品源码；只读扫描。

## 3. 现象

先用最省事的形式扫产品源码：

```
arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -std=c99 -fanalyzer -fsyntax-only \
  -Wall -Wextra -Iinclude -Isrc/port/host -Ithird_party/ed25519 \
  src/core/*.c src/driver/*.c
```

输出为空，退出码 0。看起来"干净"。

## 4. 排查过程

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 产品代码确实干净 | 直接接受 0 告警 | 打算就这么写进脚本 |
| 2 | 但"干净"总该有办法证伪 | 写 `analyzer-probe/bad.c`：空指针解引用 + 内存泄漏 + double free 三个明显缺陷 | 待测 |
| 3 | 分析器是否真的在跑 | `-fanalyzer -fsyntax-only` 扫 `bad.c`，数 `warning:` 行 | **0 条** |
| 4 | 是不是缺了 `-c` | 改成 `-fanalyzer -c -o bad.o` 再扫同一文件 | **4 条** |
| 5 | 产品源码在正确模式下会不会也有告警 | 逐文件 `-c -o scratch/xxx.o` 重扫 `src/core/*.c` + `src/driver/*.c` | 10 个文件，**仍然 0 条** |

**转折点**：第 3 步和第 4 步扫的是**同一个文件**，只有编译模式不同，
告警数从 0 变成 4。这说明 `-fsyntax-only` 把 `-fanalyzer` 整个关掉了，
而不是"代码恰好干净"。

## 5. 根因

`-fanalyzer` 的分析阶段挂在编译后端（GIMPLE 之后）。
`-fsyntax-only` 让 GCC 在语法/语义检查完成时就退出，**根本不进后端**，
所以分析器没有输入可分析，自然一条都不报。
两者同时出现时 GCC 不警告、不报错，静默降级为"纯语法检查"。

结果是一个**假阳性清洁报告**：命令跑得通、退出码 0、输出为空，
但检查的东西根本不存在。

## 6. 修复

- 文件：`build-temp/static-check/check.py`（新增，四道闸门）
  - 第 1 道改成 `-fanalyzer -c -o <scratch>/xxx.o`，每个源文件单独编译；
    目标文件写到 `build-temp` 下的临时目录，跑完删除，仓库零残留。
  - 脚本头部把这次的对照实验（0 条 vs 4 条）写成注释，防止以后有人"优化"回 `-fsyntax-only`。
- 文件：`build-temp/static-check/check.cmd`（双击入口，纯 ASCII 内容 + `chcp 65001`）
- 备选方案与放弃原因：装 `cppcheck` 或 `clang-tidy`。
  放弃原因：本机没有，装包会给项目加一条环境依赖；
  而 `-fanalyzer` 已经能覆盖"空指针/泄漏/双重释放/越界"这几类，
  且它和目标编译器是同一套（arm-none-eabi-gcc 15.2.1），不会出现"工具说没问题但目标编不过"。

## 7. 验证

```
python build-temp\static-check\check.py
```

| 项 | 期望 | 实际 |
|---|---|---|
| 探针 `bad.c` + `-fsyntax-only` | 报出缺陷 | **0 条**（证明该方法无效） |
| 探针 `bad.c` + `-c -o` | 报出缺陷 | **4 条**（证明该方法有效） |
| 产品源码 `-fanalyzer -c -o` | 0 条 | 10 个文件 0 条 |
| `-Wall -Wextra -Werror` 语法 | 0 条 | 21 个文件 0 条 |
| 主机完整套件（TinyCC 1024 KiB） | 0 失败 | 3164 checks / 0 failures |
| `python -m unittest tests_tools` | 0 失败 | 28 tests / 0 failures |
| 端到端耗时 | — | **8.9 秒**（`check.cmd` 实测） |

未验证：没有在目标板上跑；`-fanalyzer` 是编译期分析，与运行期行为无关。

## 8. 学到的东西

1. **"没报错"要先证明"会报错"**。一个检查工具在已知坏样本上必须变红，
   否则它的绿色毫无意义。这条对 lint、静态分析、测试套件一律成立。
2. 编译器的"优化/加速"开关经常是**语义开关**。`-fsyntax-only` 看起来只是"少做点事"，
   实际是"少做了唯一重要的那件事"。同类陷阱还有 `-fno-analyzer`、`--dry-run` 类参数。
3. 数字对不代表过程对。这次的 0 告警**碰巧**和真实结果一致（重扫仍是 0），
   如果我只记结果不记方法，下次换个项目就会用一个失效的命令得出假结论。

## 9. 简历表述

> 建立 4 道闸门的静态自检（`-fanalyzer` + `-Werror` + 3164 条主机断言 + 28 项 Python 单测），
> 单次 8.9 秒；并用"已知缺陷探针"证明分析器真实生效，
> 发现原命令 `-fsyntax-only` 会静默关闭 `-fanalyzer`、产出假清洁报告。

> 关键点不是"扫了 0 告警"，而是"先证明这个扫描会报警"。

## 10. 边界与未解决

- 只覆盖 `src/core/` 和 `src/driver/`，`platform/stm32f407/` 需要 HAL 头，本轮未纳入。
- `-fanalyzer` 对跨函数、跨编译单元的数据流能力有限，不等于形式化验证。
- 没做 CI：`check.py` 目前是手动/双击触发，还没接 git hook 或流水线。
- 之前 `2026-11-04-code-audit.md` 里"0 告警"的表述已被本条修正：
  结论仍然成立，但当时的方法不成立。已在该条目加了指向本条的说明。
