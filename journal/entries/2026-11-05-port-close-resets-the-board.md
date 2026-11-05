# 找到「跨脚本状态不持久」的真凶：关串口会复位板子

- 日期：2026-11-05
- 类型：缺陷（环境陷阱）
- 关键词：`ch340` `dtr` `nrst` `handoff` `serial` `usart1`
- 验证等级：实机

## 1. 一句话

板子上的 CH340 把 DTR/RTS 接到了 NRST，`SerialCanTransport.close()` 拉低 DTR 就是一次硬复位；
所以任何"先运行 A 脚本留下状态、再运行 B 脚本"的流程都不成立。

## 2. 背景与约束

阶段 D 收尾后，board-lab 的驱动脚本都是"一次一个进程"，每个进程自己开关串口：
`uds_app_handoff.py` 负责把 App 停到 loader，`uds_stage_d.py` 负责下载安装。
上一轮凭这套流程"验证"过 App→loader 交棒：脚本末尾看到了
`loader: stop requested by the application`，退出码 0。

约束：不能改 BSP 的引脚定义（那属于硬件事实，不是软件可以修的），只能改工具和操作流程。

## 3. 现象

1. `uds_app_handoff.py` 跑完，串口打印 `loader: stop requested by the application`，板子停在 loader。
2. 紧接着跑 `uds_stage_d.py --image app_v9.img2`，第一条 `22 02 00` 回来却是
   `... 00 00 00 80`（`diag[3]` 的 bit7 = 1，即 **App 在应答**），`metadata_seq` 已经比上次多 1。
3. 于是 `34` 直接被 NRC `0x22` 拒（App 是 `OTA_ENABLE_FLASHING=0` 的构建），
   后续 88 帧 `36`、`37`、`31` 全部 `0x22`，看起来像"门控卡死"又回来了。
4. 另一条线索：用 ST-Link `-c port=SWD mode=UR -q -rst` 复位后抓串口，**一个字节都没有**；
   而用固件自己的 `11 01` 复位，能稳定抓到完整横幅。

## 4. 排查过程

| 步骤 | 我怀疑什么 | 做了什么 | 结果 |
|---|---|---|---|
| 1 | 板子没连 | 重新枚举 PnP | 错了：`STM32 STLink` 在 `USBDevice` 类下，我上一轮只筛了 `Class -eq USB` 才漏掉；CH340 在 COM9 |
| 2 | 日志被 `uart_binary` 抑制 | 读 BSP 的 `ota_user_log` | 不是：`uart_ready` 在 `ota_bsp_init` 里就置 1，二进制模式只在 `ota_bsp_uart_start` 之后 |
| 3 | loader 走了 `force_loader` 分支不打印 | 读 `ota_user_force_loader()` | 不是：loader 构建 `BOOT_ROLE=1`，返回 0，会正常打印 |
| 4 | 打开串口时 DTR 触发了复位 | 开端口后静读 4 s | 不是：0 字节 |
| 5 | 关串口才触发复位 | 读状态（seq 59、role=application）→ 发 `10 02`（App 落盘 `boot_request`）→ **关端口，不发 `11 01`** → 等 3 s → 再读状态 | **转折点**：seq 变成 61（+2 = 写入 + 消费），role 变成 **loader** |

**转折点**：第 5 步。没发任何复位指令，却多出一次"loader 消费 `boot_request`"的写入，
说明板子在两次读之间**被复位过一次**，而且复位发生在关端口的那一刻。

## 5. 根因

CH340 模块（ATK Explorer 参考板的 USB 串口）把 DTR/RTS 接在 MCU 的 NRST 上。
`pyserial` 关闭端口时释放/拉低 DTR，产生一个复位脉冲：

- 复位后 loader 启动，读到 `boot_request=1` → 消费并写回 `boot_request=0`（seq +1）→ 停在 loader。
- 下一次复位时 `boot_request=0` → 按 `boot_select` 第 172 行判定 active 有效 → 跳 App。

所以"交棒成功"确实发生了，但**状态活不过进程退出**。而随后那个脚本自己又不会交棒，
它直接假设板子在 loader，于是把 `34` 发给了 App。

## 6. 修复

- 文件：`stages/board-lab/host/uds_stage_d.py`（新增 `--handoff`）
  - 在**同一个串口会话**里做：`10 02`（App 落盘旗标）→ `11 01`（复位）→ 抓横幅并断言
    含 `stop requested` → 再 `10 02` / `27` / `34` / `36…` / `37` / `31` / `11 01`。
  - 这才是真实诊断仪的流程：一次连接把 ECU 从 App 送进 loader 再把它写完。
- 文件：`stages/board-lab/host/uds_stage_d.py`（顺带修自己引入的性能坑）
  - 抓横幅时临时把 `ser.timeout` 调成 0.2 s，抓完必须恢复成传输层的 0.01 s。
    忘了恢复会让每帧 `0x36` 多付 0.2 s × 多次轮询：实测 0.05–0.34 s/帧 变成 0.43–2.47 s/帧（约 7 倍）。
- 备选方案与放弃原因：把 DTR 固定为不活动（`dtr=False`）——放弃了，因为 RTS 也在同一路上，
  且换一台机器/换一根线就失效；软件上"不依赖关端口时的电平"更可靠。

## 7. 验证

```
python host/uds_app_handoff.py --port COM9 --out evidence/app-handoff-v8
python host/uds_stage_d.py --port COM9 --handoff --image <app_v9.img2> --out evidence/stage-d/20261105-v9-handoff
```

| 项 | 期望 | 实际 |
|---|---|---|
| 关端口前后 seq 差 | 无复位时 0 | **+2**（写入 1 + 消费 1），role 从 application 变 loader |
| 开端口后是否有横幅 | 若有复位则应有 | 0 字节（开端口不复位，复位在关端口） |
| `--handoff` 后 `34` | `74 …` | `74 20 02 00`（14.26 s 擦除） |
| 交棒横幅 | 含 `stop requested` | 有，脚本断言通过 |
| 升级结果 | `active_version=9` | `active_version=9`、`active_size=44288`、`active_crc=5F966357` |

未验证：没在别的 CH340 模块上复现；不确定是 DTR 还是 RTS 那一路（两者都在模块的排针上，未逐根断开测）。

## 8. 学到的东西

1. "上一步成功了"不等于"下一步开始时状态还在"——跨进程的工具链必须把状态机当成易失的。
2. 一条 `11 01` 能给出复位横幅、ST-Link 的 `-rst` 却给不出，这种不对称本身就是线索，不该当成偶然。
3. 调试打印要一次给足信息：这次是"seq +2 且 role 变了"这两个字段同时看才定位到复位点。

## 9. 简历表述

> 定位并绕开 CH340 DTR→NRST 导致的串口关闭复位问题，把"App 请求进入 Bootloader 后续升级"改为单连接原子流程，
> 实现从运行态应用连续升级（v8→v9→v10）并在每轮读回内部 Flash 做 SHA-256 比对全一致。

> 难点：症状表现为"UDS 门控随机卡死"，实际是 3 类原因叠加（构建关闭、作业关门、串口关闭复位），
> 靠状态 DID 遥测与 EEPROM 序号差值把三者分开。

## 10. 边界与未解决

- 未做真实拔电测试（拔 USB 会让 CH340 消失，属于另一类中断模型）。
- 关闭端口复位意味着"最后一次复位后的串口输出会丢"，所以抓启动横幅必须由固件触发的复位来配合。
