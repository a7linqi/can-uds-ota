#ifndef TEST_UTIL_H
#define TEST_UTIL_H

#include <stdint.h>
#include <stdio.h>

extern int g_checks;
extern int g_failures;

#define TEST_ASSERT(cond)                                                   \
    do {                                                                    \
        if (cond) {                                                         \
            g_checks++;                                                     \
        } else {                                                            \
            g_failures++;                                                   \
            printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);         \
        }                                                                   \
    } while (0)

static inline uint32_t t_get_be32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16)
         | ((uint32_t)p[2] << 8)  | (uint32_t)p[3];
}

static inline void t_put_be32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

#endif /* TEST_UTIL_H */
