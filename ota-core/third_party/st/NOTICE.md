# Third-party source attribution

- startup_stm32f407xx.s is from STMicroelectronics CMSIS device templates
  shipped in Keil.STM32F4xx_DFP 2.16.0 (2017 ST copyright header retained).
  The per-file header specifies BSD-3-Clause, included alongside this file.
  Project changes: stack changed from 1 KiB to 4 KiB, heap changed to zero;
  a modification notice was added at the top. Vector order is unchanged.
- tools/templates/target_option.xml is based on project-option metadata in
  the same pack's basic MCBSTM32F400/Blinky example; device, compiler,
  memory ranges, source groups and paths are replaced by generate_mdk.py.
- Optional Explorer firmware is compiled from ST's installed HAL, device
  SystemInit and GNU startup sources in STM32F4xx_DFP 2.16.0. Their per-file
  headers carry the 2017 STMicroelectronics copyright and BSD-3-Clause terms,
  reproduced in BSD-3-Clause.txt. Complete installed vendor source packs are
  not included. The project supplies its own Explorer board glue.
- Packaged ELF/BIN files contain the resulting compiled vendor components.
  CMSIS and GNU/Newlib runtime notices are in ../cmsis and ../runtime.

Official references:
- https://www.st.com/resource/en/reference_manual/rm0090-stm32f4xx-reference-manual-stmicroelectronics.pdf
- https://github.com/STMicroelectronics/stm32f4xx-hal-driver
