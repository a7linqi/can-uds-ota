#include "ota/watchdog_policy.h"
#include "test_util.h"
void run_watchdog_tests(void)
{
    watchdog_policy_t p;
    uint32_t start=0xFFFFFF00u;
    puts("[watchdog policy]");
    watchdog_policy_init(&p,0,start,0u);
    TEST_ASSERT(watchdog_policy_should_feed(&p,start,0)==1);
    TEST_ASSERT(!watchdog_policy_should_feed(&p,start+249u,0));
    TEST_ASSERT(watchdog_policy_should_feed(&p,start+250u,0)==1);
    TEST_ASSERT(watchdog_policy_should_feed(&p,start+500u,0)==1);
    watchdog_policy_fail(&p);
    TEST_ASSERT(!watchdog_policy_should_feed(&p,start+1000u,1));
    watchdog_policy_init(&p,1,start,1000u);
    TEST_ASSERT(!watchdog_policy_should_feed(&p,start,1));
    TEST_ASSERT(watchdog_policy_may_confirm(&p,start+999u));
    TEST_ASSERT(!watchdog_policy_confirmed(&p));
    TEST_ASSERT(watchdog_policy_should_feed(&p,start+1500u,1));
    TEST_ASSERT(!watchdog_policy_should_feed(&p,start+1750u,0));
    TEST_ASSERT(watchdog_policy_should_feed(&p,start+2000u,1));
    watchdog_policy_init(&p,1,start,1000u);
    TEST_ASSERT(!watchdog_policy_may_confirm(&p,start+1000u));
    TEST_ASSERT(p.phase==WATCHDOG_FAILED);
    TEST_ASSERT(watchdog_policy_confirmed(&p)!=0);
    TEST_ASSERT(!watchdog_policy_should_feed(&p,start+2000u,1));
    watchdog_policy_init(&p,1,0u,1000u);
    TEST_ASSERT(!watchdog_policy_should_feed(&p,1000u,1) && p.phase==WATCHDOG_FAILED);
    watchdog_policy_init(&p,1,0u,0u);
    TEST_ASSERT(!watchdog_policy_should_feed(&p,0u,1));
    watchdog_policy_init(&p,1,0u,60001u);
    TEST_ASSERT(!watchdog_policy_may_confirm(&p,0u));
}
