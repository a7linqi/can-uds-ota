# 修掉 ISO-TP 把保留 STmin 当致命错误的问题（135 条边界断言顺带做出来）

- 日期：2026-11-04
- 类型：缺陷
- 关键词：`isotp` `stmin` `iso15765-2` `uds` `边界测试`
- 验证等级：主机模型（TinyCC / PC，不是目标构建，不是实机）

## 1. 一句话

`src/core/isotp.c` 收到保留 STmin（0x80–0xF0、0xFA–0xFF）会直接中止整个多帧传输，
而 ISO 15765-2 要求按最长的 0x7F（127 ms）继续；1 行修掉，并用同一份断言在修复前后各证明了
"缺陷存在"和"缺陷消失"。

## 2. 背景与约束

- 上一轮代码体检扫完 `src/core/*.c`、`src/driver/*.c`（`-fanalyzer` 0 告警），
  只留下两个没扫的：`uds.c` 的 DID 拼接边界、`isotp.c` 的首帧长度边界。
- 约束：**不能只靠读代码下结论**。项目红线是"数字只用当场实测、分清模型/构建/实机"，
  而"读代码觉得有问题"既不是实测，也容易被自己骗。
- 约束：本 harness 不改产品仓库的 `src/`，改动只能以补丁包形式交付。
- 板子此时正在跑 NOR 恢复演练（512 块），SWD 和串口都不能碰，正好做纯主机侧工作。

## 3. 现象

旧实现在 `isotp_on_frame()` 的流控分支里：

```c
if(encoded<=0x7Fu) l->fc_stmin=encoded;
else if(encoded>=0xF1u && encoded<=0xF9u) l->fc_stmin=1u;
else { (void)abort_tx(l,ISOTP_ERR_FLOW); return; }
```

`else` 同时覆盖了两段 Reserved：`0x80..0xF0` 和 `0xFA..0xFF`。

实测（`edge_old.exe`，E 组）：

```
TEST_ASSERT(W.link.tx_state == ISOTP_IDLE);        -> 通过
TEST_ASSERT(W.link.tx_error == ISOTP_ERR_FLOW);    -> 通过
```

两条都通过，说明发一帧 `30 00 00 80`（FC，STmin=0x80）之后，
发送侧状态机直接回到 IDLE、`tx_error = -7`，剩下的连续帧一帧都没发。

## 4. 排查过程

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | `uds.c` DID 拼接会越界 | 手算 `append_did` 的 `pos+2+len > max`，核对 `UDS_UPDATE_STATUS_SIZE=40` 与 9 个 `put_be32` | 正好 40 字节，无越界 |
| 2 | `0x34` 的 size 上界超过外部槽会写穿到备份区 | 核对 `IMAGE_MAX_PAYLOAD=APP_SIZE` 与 `EXT_IMAGE_SLOT_SIZE`，找编译期 `#error` | 有 `#error` 兜底，ZG/ZE 两套都富余 |
| 3 | `0x36` 重传校验的 `previous[510]` 栈缓冲会溢出 | 核对 `UDS_MAX_DOWNLOAD_DATA = BLOCK-2 = 510`、`len ≤ 512`、`n = len-2` | 正好卡在边界，不溢出 |
| 4 | 首帧 `dlc=2` 会让 `dlc-2` 下溢 | 读代码 | 有 `if (f->dlc < 3u) return;`，挡住 |
| 5 | 断言的写法本身有偏差 | 把 30 个候选逐条写成断言，跑一遍 | 只有 3 条 NOTE + 1 条真缺陷，其余全过 |

**转折点**：前 4 步全是"读代码觉得可疑"，4 条全部被现有代码或编译期断言兜住。
第 5 步把猜测变成可执行断言之后，命中率才从 0/4 变成 1 个真缺陷 + 3 条语义偏差。
**读代码只能生成假设，断言才能给结论。**

另一条弯路：一开始想直接改产品仓库的 `isotp.c` 再跑测试。改成"补丁副本 + 同一份断言跑两遍"之后，
证据反而更强——旧实现上 `#else` 分支的断言**通过**，等于用测试**证明了缺陷存在**，
而不是靠我在报告里说"它有问题"。

## 5. 根因

STmin 的 `else` 分支把"非法值"和"保留值"混为一谈。
保留值在规范里不是错误：接收方可以发（虽然不该发），发送方必须退化到最慢的 0x7F 继续，
否则一次不规范的对端就能让整次升级失败——而升级失败在这里的代价是"车端刷不进去"。

## 6. 修复

- 文件：`src/core/isotp.c`（补丁见 `build-temp/uds-isotp-edges/fix-reserved-stmin.diff`）
- 文件：`tests/test_protocol_edges.c:82`（补丁见 `update-test-protocol-edges.diff`）——
  这条断言把旧行为写死了：`tx_state==IDLE && tx_error==ISOTP_ERR_FLOW`。
  补丁改变契约，它必须一起改，否则完整套件从 3164/0 变成 3163/1。

```diff
-            else { (void)abort_tx(l,ISOTP_ERR_FLOW); return; }
+            /* 0x80..0xF0 and 0xFA..0xFF are reserved. ISO 15765-2 requires the
+               sender to use the longest legal value (0x7F = 127 ms) and keep
+               transmitting; aborting would make one sloppy tester fatal. */
+            else l->fc_stmin=0x7Fu;
```

- 备选方案与放弃原因：
  - **保留中止，只加日志**：会被动接受"不规范对端 = 升级失败"，与项目"能恢复"的卖点冲突，放弃。
  - **把保留值当成 0 ms（全速发）**：更快，但违反"STmin 是最小间隔"的语义，
    对端缓冲可能溢出，比 127 ms 更危险，放弃。

## 7. 验证

```bat
cd D:\Project\Can\build-temp\uds-isotp-edges
run.cmd
```

| 项 | 期望 | 实际 |
|---|---|---|
| 旧实现断言数 | 覆盖 9 组边界 | 135 checks，0 failures，3 notes |
| 旧实现遇到 STmin=0x80 | 中止 | `tx_state==IDLE`、`tx_error==-7`（断言通过即证据） |
| 补丁后断言数 | 多 9 条 STmin 断言 | 144 checks，0 failures，3 notes |
| 补丁后 STmin=0x7F 被存下 | `fc_stmin==0x7F` | 通过 |
| 补丁后 127 ms 真的被遵守 | 126 ms 不发、127 ms 才发 | `nframes` 1 → 1 → 2，通过 |
| 另一段保留值 0xFA | 同 0x80 | 通过 |
| 可接入版（无 main） | 与独立版同结果 | 135 / 144，一致 |
| **完整套件，原版** | 3164 / 0 | 3164 checks, 0 failures, 420 掉电场景 |
| **完整套件，只打源码补丁** | 只有 1 条旧断言失败 | **3163 checks, 1 failure**（`test_protocol_edges.c:82`） |
| **完整套件，源码+测试都改** | 全过 | **3165 checks, 0 failures, 420 掉电场景** |

未验证：没有在 STM32 目标板上跑；没有接真实 CAN 总线；没有用真实 tester 发保留 STmin。
"主机模型通过"不等于"实机通过"。

## 8. 学到的东西

1. **规范里的 Reserved 不是错误值**。协议留白通常有明确的降级规则（这里是最长 STmin），
   实现者按"非法输入"处理看起来更安全，实际是把自己的健壮性交给了对端。
2. **断言当前行为也是一种测试**。`#else` 分支断言"它会中止"，跑通之后就拿到了缺陷存在的证据；
   改完切到 `#ifdef` 分支断言"它应该继续"，同一份文件完成前后对照。
3. **读代码的产出是假设，不是结论**。这一轮 4 个"看起来会炸"的点全是假阳性，
   真缺陷反而是唯一一个"读起来像故意这么写"的地方。

## 9. 简历表述

> 为 ISO-TP/UDS 协议栈补齐 135 条输入边界断言（9 组，0 失败），
> 实测发现 reserved STmin 处理偏离 ISO 15765-2（会中止整次传输），
> 1 行修复后同一套断言扩到 144 条全过，并用 126/127 ms 两个时刻验证了 0x7F 间隔真的生效；
> 完整回归 3165 条断言 0 失败（含 420 个存储掉电场景）。

> 边界：主机模型验证；STmin 依据 ISO 15765-2 保留值条款与 Linux `net/can/isotp.c` 的同类修正。

## 10. 边界与未解决

- 只覆盖经典 CAN（dlc ≤ 8），没有 CAN FD。
- 没有覆盖 `0x34`/`0x37` 的完整下载—校验链路（要构造合法签名镜像），只测了 `0x36` 的计数器/重传/越界。
- 三条 NOTE 里只有"响应缓冲不足回 0x72 而非 0x14"值得再讨论，优先级低，未改。
- `isotp` 在 `port.now_ms == NULL` 时多帧发送可以永久等 FC（无时基则无超时）；
  当前平台都提供了时基，属于"已知限制"而非缺陷，未改。
