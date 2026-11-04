# NOR 恢复演练工具链：擦写 + 自校验 + 断电收敛

- 日期：2026-11-04
- 类型：功能
- 关键词：`nor` `swd-mailbox` `crc32` `幂等` `断电`
- 验证等级：**主机模型**（全量）+ **实机**（只读核验）

## 1. 一句话

在第一次真实擦除外部 NOR 之前，先造出"能把出厂内容原样写回、并且能证明写对了"的能力。

## 2. 背景与约束

板上 NOR 存着出厂 FAT16 内容（图片/音乐/视频/触摸校准），全片只做过**一次**两遍一致的 dump，是全片内容的唯一副本：

| 项 | 值 |
|---|---|
| 镜像 | `backups/nor-original/full/20261103_200408_779736/pass1/pass1.bin` |
| 大小 | 16777216 B |
| SHA256 | `929af96a…05e1` |
| CRC32 | `0xA4188821` |

约束：
- **不得重复全片读**（实测 3 小时：SWD 读 8192 B 平均 2.2 s → 3.63 KB/s）；
- 不能用"再读一遍"当验收，否则一次演练要 3 小时；
- 第一次真实擦除前必须跑通恢复演练。

## 3. 现象 / 目标

要回答一个问题：**如果把 NOR 擦坏了，能不能在 90 分钟内把它变回原样，并且有证据？**

## 4. 排查过程（设计取舍）

| 取舍点 | 备选 | 选定与理由 |
|---|---|---|
| 块大小 | 8 KiB / 32 KiB | **32 KiB**：传输瓶颈是 SWD 每次 CLI 调用的固定开销，块越大调用越少（2048→512 块） |
| 校验方式 | 主机再读一遍 / 器件自算 | **器件自算整片 CRC32**：SPI 读 16 MiB 只要 66 s，且校验的是芯片里的真实内容 |
| 断电后 | 续传 / 从头重来 | **从头重来**：靠"先擦后写 + 幂等"收敛，比信任半途状态可靠 |
| 提交信号 | status 轮询 / ack 门铃 | **ack 门铃**（见第 5 节） |

**转折点**：发现"上一块的 `BLOCK_OK` 会一直留在 `status` 里"，主机若"看到 BLOCK_OK 就当成功"，会在设备根本没收到新块时误判通过。

## 5. 根因 / 关键机制

1. **ack 门铃**：主机写非 0 值提交；设备消费后把它清回 0，然后才发布 READY/BLOCK_OK/DONE。因为主机只写、设备只清，"ack==0"是设备独有的"我收到了"凭证。
2. **先验后动**：块 CRC 不对时**一个字节都不碰 NOR**（错误码 `BLOCK_CRC`），避免"写了一半才发现数据不对"。
3. **逐页回读**：每写 256 B 立刻用 `0x03` 读回比对，失败给 `VERIFY` 而不是等到最后。
4. **整片 CRC 期间 status 保持 BUSY**：此时发布 DONE 就是撒谎。

## 6. 修复 / 实现

- `stages/board-lab/firmware/w25q_bench.c`：可注入 SPI/CS/延时的 W25Q 驱动；WREN 后**回读 WEL**（CS 坏了就 fail-closed），擦写都等 WIP，超时有界。
- `stages/board-lab/firmware/nor_restore.c`：协议 v3 状态机，无 HAL 依赖；`WAIT→READY→ERASE→PROGRAM→VERIFY_PAGE→COMMIT→FINAL_CRC`。
- `stages/board-lab/firmware/nor_restore_sim.c`：把**同一份 C** 导出成 DLL，供主机模拟器驱动（不是用 Python 重写逻辑）。
- `stages/board-lab/host/nor_restore.py`：`sim` / `--hardware` 双后端；`--mode verify` 只读核验；`--cut-at-block/--cut-phase` 注入断电。
- `stages/board-lab/build_restore.py`：交叉编译并把产物绑定到冻结核心哈希。
- `stages/board-lab/tests/*`：驱动测试、状态机测试、断电矩阵、16 MiB 金标模拟。

备选方案：在 Python 里重写一遍状态机做模拟。放弃原因——两套实现必然发散，且验证不到真实的 C 逻辑。

## 7. 验证

```
python D:\Project\Can\stages\board-lab\tests\run_restore_checks.py

# 实机只读核验（需先烧 nor_restore_WRITE.bin）
python stages\board-lab\host\nor_restore.py --image <golden.bin> ^
  --expect-crc32 0xA4188821 --mode verify --hardware --mailbox-addr 0x200003B0
```

| 项 | 期望 | 实际 |
|---|---|---|
| 断电矩阵（8 块 × 擦/写/回读 + 整片扫描掉电） | 每个切点都"掉电→重跑→逐字节一致" | 25 个切点全通过 |
| 16 MiB 金标模拟恢复 | 器件 CRC = 金标 CRC | `0xA4188821`，4096 扇区 / 65536 页，`program_without_erase=0` |
| 只读 dump 固件可复现 | 重建后哈希不变 | `af69df8e…` 与重建前一致 |
| **实机只读核验** | 器件自算整片 CRC = 金标 | **`0xA4188821`，66.0 s，`crc_match=true`** |

未验证：**擦写本体没在真机上跑过**（推算 85–90 分钟：32 KiB `-w` 实测 8.33 s → 3.9 KB/s）。模拟里的"断电"是模型行为，不等于真实掉电。

## 8. 学到的东西

1. **验收标准要选"不可伪造"的那个**：不看"有没有报错"，只看器件自己算出的整片 CRC。
2. **幂等比续传更好做也更可靠**：不保存中间状态，每次从第 0 块重来，靠"先擦后写"收敛。
3. **门铃式握手**：主机写、设备清，解决"状态字段被上一轮污染"的经典竞态。这个坑在写代码时就该想到，我是先写错再发现的。
4. **硬件会暴露纯软件模型永远看不到的东西**：`-w32` 写完会自己回读校验，而设备立刻清了 `ack`，CLI 于是报 `Failed to download data!`——但字其实写进去了。

## 9. 简历表述

> 为 16 MiB 外部 NOR 设计并实现"可恢复"擦写链路（自定义 SWD mailbox 协议 v3 + 32 KiB 块 + ack 门铃握手），在 25 个断电切点上验证"掉电即重跑、逐字节收敛"，实机由器件自算整片 CRC32 与金标镜像一致（66 s 完成全片校验，替代 3 小时的 SWD 重读）。

## 10. 边界与未解决

- 擦写本体未上板（等供电稳定 + 时间窗口）。
- `-w` 写 32 KiB 到 SRAM 的 CLI 行为只在这块机器上验证过，换机器需重测。
- 产品路径上的 `ota_user_nor_erase_4k/page_program` 仍是返回 -1 的空实现，与本工具链无关（那是维护端的活）。
