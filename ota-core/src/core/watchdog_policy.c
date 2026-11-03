#include "ota/watchdog_policy.h"
#include <string.h>
void watchdog_policy_init(watchdog_policy_t *p,int application,uint32_t now,uint32_t deadline)
{
    memset(p,0,sizeof *p); p->started_ms=now; p->health_deadline_ms=deadline;
    p->phase=application?WATCHDOG_APP_WAIT:WATCHDOG_BOOT;
    if(application && (!deadline || deadline>60000u)) p->phase=WATCHDOG_FAILED;
}
int watchdog_policy_may_confirm(watchdog_policy_t *p,uint32_t now)
{
    if(p->phase!=WATCHDOG_APP_WAIT) return 0;
    if((uint32_t)(now-p->started_ms)>=p->health_deadline_ms) {
        p->phase=WATCHDOG_FAILED; return 0;
    }
    return 1;
}
int watchdog_policy_confirmed(watchdog_policy_t *p)
{
    if(p->phase!=WATCHDOG_APP_WAIT) return -1;
    p->phase=WATCHDOG_APP_RUNNING; return 0;
}
int watchdog_policy_should_feed(watchdog_policy_t *p,uint32_t now,int healthy)
{
    if(p->phase==WATCHDOG_APP_WAIT) { (void)watchdog_policy_may_confirm(p,now); return 0; }
    if((p->phase!=WATCHDOG_BOOT && p->phase!=WATCHDOG_APP_RUNNING) ||
       (p->phase==WATCHDOG_APP_RUNNING && !healthy)) return 0;
    if(p->has_fed && (uint32_t)(now-p->last_feed_ms)<WATCHDOG_FEED_INTERVAL_MS) return 0;
    p->has_fed=1u; p->last_feed_ms=now; return 1;
}
void watchdog_policy_fail(watchdog_policy_t *p) { p->phase=WATCHDOG_FAILED; }
