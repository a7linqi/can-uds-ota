# UDS 说能收 512 字节，BSP 只肯收页内——阶段 C 之前先修掉的跨页缺陷

## 1. 一句话

`0x34` 告诉测试仪"我一帧最多收 512 字节"，但 BSP 的写钩子直接把它交给 `nor_spi_program_page`，
而后者严格拒绝跨 256 字节页边界；510 字节的数据帧在真板子上必然被拒。阶段 B 恰好用 256 字节，
把这个缺口藏了一整天。

## 2. 背景

T5 阶段 C 的目标是"擦一个扇区、写满 4 KiB、读回一致"。前一轮阶段 B 只写了 256 字节，
`36 01` 一次就过。要写 4 KiB 就必须多发几帧，而 `0x34` 的正响应 `74 20 02 00` 里
`maxNumberOfBlockLength = 0x0200`，也就是 512 字节（含 SID 和块序号，数据位 510 字节）。
自然的选择就是每帧 510 字节、共 9 帧。

## 3. 现象

还没上板，先读实现时发现两处对不上：

```
src/driver/spi_nor.c: nor_spi_program_page()
    if(!data || !valid(d,address,n) || n>NOR_PAGE_BYTES-address%NOR_PAGE_BYTES)
        return NOR_ERR_ARGUMENT;                       /* 严格页内 */

platform/stm32f407/board/explorer_reference/explorer_reference.c:
    int ota_user_nor_page_program(uint32_t a,const uint8_t *p,uint32_t n)
    { return nor.ready?nor_spi_program_page(&nor,a,p,n):-1; }   /* 直接透传 */
```

地址 0 + 510 字节，`256-0=256 < 510`，直接 `NOR_ERR_ARGUMENT`。也就是说阶段 C 一上板，
第 1 帧就会回 `NRC 0x72`，而 `0x34` 刚刚才声明过 512。

## 4. 排查

三个问题依次问下来：

1. **为什么阶段 B 没暴露？** 阶段 B 用 `--size 256`，一帧数据正好 256 字节，地址 0 处
   `256-0=256 >= 256`，刚好在页内，通过。
2. **为什么主机测试没暴露？** `tests_driver/test_spi_nor.c` 里有一个 `bridge_write()`，
   它充当"BSP 替身"。这个替身**自己写了拆页循环**：

   ```c
   while(n) { uint32_t count=256u-off%256u; ... nor_spi_program_page(...); }
   ```

   也就是说，测试里的 BSP 比真 BSP 更正确，于是测试替真 BSP 通过了本该失败的事。
   这不是测试太少，是替身太聪明。
3. **芯片真的会怎样？** W25Q 的 `0x02` 页编程命令在页内回卷：地址越过 255 会绕回页首，
   而不是进下一页。测试的芯片模型正是这样实现的（`&255`），所以一旦漏了拆页，
   跨页写入会**静默覆盖页首 254 字节**，读回校验发现的只是"某段不对"，很难指向根因。

## 5. 根因

不是某一层写错，是**同一个契约在两层被实现成了两种语义**：

- 产品核心到 BSP 的 `flash_port_t.write(user, off, data, n)` 语义是"任意长度，你来拆"。
  测试替身按这个实现了，产品真 BSP 没实现。
- `nor_spi_program_page` 的严格是对的：芯片命令本身有页内回卷，放松它等于把静默数据损坏
  引进来。要拆页的是调用方，不是它。

## 6. 修复

驱动加一个组合子，语义与 `flash_port_t.write` 对齐，严格性留在原函数：

```c
int nor_spi_write(nor_spi_t *d,uint32_t address,const uint8_t *data,uint32_t n)
{
    uint32_t chunk;
    int result;
    if(!data || !valid(d,address,n)) return NOR_ERR_ARGUMENT;
    while(n) {
        chunk=NOR_PAGE_BYTES-(address%NOR_PAGE_BYTES);
        if(chunk>n) chunk=n;
        result=nor_spi_program_page(d,address,data,chunk);
        if(result) return result;
        address+=chunk; data+=chunk; n-=chunk;
    }
    return 0;
}
```

三处改用它：BSP 钩子 `ota_user_nor_page_program`、测试的 `bridge_write`、
新加的 `cross_page_write()` 用例。**把替身改成与实物同一条代码路径**，是这次真正的修复；
只改 BSP 的话，下次换个替身还会重演。

## 7. 验证

| 项 | 结果 |
|---|---|
| 红：把 `nor_spi_write` 退化成单次 `program_page` | 740 项里 **5 项失败**（510 字节、非对齐 200+100、脏页部分写） |
| 绿：正确实现 | **740 checks / 0 failures**（原 720，新增 20 项） |
| 仿真后端阶段 C | 9 帧全 `76 xx`，末帧 16 字节，`37` 回 `NRC 0x72` |
| 实机阶段 C | `0x34` **13.844 s** 擦 1 MiB；9 帧 `0x36`（510×8 + 16），**2.66 s**，每帧 0.33 s |
| 器件判决 | 整片 16 MiB CRC32 **`0x3EAD7229`** == 预测，`crc_match: true`，66.0 s 扫描 |

整片 CRC 相等这一条同时钉住三件事：擦除范围是 1 MiB、4096 字节递增图案（0x30,0x31,…）
从偏移 0 起逐字节正确、槽外 15 MiB 没动。如果跨页真的回卷覆盖了页首，CRC 不可能相等。

## 8. 学到

- **契约要在每一层用同一个实现落地**。测试替身比实物聪明，等于把缺陷藏进替身里。
- 芯片手册里"页内回卷"这种细节，只有在驱动层强制页内才安全；上层随便给长度是常态，
  所以拆分函数必须存在，而不是靠调用方自觉。
- 声明出来的能力必须真的可用：`0x34` 声明 512，就得能收 512。做不到时有两个正当选择
  ——改声明，或者改实现；把两者留着不一致是最坏的。

## 9. 简历表述

发现并修复 UDS 下载路径的跨页写入缺陷：`0x34` 声明的 512 字节块与 W25Q128 的 256 字节页边界
冲突，页编程命令在页内回卷会静默覆盖页首。在驱动层增加按页拆分的组合子，严格性保留在原语上，
并把测试里"自己拆页的 BSP 替身"改为调用真实实现；用退化实现先造 5 项失败、再跑 20 项新断言全绿，
实机以器件自算 16 MiB CRC32 与预测逐字节相等作为验收。

## 10. 边界

- 真实擦写只在 `DOWNLOAD_OFFSET` 起 1 MiB 槽内，已按金标 32 块定向恢复回 `0xA4188821`。
- `0x37` 仍按设计被拒（载荷不是合法包），状态没有进 `UPDATE_READY`，bootloader 没参与。
- EEPROM 元数据在 `UPDATE_DOWNLOADING`（`22 02 00` 第二字节 = 1），仍无恢复工具。
- 阶段 D（完整签名包 + `0x31` + `0x11`）未做；`hardware_verified` 仍是保守的 `false`（机器可读
  证据字段只覆盖主机/目标构建），实机结论以 board-lab 证据目录为准。
