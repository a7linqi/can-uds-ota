#ifndef OTA_FLASH_LAYOUT_H
#define OTA_FLASH_LAYOUT_H

/* F407ZG default. A ZE build must override FLASH_TOTAL_SIZE to 0x80000.
 * Internal flash contains ONE executable app, with its vector table at
 * APP_BASE_ADDR. Package headers exist ONLY in external NOR flash. */
#define FLASH_BASE_ADDR       0x08000000u
#ifndef FLASH_TOTAL_SIZE
#define FLASH_TOTAL_SIZE      0x00100000u
#endif
#define BOOT_OFFSET           0u
#define BOOT_SIZE             0x00010000u
#define APP_OFFSET            BOOT_SIZE
#define APP_BASE_ADDR         (FLASH_BASE_ADDR + APP_OFFSET)
#define APP_SIZE              (FLASH_TOTAL_SIZE - APP_OFFSET)

/* Two external image containers; 2 MiB is the required allocated region,
 * not a claim about the capacity/model fitted to the purchased board. */
#define EXT_IMAGE_SLOT_SIZE   0x00100000u
#define DOWNLOAD_OFFSET       0u
#define BACKUP_OFFSET         EXT_IMAGE_SLOT_SIZE
#define EXT_FLASH_TOTAL_SIZE  (2u * EXT_IMAGE_SLOT_SIZE)
#define EXT_ERASE_SIZE        4096u
#define IMAGE_HEADER_SIZE     32u
#define IMAGE_MAX_PAYLOAD     APP_SIZE
#define OTA_SIGNATURE_SIZE    64u

/* AT24C02: 2 Kbit = 256 bytes. Records occupy independent page ranges. */
#define EEPROM_TOTAL_SIZE     256u
#define EEPROM_PAGE_SIZE      8u
#define META_A_OFFSET         0u
#define META_B_OFFSET         128u
#define META_RECORD_SIZE      64u
#define META_COPY_COUNT       2u

#if FLASH_TOTAL_SIZE != 0x00100000u && FLASH_TOTAL_SIZE != 0x00080000u
#error Supported internal flash capacities are 512 KiB and 1 MiB
#endif
#if IMAGE_HEADER_SIZE + IMAGE_MAX_PAYLOAD + OTA_SIGNATURE_SIZE > EXT_IMAGE_SLOT_SIZE
#error External image container is too small
#endif
#endif
