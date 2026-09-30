#ifndef DIAG_SDK_H
#define DIAG_SDK_H

#include <stddef.h>
#include <stdint.h>

#define DIAG_SDK_VERSION_MAJOR 0
#define DIAG_SDK_VERSION_MINOR 1
#define DIAG_SDK_VERSION_PATCH 0

enum diag_sdk_level {
	DIAG_SDK_LEVEL_INFO = 0,
	DIAG_SDK_LEVEL_WARNING = 1,
	DIAG_SDK_LEVEL_ERROR = 2,
};

/* Portable event schema v1. Build ID must be a 20-character lowercase hex
 * fingerprint embedded in the unstripped ELF. The file system is pre-mounted.
 * Call from a thread; never write a filesystem from an ISR or fatal handler.
 */
int diag_sdk_start(const char *build_fingerprint);
void diag_sdk_record(enum diag_sdk_level level, const char *category,
		     uint16_t code, int value)
	__attribute__((noinline));
void diag_sdk_log_files(char *buffer, size_t length);
uint32_t diag_sdk_boot_count(void);
uint32_t diag_sdk_event_count(void);

#endif
