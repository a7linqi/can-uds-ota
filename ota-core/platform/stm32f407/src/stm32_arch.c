#include "ota_stm32.h"
#define REG32(a) (*(volatile uint32_t *)(a))
uint32_t ota_arch_irq_save(void)
{
    uint32_t value;
    __asm volatile("mrs %0, primask\ncpsid i":"=r"(value)::"memory");
    return value;
}
void ota_arch_irq_restore(uint32_t value)
{ __asm volatile("msr primask, %0"::"r"(value):"memory"); }
void ota_arch_irq_enable(void) { __asm volatile("cpsie i":::"memory"); }
void ota_arch_wait(void) { __asm volatile("wfi"); }
void ota_arch_reset(void)
{
    __asm volatile("dsb 0xF":::"memory");
    REG32(0xE000ED0Cu)=0x05FA0004u | (REG32(0xE000ED0Cu)&0x700u);
    __asm volatile("dsb 0xF":::"memory");
    for(;;) ota_arch_wait();
}
__attribute__((naked,noreturn))
static void branch_to_reset(uint32_t stack __attribute__((unused)),uint32_t reset __attribute__((unused)))
{
    __asm volatile(
        "msr msp, r0\n"
        "movs r2, #0\n"
        "msr psp, r2\n"
        "msr control, r2\n"
        "isb 0xF\n"
        "bx r1\n");
}
int ota_arch_can_handoff(void)
{
    uint32_t ipsr,control;
    __asm volatile("mrs %0, ipsr":"=r"(ipsr));
    __asm volatile("mrs %0, control":"=r"(control));
    return ipsr==0u && control==0u;
}
void ota_arch_handoff(uint32_t stack,uint32_t reset)
{
    uint32_t i;
    /* This bare-metal handoff must not run from an ISR/PSP/unprivileged task. */
    if(!ota_arch_can_handoff()) for(;;) ota_arch_wait();
    (void)ota_arch_irq_save();
    REG32(0xE000E010u)=0u; REG32(0xE000E014u)=0u; REG32(0xE000E018u)=0u;
    for(i=0u;i<3u;i++) {
        REG32(0xE000E180u+4u*i)=0xFFFFFFFFu;
        REG32(0xE000E280u+4u*i)=0xFFFFFFFFu;
    }
    REG32(0xE000ED04u)=(1u<<27)|(1u<<25);
    REG32(0xE000ED08u)=APP_BASE_ADDR;
    __asm volatile("movs r2, #0\nmsr basepri, r2\nmsr faultmask, r2\ndsb 0xF\nisb 0xF":::"r2","memory");
    /* No C stack accesses after MSP changes. IRQs remain masked until the
     * app's C runtime is initialized and app main explicitly enables them. */
    branch_to_reset(stack,reset);
}
