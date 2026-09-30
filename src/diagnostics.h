#ifndef CRASH_LAB_DIAGNOSTICS_H
#define CRASH_LAB_DIAGNOSTICS_H

#include <stdint.h>
#include <stddef.h>
#include "build_id.h"
#include <diag_sdk/diag_sdk.h>

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

#define diagnostics_init() diag_sdk_start(CRASH_LAB_FINGERPRINT)
#define diagnostics_boot_count diag_sdk_boot_count
#define diagnostics_event_count diag_sdk_event_count
#define diagnostics_log_files diag_sdk_log_files

/* NOINLINE: return address belongs to the caller, not this function. In
 * addr2line/GDB subtract one byte from the return PC to get the call site.
 * Only use from thread context after the filesystem has been mounted. */
#define DIAGNOSTICS_LEVEL(_code) \
	(((_code) == DIAG_BOOT || (_code) == DIAG_BLE_CONNECTED || \
	  (_code) == DIAG_BLE_DISCONNECTED || (_code) == DIAG_HEARTBEAT) ? \
	 DIAG_SDK_LEVEL_INFO : \
	 (((_code) == DIAG_CRASH_ARMED || (_code) >= DIAG_INJECT_RECOVERABLE) ? \
	  DIAG_SDK_LEVEL_WARNING : DIAG_SDK_LEVEL_ERROR))
#define diagnostics_record(_category, _code, _value) \
	diag_sdk_record(DIAGNOSTICS_LEVEL(_code), (_category), (_code), (_value))

#endif
