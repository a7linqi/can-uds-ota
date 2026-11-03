/* NOR backup bring-up image: read-only 16 MiB NOR dumper in the boot region. */
ENTRY(Reset_Handler)
MEMORY
{
  FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K
  RAM (xrw) : ORIGIN = 0x20000000, LENGTH = 128K
}
_estack = ORIGIN(RAM) + LENGTH(RAM);
SECTIONS
{
  .isr_vector : { . = ALIGN(4); KEEP(*(.isr_vector)) . = ALIGN(4); } > FLASH
  .text :
  {
    . = ALIGN(4);
    *(.text .text.*) *(.rodata .rodata.*)
    KEEP(*(.init)) KEEP(*(.fini))
    . = ALIGN(4);
  } > FLASH
  .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } > FLASH
  .ARM.exidx :
  {
    __exidx_start = .; *(.ARM.exidx* .gnu.linkonce.armexidx.*) __exidx_end = .;
  } > FLASH
  .preinit_array :
  {
    PROVIDE_HIDDEN(__preinit_array_start = .);
    KEEP(*(.preinit_array*))
    PROVIDE_HIDDEN(__preinit_array_end = .);
  } > FLASH
  .init_array :
  {
    PROVIDE_HIDDEN(__init_array_start = .);
    KEEP(*(SORT(.init_array.*))) KEEP(*(.init_array))
    PROVIDE_HIDDEN(__init_array_end = .);
  } > FLASH
  .fini_array :
  {
    PROVIDE_HIDDEN(__fini_array_start = .);
    KEEP(*(SORT(.fini_array.*))) KEEP(*(.fini_array))
    PROVIDE_HIDDEN(__fini_array_end = .);
  } > FLASH
  .data :
  {
    . = ALIGN(4); _sdata = .;
    *(.data .data.*) *(.ramfunc .ramfunc.*)
    . = ALIGN(4); _edata = .;
  } > RAM AT> FLASH
  _sidata = LOADADDR(.data);
  .bss (NOLOAD) :
  {
    . = ALIGN(4); _sbss = .;
    *(.bss .bss.*) *(COMMON)
    . = ALIGN(4); _ebss = .;
  } > RAM
  .stack_reserve (NOLOAD) :
  {
    . = ALIGN(8); _end = .; PROVIDE(end = .);
    . += 4096; . = ALIGN(8);
  } > RAM
  /DISCARD/ : { *(.note.GNU-stack) }
}
ASSERT(ADDR(.isr_vector) == 0x08000000, "Vector table is not at the boot image base")
ASSERT(SIZEOF(.isr_vector) >= 8, "Boot vectors are missing")
ASSERT(_sidata + SIZEOF(.data) <= 0x08010000, "Image exceeds the 64 KiB boot region")
ASSERT(_end + 4096 <= _estack, "RAM does not leave the reserved 4 KiB stack")
