# PC 诊断与刷写工具

这部分可以在板子到货前运行。PC 使用 Python；ECU 是子进程中实际编译运行的 C 核心，复用工程的 ISO-TP、UDS、存储和 Bootloader。模拟 CAN 不依赖 python-can，时钟为虚拟时钟，不能据此计算真实刷写速度或总线负载。

## 先运行闭环演示

在工程根目录执行，`--cc` 填可用的 C99 编译器路径。TinyCC 自动使用 `-run`；GCC/Clang 会先生成仿真器，再启动它。

~~~powershell
python tools/demo_roundtrip.py --cc "你的 C 编译器完整路径"
python run_pc_checks.py --cc "你的 C 编译器完整路径"
python tools/uds_tool.py info --cc "你的 C 编译器完整路径"
python tools/uds_tool.py status --cc "你的 C 编译器完整路径"
~~~

演示依次执行：确认 V1 → V2 未确认而回滚 V1 → 确认 V2 → 下载 V3 时注入存储断电 → 重启仍保留 V2 → 重新完整下载并确认 V3。结果保存在 `evidence/pc-roundtrip.json`。

演示镜像只有用于检查的向量和填充字节，不能写入 MCU。仿真器没有执行 App 指令；`confirm_app_health()` 是测试夹具，直接调用 C 的确认入口。真实固件必须由 App 自检后调用该入口。

## 从真实 App BIN 生成升级包

App 必须链接到 `0x08010000`，导出的 BIN 从向量表开始，不包含 IMG2 包头。

~~~powershell
python tools/make_image.py app.bin app-v1.img --version 1 --flash-kib 1024
python tools/uds_tool.py inspect app-v1.img --flash-kib 1024
python tools/uds_tool.py flash app-v1.img --backend sim --cc "你的 C 编译器完整路径"
~~~

ZG 使用 1024 KiB，ZE 使用 512 KiB。预编译仿真器可用 `--ecu-bin` 指定，必须自行确认其容量配置与 `--flash-kib` 一致。每次 CLI 启动仿真器都会创建全新 RAM 存储；跨多次升级的持久状态演示使用上面的 `demo_roundtrip.py`。

## USB-CAN 接入

真实后端通过可选依赖 python-can 接入，当前没有实物验证。实际 interface/channel/index 取决于适配器固件、系统驱动和 python-can 后端；请按对应后端文档核对，不能把示例通道当成你的设备通道。

~~~powershell
python -m pip install "python-can[gs-usb]>=4.6,<5"
python tools/uds_tool.py info --backend can --interface gs_usb --channel "你的实际通道" --index 0 --bitrate 500000
~~~

目前只能先用 `info` 核对标准帧请求 `0x7E0` / 应答 `0x7E8`。固件默认关闭写入门控；真实板卡的驱动、擦写耗时、待响应机制和看门狗验证完成前，不要把仿真通过当作真机可刷写的依据。PythonCanTransport 只测试了驱动调用契约，没有安装或验证实际 USB 驱动。

工具另外做软件过滤，因为 gs_usb 不支持设备侧报文过滤。该后端的 `send(timeout)` 不支持超时中断；客户端会传入剩余期限并在返回后检查，但不能强行打断被驱动阻塞的调用。因此总期限覆盖协议状态机的等待，不是对所有第三方驱动调用的硬实时保证。

## 工具处理的边界

| 情况 | 行为 |
|---|---|
| 包长度、向量、头 CRC 或 payload CRC 不合格 | 打开 CAN 之前拒绝 |
| `0x34` 最大块长为 512 | 每块最多 510 数据字节，另外 2 字节为 SID/BSC |
| BSC 从 255 回绕 | 下一块为 0，再下一块为 1 |
| `0x36` 应答超时 | 默认重发同一块 1 次，可用 `--retries 0..3` 调整 |
| `0x37` 提交应答丢失 | 报告提交状态未知，停止自动操作；不自动重发或重启刷写 |
| `0x78` 等待响应 | 保持当前请求，等待最终应答；不重新发送请求 |
| FC、STmin、分帧或连续等待消耗过久 | 受每次 UDS 请求的总期限限制，默认 120 秒，可用 `--request-timeout` 调整 |
| `0x11` 应答完成 | 只说明复位请求已被确认，不代表新 App 健康或升级最终成功 |

`flash` 默认发复位；`--no-reset` 只下载并验证 READY 包。事件日志默认写入 `runs/`，可用 `--log 路径` 指定。SHA256 仅作文件标识；包仍只有 CRC32 和版本策略，没有签名。0x27 是演示算法，不能作为量产安全机制。

`status` 只读查询本工程 DID 0x0200，用于观察 READY/TRIAL、版本、长度和 CRC，详见 `UPDATE_STATUS.md`。它不做镜像扫描，也不代替 App 健康确认。

`install IMAGE` 在本地镜像与设备 READY 状态匹配时验证并复位安装，不重复下载；详见 `STAGED_INSTALL.md`。提交或复位应答丢失时都不会自动重发相关操作。

参考：
- [python-can gs_usb 后端文档](https://python-can.readthedocs.io/en/stable/interfaces/gs_usb.html)
- [DiagnosticSessionControl 时序字段实现参考](https://udsoncan.readthedocs.io/en/v1.21.2/_modules/udsoncan/services/DiagnosticSessionControl.html)：P2 单位 1 ms，P2* 单位 10 ms。
