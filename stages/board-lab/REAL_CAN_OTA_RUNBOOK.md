# 真实 CAN 完整 OTA 验收 Runbook（2026-11-09 准备完成；首次接线执行受阻，见第 11 节）

> 配套：`REAL_CAN_OTA_ASSESSMENT.md`（改动清单/时序/风险）、`board-docs/05-工具与命令/CAN台架-排障实录.md`（历史坑）。
> **状态**：不依赖硬件的准备工作已全部完成并实测；**第 4 步之后的硬件步骤未通过**——2026-11-09 晚首次接线时链路没建起来（`status` 超时），A/B 对照已排除固件，卡点回到板级供电与 `USB_232`。详见第 11 节与 `evidence/can/bringup_20261109_ab.json`。**真实 CAN 完整 OTA 仍未验证。**

## 0. 已经备好的东西（不依赖硬件，均已实测）

| 项 | 值 |
|---|---|
| 可写 CAN loader | `D:\Project\Can-learning\ota-core\build-temp\stage-d-can\loader\loader.bin`，48172 B，sha256 `0f14d783e2154d1e…` |
| 初始 app | `…\stage-d-can\app\app.bin`，44824 B，sha256 `47f604c135166a0d…` |
| 构建脚本 | `build-temp\build_stage_d_can.py`（由 `build_stage_d.py` 复制，只改 `TARGET`/`OUT` 两行；生成方式见评估文档 A 节） |
| 现成签名包 | `build-temp\stage-d\pkgs\app_v14.img2` … `app_v23_wd.img2`（44920 B） |
| 关键交叉验证 | `app_v14.img2` 剥掉 32 B 头 + 64 B 签名后的 payload = 44824 B、sha256 `47f604c1…`，与新构建 `app.bin` **逐字节相同**；历史 `POWER_CUT_EVIDENCE.md` 记录的实机读回值也是 `47f604c1…` |
| PC 工具 | `tools/uds_tool.py`（`status`/`flash`/`--backend can`/`--log`）、`tools/make_image.py`（`--version N --sign`） |
| 工程状态 | MDK 工程已恢复原 `--uart` 版，活库 `git status` 干净 |

## 1. 接线自检（必须先做）

```powershell
Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_0C72|VID_1A86|VID_0483' } |
  Select-Object Status,Class,FriendlyName
```

期望三项都在：`PCAN-USB`（CAN-Hardware）、`USB-SERIAL CH340`（Ports，真实 COM）、`STM32 STLink`（USBDevice）。
CANH↔CANH、CANL↔L、**GND 共地**；UTC-T01 拨码 `120R`；P5 两个跳帽竖直短接到 CAN 侧；`USB_232` 插上（供电 + 开机日志）。
`PCAN_USBBUS1` 区分大小写；跑脚本前关闭 PcanView（它会独占通道，导致 `CAN_Initialize=0x1400`）。

链路自检（只读，应已通过）：

```powershell
python D:\Project\Can\stages\board-lab\host\can_gate_probe.py
```

## 2. 备份当前板子（保证可回退）

当前板子是只读诊断固件 `explorer_ZG_diagnostics_READONLY.bin`，随时可重烧回来。仍建议先读回留档：

```powershell
$cli='D:\Keil\CubProgram\bin\STM32_Programmer_CLI.exe'
& $cli -c port=SWD mode=normal -u 0x08000000 0x10000 <evidence>\loader-before-can-ota.bin
& $cli -c port=SWD mode=normal -u 0x08010000 0x10000 <evidence>\app-before-can-ota.bin
```

## 3. 烧可写 loader（**待验证**）

只烧 loader，**App 区留空**——让 OTA 自己完成安装、TRIAL、跳转，才是要验证的完整路径。

```powershell
& $cli -c port=SWD mode=normal -e all `
  -w D:\Project\Can-learning\ota-core\build-temp\stage-d-can\loader\loader.bin 0x08000000 -v -rst
```

烧完读 COM 横幅，确认 `CAN: normal physical 500 kbit/s`、`loader diagnostics: request 7E0, response 7E8` 且门控日志显示写入已允许。
若 EEPROM 里残留旧元数据导致状态不是 `IDLE`，用 `build_stage_d.py` 的 `wipe` 变体（`build-temp/stage-d/wipe_meta_main.c`，清零两份记录模拟空片）临时烧一次清状态，再烧回可写 loader。

## 4. 读状态（只读，安全）

```powershell
python tools\uds_tool.py status --backend can --interface pcan `
  --channel PCAN_USBBUS1 --bitrate 500000 --request-timeout 120
```

判读：
- `state` 必须是 `IDLE`（或可恢复的 `DOWNLOADING`）；不是就先 `10 02` 重算门控或清元数据
- `programming_allowed` 应为 `1`（可写 loader + `STORAGE_TRUSTED=1` 才会是 1；只读固件下 `0x22 0200` 回 `7F 22 72`）
- 记下 `active_version` / `min_version`，决定目标版本号
- `flash_bytes` 应为 `1048576`

`--log` 只对 `flash`/`install` 生效，`status` 不加。

## 5. 选包 / 生成包

- 若 `active_version < 14`：直接复用 `build-temp\stage-d\pkgs\app_v14.img2`（payload 已与 `app.bin` 逐字节核对）
- 否则生成更高版本（`0x37` 要求严格递增，见 `src/core/uds.c:429`）：

```powershell
python tools\make_image.py build-temp\stage-d-can\app\app.bin <out>.img2 --version <active+1> --sign
python tools\uds_tool.py inspect <out>.img2
```

`flash` 收的是 **IMG2 包**（32 B 头 + payload + 64 B 签名），不是裸 BIN。

## 6. 正式刷写（核心，单独留真实 CAN 证据）

```powershell
python tools\uds_tool.py flash <pkg>.img2 --backend can --interface pcan `
  --channel PCAN_USBBUS1 --bitrate 500000 --request-timeout 120 `
  --log <evidence>\can\can-ota-01.jsonl
```

记录：起止时间与总耗时、约 **88–89 个 `0x36`** 各自的响应、`0x37` 响应、`0x11` 后的开机横幅。
`flash` 默认在末尾发 `0x11` 复位；想分开观察安装窗口可加 `--no-reset` 再手动复位。
包大小 44920 B，`UDS_MAX_DOWNLOAD_BLOCK=512`（每块 510 B 数据），ISO-TP 连续帧 7 B/帧，约 **6400 个 CAN 帧**——不是 230 帧。

**S3 保活不需要**：`UDS_S3_TIMEOUT_MS=5000`，但每个请求刷新 `last_request_ms`，且同步 job 处理期间不计时（`uds.c:595/626`、`stm32_diag.c:38/175`）。仍需记录每块耗时，若出现单次 > 5 s 的间隔再考虑插 `3E 00`。

**IWDG 32 s 限制不适用**：这个变体 `OTA_ENABLE_WATCHDOG=0`、`OTA_EXPLORER_WATCHDOG_CAPABLE=0`。记录擦除耗时与 32 s 对照只作参考。

## 7. 复位后确认

```powershell
python tools\uds_tool.py status --backend can --interface pcan --channel PCAN_USBBUS1 --bitrate 500000
```

期望：`active_version` == 目标版本、`state=IDLE`、`role=application`。

再用 SWD 读回 `0x08010000` 44824 B，sha256 应等于 `47f604c1…`（与 `app.bin` 逐字节相同）；并核对 App 尾部 96 B 凭证（32 B 头 + 64 B 签名）与包内签名一致。

## 8. 证据落盘

`D:\Project\Can\stages\board-lab\evidence\can\` 下保存：`can-ota-01.jsonl`、`status-before.json`、`status-after.json`、`installed-app.bin`、SWD 读回结果、`summary.json`。
然后：新增 journal 条目 + `INDEX.md` 一行；更新 `PROJECT_SUMMARY.md`、两份 `CAN_UDS_HANDOFF.md`、学习仓库 README 的「仍未验证」表；两个仓库提交并推送。

## 9. NRC 速查（源码行号，别猜）

| 响应 | 含义 |
|---|---|
| `7F 34 22` | 会话/状态/门控不对（`uds.c:333/349`）或 `programming_allowed=0` |
| `7F 34 33` | 没解锁（`uds.c:334`） |
| `7F 36 73` | 重复块长度/内容/计数器不一致（`uds.c:385/388/390`） |
| `7F 37 31` | 版本未严格递增（`uds.c:429`） |
| `7F 37 72` | 元数据/验签/保存失败（`uds.c:423/427/434`） |
| `7F 22 72` | **烧错固件**：只读构建 `STORAGE_TRUSTED=0` 的门控，可写固件不该出现 |

## 10. 边界

- 这份 runbook 是**执行清单**，不是已完成的验证；真实 CAN 完整 OTA 仍是「未验证」。
- 终端电阻实测 160 Ω、拨码已在 `120R`，历史多次验收稳定通过，不必先调成 60 Ω。
- 真实 CAN 上的断电恢复（第 6 步之后拔 CAN 线/断电）是下一项，本轮先不合并。
- 三个不可逆操作（RDP2 / OTP 单调计数器 / WRP）**先不要动**。

## 11. 首次接线执行记录（2026-11-09 晚，`status=blocked`）

接线后 `PCAN-USB` OK、`STM32 STLink` OK，但 **`USB_232`（CH340）三个实例全 `Present=False`**，COM 列表全是蓝牙虚拟口；ST-Link 目标电压 **2.87–2.88 V**（11-07 打通时是 3.24 V）。

按顺序做完且实测：备份两块 Flash 各 64 KiB → 烧可写 CAN loader（48172 B，全片擦除+校验+复位通过，镜像字符串确认是 CAN 诊断不是 UART）→ `status` 超时 → 50 帧底层探针 / 45 s 观察窗口全部 0 回帧 → **A/B 烧回 11-07 打通过的同一份镜像（`8a383388…`）仍 0 回帧** → 主动发帧探针 0 帧（11-07 同一手法 8 s 收到 18 帧）。

**结论**：固件、构建变体、门控、PC 客户端、驱动、波特率常量全部排除；板子这一刻没有作为 CAN 节点工作。**最可能的推断（未实测）**：`USB_232` 没真正插好 → 5 V 轨没建立 → CAN 收发器不工作；2.87 V 符合弱/寄生供电。3.3 V 域有电不能反推 5 V 有电。

**下次继续前必须先满足**（缺一不要跑 `flash`）：

1. 设备管理器出现真实的 `USB-SERIAL CH340 (COMx)`，且 `/dev` 列表里它不是蓝牙口；
2. 用电压档量板子 **5 V ↔ GND ≈ 5 V**（这是唯一没实测的关键项）、3.3 V ↔ GND ≈ 3.3 V；ST-Link 目标电压回到 ≈3.24 V；
3. 重新跑第 1 节 `can_gate_probe.py` 链路自检，能看到 `0x7E8`；
4. 读 COM 开机横幅，确认 `CAN: normal physical 500 kbit/s` 与 `loader diagnostics: request 7E0, response 7E8`。

**两个新坑**（已写进 `journal/entries/2026-11-09-can-ota-bringup-ab-rules-out-firmware.md`）：

- 失败态探针会**覆写已提交的 `evidence/can/*.json`**（`passed` → `failed`）。本轮已 `git checkout` 还原，并把失败结果单独存为 `bringup_20261109_ab.json`；以后跑实验态脚本先改输出路径。
- `CAN_GetStatus = 0x0` / `bus.state = ACTIVE` **不是 ACK 证据**，兼容适配器不上报 ACK 错误；不能以「状态 OK」当作链路已建立。

板子与两仓库已完整回滚：探针残留 0、MDK 恢复 `--uart` 版、diagnostics bin 恢复 `8a383388…`、板子烧回原版只读固件、工作树干净。
