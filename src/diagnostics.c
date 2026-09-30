#include "diagnostics.h"
#include "build_id.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/fs/fs.h>
#include <zephyr/kernel.h>

#define EVENTS_PATH "/lfs/events.ndjson"
#define BOOT_COUNT_PATH "/lfs/boot_count.txt"
#define MAX_EVENTS_BYTES 24576

static K_MUTEX_DEFINE(diag_lock);
static uint32_t boot_count;
static uint32_t event_count;
static bool ready;
static void heartbeat_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(heartbeat, heartbeat_handler);

static void heartbeat_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	size_t unused = 0;
	int ret = k_thread_stack_space_get(k_current_get(), &unused);
	diagnostics_record("health", DIAG_HEARTBEAT, ret == 0 ? (int)unused : ret);
	k_work_reschedule(&heartbeat, K_SECONDS(CONFIG_CRASH_LAB_METRICS_INTERVAL_SECONDS));
}

int diagnostics_init(void)
{
	struct fs_file_t file;
	char previous[24] = {0};
	fs_file_t_init(&file);
	if (fs_open(&file, BOOT_COUNT_PATH, FS_O_READ) == 0) {
		ssize_t read = fs_read(&file, previous, sizeof(previous) - 1);
		if (read > 0) {
			boot_count = (uint32_t)strtoul(previous, NULL, 10);
		}
		(void)fs_close(&file);
	}
	boot_count++;
	char next[24];
	snprintk(next, sizeof(next), "%u\n", boot_count);
	fs_file_t_init(&file);
	int ret = fs_open(&file, BOOT_COUNT_PATH, FS_O_CREATE | FS_O_TRUNC | FS_O_WRITE);
	if (ret != 0) {
		return ret;
	}
	if (fs_write(&file, next, strlen(next)) != (ssize_t)strlen(next)) {
		ret = -EIO;
	} else {
		ret = fs_sync(&file);
	}
	(void)fs_close(&file);
	ready = ret == 0;
	if (ready) {
		k_work_schedule(&heartbeat, K_SECONDS(CONFIG_CRASH_LAB_METRICS_INTERVAL_SECONDS));
	}
	return ret;
}

uint32_t diagnostics_boot_count(void) { return boot_count; }
uint32_t diagnostics_event_count(void) { return event_count; }

void diagnostics_log_files(char *buffer, size_t length)
{
	if (length < 3) {
		return;
	}
	buffer[0] = '[';
	buffer[1] = '\0';
	size_t used = 1;
	struct fs_dir_t dir;
	fs_dir_t_init(&dir);
	if (fs_opendir(&dir, "/lfs") == 0) {
		struct fs_dirent entry;
		while (fs_readdir(&dir, &entry) == 0 && entry.name[0] != '\0') {
			if (entry.type != FS_DIR_ENTRY_FILE || strncmp(entry.name, "zephyr.", 7)) {
				continue;
			}
			int added = snprintk(buffer + used, length - used, "%s\"%s\"",
				used > 1 ? "," : "", entry.name);
			if (added <= 0 || added >= length - used - 2) {
				buffer[used] = '\0';
				break;
			}
			used += added;
		}
		(void)fs_closedir(&dir);
	}
	buffer[used++] = ']';
	buffer[used] = '\0';
}

__attribute__((noinline, used))
void diagnostics_record(const char *category, enum diag_code code, int value)
{
	uintptr_t pc = (uintptr_t)__builtin_return_address(0);
	if (!ready || k_is_in_isr()) {
		return;
	}
	/* All categories are static ASCII tokens from this application. Never
	 * embed externally supplied text without JSON escaping. */
	char line[240];
	const char *level = (code == DIAG_BOOT || code == DIAG_BLE_CONNECTED ||
		code == DIAG_BLE_DISCONNECTED || code == DIAG_HEARTBEAT) ? "info" :
		(code == DIAG_CRASH_ARMED || code >= DIAG_INJECT_RECOVERABLE) ? "warning" : "error";
	int n = snprintk(line, sizeof(line),
		"{\"v\":1,\"fingerprint\":\"%s\",\"boot\":%u,"
		"\"uptime_ms\":%lld,\"level\":\"%s\",\"category\":\"%s\",\"code\":%u,"
		"\"value\":%d,\"pc\":\"0x%08x\"}\n",
		CRASH_LAB_FINGERPRINT, boot_count, k_uptime_get(), level, category,
		(unsigned int)code, value, (unsigned int)pc);
	if (n <= 0 || n >= sizeof(line)) {
		return;
	}
	k_mutex_lock(&diag_lock, K_FOREVER);
	struct fs_dirent entry;
	if (fs_stat(EVENTS_PATH, &entry) == 0 && entry.size + n > MAX_EVENTS_BYTES) {
		(void)fs_unlink(EVENTS_PATH);
	}
	struct fs_file_t file;
	fs_file_t_init(&file);
	if (fs_open(&file, EVENTS_PATH, FS_O_CREATE | FS_O_WRITE | FS_O_APPEND) == 0) {
		if (fs_write(&file, line, n) == n && fs_sync(&file) == 0) {
			event_count++;
		}
		(void)fs_close(&file);
	}
	k_mutex_unlock(&diag_lock);
}
