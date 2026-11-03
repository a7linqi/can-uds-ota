#ifndef OTA_WATCHDOG_POLICY_H
#define OTA_WATCHDOG_POLICY_H
#include <stdint.h>
#define WATCHDOG_FEED_INTERVAL_MS 250u
typedef enum {
    WATCHDOG_BOOT=0,WATCHDOG_APP_WAIT,WATCHDOG_APP_RUNNING,WATCHDOG_FAILED
} watchdog_phase_t;
typedef struct {
    watchdog_phase_t phase;
    uint32_t started_ms,last_feed_ms,health_deadline_ms;
    uint8_t has_fed;
} watchdog_policy_t;
void watchdog_policy_init(watchdog_policy_t *p,int application,uint32_t now,uint32_t health_deadline_ms);
int watchdog_policy_may_confirm(watchdog_policy_t *p,uint32_t now);
/* Call only after an eligible health check and durable confirmation succeed.
 * The deadline gates START of confirmation; its storage calls are bounded
 * separately. No feed is allowed while confirmation is in progress. */
int watchdog_policy_confirmed(watchdog_policy_t *p);
int watchdog_policy_should_feed(watchdog_policy_t *p,uint32_t now,int healthy);
void watchdog_policy_fail(watchdog_policy_t *p);
#endif
