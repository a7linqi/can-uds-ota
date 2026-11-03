#include <stdint.h>
extern const uint32_t __Vectors[];
/* Called by ST startup BEFORE data/BSS initialization: use no globals. */
void SystemInit(void)
{
    *(volatile uint32_t *)0xE000ED08u=(uint32_t)__Vectors;
    *(volatile uint32_t *)0xE000ED88u|=0xFu<<20; /* F407 CP10/CP11 access */
    __asm volatile("dsb 0xF\nisb 0xF":::"memory");
}
/* Clocks/GPIO/SysTick belong to the real BSP, not this VTOR-only hook. */
