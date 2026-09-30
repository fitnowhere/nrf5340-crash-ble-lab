#include <diag_sdk/diag_sdk.h>

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/fs/fs.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log_ctrl.h>

static K_MUTEX_DEFINE(diag_lock);
static uint32_t boot_count;
static uint32_t event_count;
static bool ready;
static char fingerprint[21];

static void heartbeat_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(heartbeat, heartbeat_handler);

static void path(char *buffer, size_t length, const char *name)
{
	snprintk(buffer, length, "%s/%s", CONFIG_DIAG_SDK_MOUNT_POINT, name);
}

static int replace_text(const char *filename, const char *text)
{
	char temporary[104];
	if (snprintk(temporary, sizeof(temporary), "%s.tmp", filename) >= sizeof(temporary)) {
		return -ENAMETOOLONG;
	}
	struct fs_file_t file;
	fs_file_t_init(&file);
	int ret = fs_open(&file, temporary, FS_O_CREATE | FS_O_TRUNC | FS_O_WRITE);
	if (ret == 0) {
		ret = fs_write(&file, text, strlen(text)) == (ssize_t)strlen(text) ?
			fs_sync(&file) : -EIO;
	}
	(void)fs_close(&file);
	if (ret == 0) {
		ret = fs_rename(temporary, filename);
	}
	if (ret != 0) {
		(void)fs_unlink(temporary);
	}
	return ret;
}

static void heartbeat_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	size_t unused = 0;
	int ret = k_thread_stack_space_get(k_current_get(), &unused);
	diag_sdk_record(DIAG_SDK_LEVEL_INFO, "health", 20,
			ret == 0 ? (int)unused : ret);
	k_work_reschedule(&heartbeat, K_SECONDS(CONFIG_DIAG_SDK_METRICS_INTERVAL_SECONDS));
}

int diag_sdk_start(const char *build_fingerprint)
{
	if (ready) {
		return -EALREADY;
	}
	if (build_fingerprint == NULL || strlen(build_fingerprint) != 20) {
		return -EINVAL;
	}
	for (const char *p = build_fingerprint; *p != '\0'; ++p) {
		if (!((*p >= '0' && *p <= '9') || (*p >= 'a' && *p <= 'f'))) {
			return -EINVAL;
		}
	}
	memcpy(fingerprint, build_fingerprint, sizeof(fingerprint) - 1);
	char filename[96];
	path(filename, sizeof(filename), "boot_count.txt");
	struct fs_file_t file;
	char previous[24] = {0};
	fs_file_t_init(&file);
	if (fs_open(&file, filename, FS_O_READ) == 0) {
		ssize_t read = fs_read(&file, previous, sizeof(previous) - 1);
		if (read > 0) {
			boot_count = (uint32_t)strtoul(previous, NULL, 10);
		}
		(void)fs_close(&file);
	}
	boot_count++;
	char next[24];
	snprintk(next, sizeof(next), "%u\n", boot_count);
	int ret = replace_text(filename, next);
	if (ret != 0) {
		return ret;
	}
	ready = true;
	/* Optional: enabling the native FS logger only after the FS is mounted. */
#if defined(CONFIG_LOG_BACKEND_FS)
	const struct log_backend *backend = log_backend_get_by_name("log_backend_fs");
	if (backend != NULL) {
		log_backend_enable(backend, NULL, LOG_LEVEL_WRN);
	}
#endif
	k_work_schedule(&heartbeat, K_SECONDS(CONFIG_DIAG_SDK_METRICS_INTERVAL_SECONDS));
	return 0;
}

uint32_t diag_sdk_boot_count(void) { return boot_count; }
uint32_t diag_sdk_event_count(void) { return event_count; }

void diag_sdk_log_files(char *buffer, size_t length)
{
	if (length < 3) {
		return;
	}
	buffer[0] = '[';
	buffer[1] = '\0';
	size_t used = 1;
	struct fs_dir_t dir;
	fs_dir_t_init(&dir);
	if (fs_opendir(&dir, CONFIG_DIAG_SDK_MOUNT_POINT) == 0) {
		struct fs_dirent entry;
		const size_t prefix_len = strlen(CONFIG_DIAG_SDK_LOG_FILE_PREFIX);
		while (fs_readdir(&dir, &entry) == 0 && entry.name[0] != '\0') {
			if (entry.type != FS_DIR_ENTRY_FILE ||
			    strncmp(entry.name, CONFIG_DIAG_SDK_LOG_FILE_PREFIX, prefix_len)) {
				continue;
			}
			const char *suffix = entry.name + prefix_len;
			if (strlen(suffix) != 4 || suffix[0] < '0' || suffix[0] > '9' ||
			    suffix[1] < '0' || suffix[1] > '9' || suffix[2] < '0' ||
			    suffix[2] > '9' || suffix[3] < '0' || suffix[3] > '9') {
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

void diag_sdk_record(enum diag_sdk_level level, const char *category,
		     uint16_t code, int value)
{
	uintptr_t pc = (uintptr_t)__builtin_return_address(0);
	if (!ready || k_is_in_isr() || category == NULL ||
	    (unsigned int)level > DIAG_SDK_LEVEL_ERROR) {
		return;
	}
	/* Enforce plain ASCII tokens: no external string can corrupt event JSON. */
	size_t category_len = strlen(category);
	if (category_len == 0 || category_len > 24) {
		return;
	}
	for (const char *p = category; *p != '\0'; ++p) {
		if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
		      (*p >= '0' && *p <= '9') || *p == '_' || *p == '-')) {
			return;
		}
	}
	char line[240];
	static const char *const levels[] = {"info", "warning", "error"};
	int n = snprintk(line, sizeof(line),
		"{\"v\":1,\"fingerprint\":\"%s\",\"boot\":%u,"
		"\"uptime_ms\":%lld,\"level\":\"%s\",\"category\":\"%s\","
		"\"code\":%u,\"value\":%d,\"pc\":\"0x%08x\"}\n",
		fingerprint, boot_count, k_uptime_get(), levels[level], category, code, value,
		(unsigned int)pc);
	if (n <= 0 || n >= sizeof(line)) {
		return;
	}
	char filename[96];
	path(filename, sizeof(filename), "events.ndjson");
	k_mutex_lock(&diag_lock, K_FOREVER);
	struct fs_dirent entry;
	if (fs_stat(filename, &entry) == 0 &&
	    entry.size + n > CONFIG_DIAG_SDK_MAX_EVENTS_BYTES) {
		(void)fs_unlink(filename);
	}
	struct fs_file_t file;
	fs_file_t_init(&file);
	if (fs_open(&file, filename, FS_O_CREATE | FS_O_WRITE | FS_O_APPEND) == 0) {
		if (fs_write(&file, line, n) == n && fs_sync(&file) == 0) {
			event_count++;
		}
		(void)fs_close(&file);
	}
	k_mutex_unlock(&diag_lock);
}
