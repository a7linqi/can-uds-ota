#include "test_util.h"
#include "ota/crc32.h"

#include <string.h>

void run_crc_tests(void)
{
    /* known answer: crc32("123456789") = 0xCBF43926 */
    const char *check = "123456789";
    uint8_t a[4] = {0x00u, 0x01u, 0x02u, 0x03u};
    uint8_t b[4] = {0x04u, 0x05u, 0x06u, 0x07u};

    printf("[crc]\n");
    TEST_ASSERT(crc32_calc((const uint8_t *)check, (uint32_t)strlen(check)) == 0xCBF43926u);

    /* incremental must equal one-shot */
    uint32_t inc = crc32_update(0u, a, 4u);
    inc = crc32_update(inc, b, 4u);
    uint8_t all[8];
    memcpy(all, a, 4);
    memcpy(all + 4, b, 4);
    TEST_ASSERT(inc == crc32_calc(all, 8u));
}
