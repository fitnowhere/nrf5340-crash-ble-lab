#ifndef CRASH_LAB_DIAGNOSTICS_H
#define CRASH_LAB_DIAGNOSTICS_H

#include <stdint.h>
#include <stddef.h>

/* Code IDs are stable across rebuilds; injection IDs are deliberately separate. */
enum diag_code {
	DIAG_BOOT = 1,
	DIAG_CRASH_ARMED = 2,
	DIAG_BLE_CONNECTED = 10,
	DIAG_BLE_DISCONNECTED = 11,
	DIAG_BLE_START_FAILED = 12,
	DIAG_BLE_ENABLE_FAILED = 13,
	DIAG_COMMAND_FAILED = 14,
	DIAG_BUTTON_FAILED = 15,
	DIAG_HEARTBEAT = 20,
	DIAG_INJECT_RECOVERABLE = 100,
	DIAG_INJECT_BLE_FAILURE = 101,
	DIAG_INJECT_STORAGE_FAILURE = 102,
	DIAG_INJECT_OTA_REJECTED = 103,
};

int diagnostics_init(void);
uint32_t diagnostics_boot_count(void);
uint32_t diagnostics_event_count(void);
void diagnostics_log_files(char *buffer, size_t length);

/* NOINLINE: return address belongs to the caller, not this function. In
 * addr2line/GDB subtract one byte from the return PC to get the call site.
 * Only use from thread context after the filesystem has been mounted. */
void diagnostics_record(const char *category, enum diag_code code, int value);

#endif
