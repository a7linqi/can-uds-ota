# NRC 0x72 是设计好的门控，不是缺陷；真实 CAN 完整 OTA 只需换传输层

## 1. 一句话

`0x22 0200` 回 NRC `0x72` 的根因是只读构建故意令 `storage_ready=0`，EEPROM 端口被关掉 → `metadata_load()` 失败；同时实机证明现成 PC 库在真实 CAN 上已能跑 ISO-TP 多帧与 Seed/Key，因此完整 OTA 的改动量比预想小得多。

## 2. 背景

真实 CAN 只读诊断打通后，剩下三个待办：查 `0x22 0200` 的 NRC `0x72`、评估真实 CAN 完整 OTA、提交仓库。此前把 `0x72` 记为「未通过」，并一度认为真实 CAN OTA 需要大改协议栈。

## 3. 现象（全部实机，真实 CAN 500 kbit/s）

用现成的 `PythonCanTransport + IsoTpClient` 逐条发请求：

```
TX 10 02                              RX 50 02 00 32 01 F4   (P2=50 ms, P2*=5000 ms)
TX 22 F1 86                           RX 62 F1 86 02         (当前会话=编程)
TX 22 02 00                           RX 7F 22 72            ← 只有这个 DID 失败
TX 27 01                              RX 67 01 5E E9 79 B1   (seed)
TX 27 02 44 A5 84 36                  RX 67 02               (解锁成功)
TX 34 00 44 08 01 00 00 00 00 AD 60  RX 7F 34 22            (11 B ISO-TP 多帧)
TX 22 02 00                           RX 7F 22 72            (稳定复现)
```

另一次对照：`0x22 F186` → `62 F1 86 01`、`0x22 F195` → 首帧 `10 08 62 F1 95 30 2E 32`（"0.2.0"）、`0x22 F190` → 20 B VIN 多帧，全部正常。证据 `stages/board-lab/evidence/can/nrc_72_gate_evidence.json`。

## 4. 排查（留弯路）

一开始按「EEPROM 读失败 / I2C 又锁死」去想，因为开机横幅里 `EEPROM 256-byte read complete` 看着正常，容易往硬件偶发故障上怀疑。其实横幅那行只证明启动时探针读过 EEPROM，**不代表运行时的 metadata 端口可用**。另一条弯路是把 `0x72` 写成验收「未通过」带了好几轮，没去读 `append_did` 的返回值映射。

## 5. 根因

源码链条（逐层可核对）：

1. `explorer_reference.h`：`OTA_EXPLORER_STORAGE_TRUSTED` 默认等于 `OTA_EXPLORER_UPDATE_CAPABLE`；diagnostics READONLY target 里后者为 0 → **`STORAGE_TRUSTED=0`**。
2. `explorer_reference.c:373-379`：`#else config->storage_verified=0u;`。
3. `stm32_ports.c`：`p->storage_ready = cfg->board_verified && cfg->storage_verified && …` → **0**。
4. `stm32_eeprom.c`：`eeprom_read()` 首行 `if(!p->storage_ready) return -1;` → **EEPROM 读恒失败**（端口函数本身存在，并非 NULL）。
5. `metadata.c`：`read_copy` 失败 → `metadata_load()` 返回 −1。
6. `uds.c:195-200`：`append_did(UDS_DID_UPDATE_STATUS)` 里 `if(loaded<0) return -3`。
7. `uds.c:251`：`r == -3` → `NRC_GENERAL_PROGRAMMING_FAILURE (0x72)`。

**所以这是只读诊断固件的刻意门控**：不信任存储就不暴露状态 DID，避免把没核对过的 NOR/EEPROM 内容当成可信遥测。UART 台架的可写变体设了 `STORAGE_TRUSTED=1`，所以那边 `0x22 0200` 正常。

同一门控还解释了 `0x34` 回 `7F 34 22`：`stm32_diag.c` 里 `programming_allowed = OTA_ENABLE_FLASHING && storage_ready = 0 && 0 = 0`，对 `0x34/0x36/0x37/0x31` 统一回 NRC `0x22`。此前项目记的「写入门控不是保险丝、两种原因总线同形」在这里得到又一条实机佐证。

## 6. 修复 / 产出

**无需改固件源码**。产出：
- `stages/board-lab/host/can_gate_probe.py`（可复现的门控实证脚本）与 `evidence/can/nrc_72_gate_evidence.json`。
- `stages/board-lab/REAL_CAN_OTA_ASSESSMENT.md`：完整 OTA 的改动清单、时序外推、风险、工作量。
- 更正文档中「`0x22 0200` 未通过」的旧表述。

## 7. 验证

已实测：上面 7 条请求/响应逐帧、源码链条 7 层、`PythonCanTransport + IsoTpClient` 在真实 CAN 上完成 11 B 多帧请求与 Seed/Key 解锁。**未实测**：真实 CAN 上的 `0x36/0x37/0x31/0x11`、NOR 擦写时序、断电恢复、长时间稳定性。评估文档里的 30–50 s 是 UART 台架实测外推，不是实测值。

## 8. 学到

「NRC 是失败」这个直觉要先让位于「NRC 是谁、在什么门控下发的」。把错误码当成待修缺陷，会漏掉它其实是设计契约的一部分。反过来，评估工作量时先跑一遍现成库的最小闭环（这次是 `0x27` + 多帧 `0x34`），比读代码猜改动量准得多。

## 9. 简历表述

可用：「在真实 CAN 上验证 ISO-TP 多帧请求与 UDS Seed/Key 安全解锁链路，并定位只读构建下状态 DID 的门控行为；据此评估出完整 OTA 只需替换传输层与补充会话保活」。**不可写**：「真实 CAN OTA 已通过」。

## 10. 边界

- 本轮只跑了只读诊断与门控遥测；`0x34` 回 NRC `0x22` 是预期门控，不是下载失败。
- 评估文档是计划，不是验证结果；真实 CAN 完整 OTA 仍**未做**。
- `0x22 0200` 在只读构建下永久回 `0x72`，除非重新构建 `STORAGE_TRUSTED=1` 的固件；这是设计行为，不要当 bug 修。
