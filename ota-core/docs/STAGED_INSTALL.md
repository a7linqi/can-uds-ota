# 已提交包的恢复安装

PC 工具新增 `install`，用于设备已经持久化 READY 包、但下载提交应答丢失，或者此前使用 `flash --no-reset` 的场景。

~~~powershell
python tools/uds_tool.py status --backend can --interface gs_usb --channel "你的实际通道" --index 0
python tools/uds_tool.py install app-v1.img --backend can --interface gs_usb --channel "你的实际通道" --index 0 --flash-kib 1024
~~~

两条都是未来真机使用方式，当前没有对硬件执行。`install` 是显式的安装/复位操作；默认门控未开或状态不符合时不会成功。

处理顺序为：本地包预检 → 只读状态查询 → 确认容量、READY、版本、payload 长度和 CRC 匹配 → 编程会话与演示解锁 → `0x31` 验证当前外部包 → `0x11` 复位。不会重新发 `0x34/0x36/0x37`，也不会重新擦写下载区。复位后的 Bootloader 仍会按正常流程备份旧 App、安装和试启动；这些必要的安装写入并未跳过。

安装结果仍标记 `application_health_confirmed: false`。PC 不能代替运行中的 App 做健康确认。匹配的 CRC 也不等于签名验证，工程尚未实现签名。

如果复位应答丢失，设备可能已经进入 TRIAL。工具报告复位状态未知，停止自动重试。反复重发复位可能消耗试启动次数，不能把丢应答等同于“没有复位”。先查询状态、观察 App 健康行为，再决定下一步。

本功能已通过 PC→实际 C 核心测试：丢 `0x77` 后直接安装、状态/镜像不匹配拒绝、容量不匹配拒绝、丢复位应答后试启动次数仍为 1。事件日志包含请求 SID 和长度，不记录密钥/整包数据。

仿真器每次 CLI 启动都会新建 RAM 存储，因此单独运行 `install --backend sim` 没有先前 READY 包会被拒绝。跨步骤场景在同一 C 仿真进程中的自动化用例里验证；真实设备的状态由 EEPROM/NOR 持久化。

可直接运行 `python tools/demo_recovery.py --cc "编译器完整路径"` 复现两种丢应答情况，结果写入 `evidence/pc-recovery.json`。
