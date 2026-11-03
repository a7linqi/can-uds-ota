#ifndef FLASH_RAM_H
#define FLASH_RAM_H
#include <stdint.h>
#include "ota/flash_layout.h"
#include "ota/ota_port.h"

#define FAULT_EVENT_MAX 256u
typedef struct { uint64_t start; uint32_t len; uint8_t device, erase; } fault_event_t;
typedef struct {
    int64_t remaining; /* -1 unlimited; cut before next mutated byte at 0 */
    int powered_off;
    uint64_t bytes;
    uint32_t event_count;
    fault_event_t events[FAULT_EVENT_MAX];
} storage_fault_t;
typedef struct {
    uint8_t *mem;
    uint32_t size, write_ops, erase_ops;
    int external, read_fail_after, ignore_writes;
    storage_fault_t *fault;
} flash_ram_t;
typedef struct {
    uint8_t mem[EEPROM_TOTAL_SIZE];
    uint32_t write_ops;
    int read_fail_after, ignore_writes;
    storage_fault_t *fault;
} eeprom_ram_t;
void storage_fault_reset(storage_fault_t *f);
void flash_ram_attach(flash_ram_t *f,uint8_t *mem,uint32_t size,int external,storage_fault_t *fault);
flash_port_t flash_ram_port(flash_ram_t *f);
eeprom_port_t eeprom_ram_port(eeprom_ram_t *f);
#endif
