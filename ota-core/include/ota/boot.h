#ifndef OTA_BOOT_H
#define OTA_BOOT_H
#include "ota/metadata.h"
typedef struct {
    flash_port_t internal;
    flash_port_t external;
    metadata_store_t *store;
} boot_context_t;
typedef enum { BOOT_TARGET_LOADER = 0, BOOT_TARGET_APP = 1 } boot_target_t;
void boot_context_init(boot_context_t *b, flash_port_t internal,
                       flash_port_t external, metadata_store_t *store);
/* Once per reset. Never jumps itself. Any failed persistence/copy step
 * stays in the loader. INSTALLING/RESTORING always restart a full copy. */
boot_target_t boot_select(boot_context_t *b, metadata_t *meta_out);
/* Application health hook only, after a real trial boot. There is no UDS
 * command to impersonate this hook. Validates installed bytes again. */
int boot_confirm(boot_context_t *b);
/* Trusted running-App health hook: confirm TRIAL, or validate the existing
 * active app in IDLE/DOWNLOADING without modifying download metadata. */
int boot_accept_app_health(boot_context_t *b);
#endif
