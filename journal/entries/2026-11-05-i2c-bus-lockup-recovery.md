# I2C 总线锁死：9 个时钟脉冲救回一块"变砖"的板子

## 1. 一句话

反复"关串口即复位"把 EEPROM 的 I2C 从机留在半传输状态，SDA 被它拉死，整块板子的 OTA 能力降级成"每次启动都停在 loader"；
在 I2C 初始化前插一段 9 时钟 + STOP 的总线清除，同一块板、同一个器件，一次修复。

## 2. 背景

2026-11-05 下午，准备做 TRIAL 失败回滚实验，动手前先读一次状态 DID 当基线。
`22 02 00` 返回 NRC `0x72`（generalProgrammingFailure），连续三次都是。

前一天已经记过一句"EEPROM 6 次复位里 2 次读失败，未定位"，当时当成偶发。
这次不是偶发：3/3 全失败，而且后面越试越多。

## 3. 现象

同一时刻、同一根串口：

| 请求 | 结果 |
|---|---|
| `10 01` | `50 01 00 32 01 F4` 正常 |
| `10 03` | `50 03 00 32 01 F4` 正常 |
| `22 F1 90`（VIN） | `62 F1 90 …` 正常 |
| `22 02 00`（状态） | **NRC `0x72`** |

UDS 栈是活的，只有要读元数据的那一条挂。

复位后抓串口横幅，板子自己给出了答案：

```
jedec_id=0x00EF4018              ← NOR 认得
nor_read_crc32=0xA4199799        ← NOR 读得动
EEPROM read failed               ← 只有 EEPROM
eeprom_crc32=0x00000000
loader: storage not ready, staying in the loader
```

NOR 完全健康，EEPROM 整条通道死了。

## 4. 排查（含弯路）

**弯路一：先怀疑元数据内容坏了。**
`metadata_load` 的返回值有三态——`1` 读到、`0` 没有有效记录、`-1` IO 错误。
如果只是两份记录都坏，返回值应该是 `0`，UDS 会走"清空状态"分支，回一个 `metadata_present=0` 的正常响应。
实际回的是 `0x72`，对应 `append_did()` 里的 `-3`，也就是 `loaded<0`。**这是 IO 错误，不是内容错误。**

**弯路二：怀疑 storage_ready 判断有问题。**
`eeprom_read()` 第一步就是 `if(!p->storage_ready) return -1;`，而 `storage_ready` 要求
`storage_verified && eeprom_bytes==256 && eeprom_page_bytes==8` 全成立。
顺着查到 `ota_bsp_init` 里那次 256 字节探测读取——横幅上那行 `EEPROM read failed` 就是它。
所以不是判断逻辑错，是**探测本身失败**。

**关键一步：把 NOR 和 EEPROM 分开看。**
两者共用一次 `read_storage_identity()`，但 NOR 是 SPI、EEPROM 是 I2C。
横幅里 NOR 的 `jedec_id` 和 `nor_read_crc32` 每次都正常，只有 EEPROM 挂——
排除了"整块存储子系统没起来"，问题锁在 I2C 这一条总线上。

## 5. 根因

I2C 从机（24C02）在传输中途被复位，会把 SDA 一直拉低等着下一个时钟。
主机这边 `HAL_I2C_Init` **无法**恢复这个状态：I2C 的 START 条件本身就是"SCL 高时把 SDA 拉低"，
而 SDA 已经被从机拉低了，主机产生不了 START，于是每一次事务都超时。

那从机为什么会在半传输状态？因为**复位正好落在 EEPROM 写的中途**。
而这个项目里"关串口 = 复位"：CH340 的 DTR/RTS 接了 NRST。
我一整天都在反复"读状态 → 关串口 → 复位"，等于持续对着 EEPROM 的写窗口掷骰子。

BSP 里其实早就有一句注释推断过这件事：

> a reset issued during an EEPROM write left the bus stuck for at least one boot

当时写成"at least one boot"是错的——**它是永久的**，因为每次启动都只是重新 `HAL_I2C_Init`，没人去解锁总线。

## 6. 修复

在 `gpio_init()` 之后、`peripherals_init()` 之前插一个 `i2c_bus_clear()`：

1. 把 PB8/PB9 从 AF4 切成 GPIO 开漏输出
2. 释放 SDA（写高）
3. 在 SCL 上打 **9 个**时钟（9 是 I2C 规范里"一个字节 + ACK"的位数，足够让从机把当前字节吐完）
4. 发 STOP：SCL 高时 SDA 由低变高
5. 切回 AF4，交给 `HAL_I2C_Init`

时机也重要：必须在 I2C 外设初始化**之前**做，否则外设已经带着一个坏状态起来了。

## 7. 验证（全部实机）

修复前 → 修复后，同一块板、同一个 EEPROM、同一条串口：

| | 修复前 | 修复后 |
|---|---|---|
| 横幅 | `EEPROM read failed` | `EEPROM 256-byte read complete` |
| `eeprom_crc32` | `0x00000000` | `0x316AB2D3` |
| loader 判决 | `storage not ready, staying in the loader` | `selecting boot target` → `handing off to the app at 08010000` |
| 状态 DID | NRC `0x72` | 正常 44 字节记录，`active_version=11` |

**排除"碰巧重启好了"**：NOR 的 `jedec_id=0x00EF4018` 与 `nor_read_crc32=0xA4199799` 在修复前后完全一致，
说明掉的电、起的电、挂的总线都是同一个环境，唯一变化是新增的 9 个时钟。

`eeprom_crc32=0x316AB2D3` 也不是巧合：这个值在项目里被记录过，是"两条自洽记录同时存在"时的常数
（`CRC32(m ‖ CRC32(m))` 对定长 m 是常数），说明读回来的是真的元数据，不是一片 0xFF。

**全程没有断过电。** 这是软件恢复相对"拔插头"的核心价值：现场设备不允许断电。

修复后 loader 与 App 两段横幅都是 `EEPROM 256-byte read complete`，
说明同一份 BSP 代码在两边都生效。

## 8. 学到

- **`HAL_I2C_Init` 不负责解锁总线。** 总线被从机拉死之后，初始化只会带着坏状态起来，错误看起来像"器件坏了"。
- **"偶发"往往是概率，不是随机。** 2/6 那次不是玄学，是复位落在写窗口里的概率；一旦踩中就永久锁死，不踩中就正常。
- **三态返回值的价值。** 如果 `metadata_load` 把"读不到"和"没有记录"混成一个值，这次会被误判成"元数据损坏"，方向全错。
- **NOR 和 EEPROM 要分开判。** 同一个 `storage_ready` 覆盖了两个物理完全不同的器件，是它们的横幅数字把人救了。

## 9. 简历表述

> 定位并修复 I2C EEPROM 总线锁死：MCU 在 EEPROM 写期间复位导致从机拉死 SDA，
> `HAL_I2C_Init` 无法恢复，整机 OTA 状态机降级为"每次启动停在 loader"；
> 在 I2C 初始化前实现 9 时钟 + STOP 的总线清除，实机验证 EEPROM 恢复、系统自愈，全程无需断电。

## 10. 边界

- 没有验证"EEPROM 器件物理损坏"的情况——那种情况下总线清除无效，现象会是修复后仍然失败。这次恰好是可恢复的锁死。
- 总线清除只在**上电/复位时**执行一次。运行中再次锁死（理论上不会，因为运行中不会复位）没有重试路径。
- 9 个时钟的宽度用 `__NOP()` 空循环凑，实测可用但没有示波器量过实际频率；
  按 168 MHz、400 次 NOP 估算半周期约 2–4 µs，对应 125–250 kHz，在 24C02 的 400 kHz 上限内。
- 没有验证总线清除在"总线本来正常"时是否完全无副作用——只能从"修复后一切正常"反推它没有破坏正常事务。
