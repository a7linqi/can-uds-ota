#include <stdio.h>

#include "test_util.h"

int g_checks = 0;
int g_failures = 0;
int g_fault_scenarios = 0;

void run_crc_tests(void);
void run_isotp_tests(void);
void run_uds_tests(void);
void run_protocol_edge_tests(void);
void run_watchdog_tests(void);
void run_boot_tests(void);
void run_signature_tests(void);

int main(void)
{
    setvbuf(stdout, 0, _IONBF, 0);
    printf("ota-core host tests\n");

    run_crc_tests();
    run_isotp_tests();
    run_uds_tests();
    run_protocol_edge_tests();
    run_watchdog_tests();
    run_signature_tests();
    run_boot_tests();

    printf("\n%d checks, %d failures\n", g_checks, g_failures);
    printf("%d storage power-cut scenarios (host model)\n",g_fault_scenarios);
    return g_failures == 0 ? 0 : 1;
}
