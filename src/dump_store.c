#include "dump_store.h"
#include "crashes.h"
#include "build_id.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/debug/coredump.h>
#include <zephyr/fs/fs.h>
#include <zephyr/fs/littlefs.h>
#include <zephyr/drivers/hwinfo.h>
#include <zephyr/logging/log.h>
#include <zephyr/storage/flash_map.h>

LOG_MODULE_REGISTER(dump_store, LOG_LEVEL_DBG);

#define STORAGE_PARTITION storage_partition
#define STORAGE_ID PARTITION_ID(STORAGE_PARTITION)
#define CRASH_PATH "/lfs/crash.bin"
#define META_PATH "/lfs/meta.txt"
#define LOG_PATH "/lfs/recent.log"
#define ARMED_PATH "/lfs/armed.txt"
#define SEQ_PATH "/lfs/sequence.txt"

FS_LITTLEFS_DECLARE_DEFAULT_CONFIG(storage);

static struct fs_mount_t littlefs_mount = {
	.type = FS_LITTLEFS,
	.fs_data = &storage,
	.storage_dev = (void *)STORAGE_ID,
	.mnt_point = "/lfs",
};

static K_MUTEX_DEFINE(store_lock);
static bool pending;
static size_t saved_size;
static uint32_t reset_reason;
static uint32_t sequence;

static int write_text(const char *path, const char *text)
{
	struct fs_file_t file;
	fs_file_t_init(&file);
	int ret = fs_open(&file, path, FS_O_CREATE | FS_O_WRITE | FS_O_TRUNC);
	if (ret == 0) {
		ssize_t wrote = fs_write(&file, text, strlen(text));
		ret = wrote == (ssize_t)strlen(text) ? fs_sync(&file) : -EIO;
	}
	(void)fs_close(&file);
	return ret;
}

static int read_u32(const char *path, uint32_t *value)
{
	char buf[24] = {0};
	struct fs_file_t file;
	fs_file_t_init(&file);
	int ret = fs_open(&file, path, FS_O_READ);
	if (ret != 0) {
		return ret;
	}
	ssize_t got = fs_read(&file, buf, sizeof(buf) - 1);
	(void)fs_close(&file);
	if (got <= 0) {
		return -EIO;
	}
	*value = (uint32_t)strtoul(buf, NULL, 10);
	return 0;
}

static int copy_coredump_to_file(void)
{
	int size = coredump_query(COREDUMP_QUERY_GET_STORED_DUMP_SIZE, NULL);
	if (size <= 0) {
		return size;
	}
	if (size > 65536) {
		return -EFBIG;
	}
	if (coredump_cmd(COREDUMP_CMD_VERIFY_STORED_DUMP, NULL) != 1) {
		return -EBADMSG;
	}

	struct fs_file_t file;
	fs_file_t_init(&file);
	int ret = fs_open(&file, CRASH_PATH, FS_O_CREATE | FS_O_WRITE | FS_O_TRUNC);
	if (ret != 0) {
		return ret;
	}

	uint8_t buffer[512];
	for (off_t offset = 0; offset < size;) {
		size_t length = MIN(sizeof(buffer), (size_t)(size - offset));
		struct coredump_cmd_copy_arg copy = {
			.offset = offset,
			.buffer = buffer,
			.length = length,
		};
		ret = coredump_cmd(COREDUMP_CMD_COPY_STORED_DUMP, &copy);
		if (ret != (int)length || fs_write(&file, buffer, length) != (ssize_t)length) {
			ret = -EIO;
			break;
		}
		offset += length;
	}
	if (ret >= 0) {
		ret = fs_sync(&file);
	}
	(void)fs_close(&file);
	if (ret != 0) {
		return ret;
	}

	saved_size = (size_t)size;
	pending = true;
	sequence++;
	char seq[24];
	snprintk(seq, sizeof(seq), "%u\n", sequence);
	(void)write_text(SEQ_PATH, seq);

	char armed[32] = "unknown";
	struct fs_file_t af;
	fs_file_t_init(&af);
	if (fs_open(&af, ARMED_PATH, FS_O_READ) == 0) {
		ssize_t got = fs_read(&af, armed, sizeof(armed) - 1);
		if (got > 0) {
			armed[got] = '\0';
		}
		(void)fs_close(&af);
	}

	char meta[320];
	snprintk(meta, sizeof(meta),
		"{\"build\":\"%s+%s\",\"fingerprint\":\"%s\",\"version\":\"%s\","
		"\"crash_seq\":%u,\"reset_reason\":%u,\"expected\":\"%s\","
		"\"dump_size\":%u}\n",
		CONFIG_CRASH_LAB_VERSION, CRASH_LAB_GIT_SHA, CRASH_LAB_FINGERPRINT,
		CONFIG_CRASH_LAB_VERSION,
		sequence, reset_reason, armed, (unsigned int)saved_size);
	ret = write_text(META_PATH, meta);
	if (ret == 0) {
		ret = coredump_cmd(COREDUMP_CMD_INVALIDATE_STORED_DUMP, NULL);
	}
	return ret;
}

int dump_store_init(void)
{
	(void)hwinfo_get_reset_cause(&reset_reason);
	(void)hwinfo_clear_reset_cause();

	int ret = fs_mount(&littlefs_mount);
	if (ret != 0) {
		/* Format only a blank device. Corrupt/older littlefs must be preserved
		 * for investigation rather than silently destroying crash evidence. */
		const struct flash_area *area;
		uint32_t first_word = 0;
		int flash_ret = flash_area_open(STORAGE_ID, &area);
		if (flash_ret == 0) {
			flash_ret = flash_area_read(area, 0, &first_word, sizeof(first_word));
			flash_area_close(area);
		}
		if (flash_ret != 0 || first_word != UINT32_MAX) {
			LOG_ERR("littlefs mount failed (%d); nonblank storage preserved", ret);
			return ret;
		}
		LOG_WRN("Formatting erased lab littlefs partition");
		ret = fs_mkfs(FS_LITTLEFS, (uintptr_t)littlefs_mount.storage_dev,
			littlefs_mount.fs_data, 0);
		if (ret == 0) {
			ret = fs_mount(&littlefs_mount);
		}
	}
	if (ret != 0) {
		LOG_ERR("littlefs unavailable: %d", ret);
		return ret;
	}
	(void)read_u32(SEQ_PATH, &sequence);

	struct fs_dirent entry;
	if (fs_stat(CRASH_PATH, &entry) == 0 && entry.size > 0) {
		pending = true;
		saved_size = entry.size;
	}

	int has_dump = coredump_query(COREDUMP_QUERY_HAS_STORED_DUMP, NULL);
	printk("CRASHLAB flash_dump_valid=%d raw_size=%d backend_error=%d\n", has_dump,
		coredump_query(COREDUMP_QUERY_GET_STORED_DUMP_SIZE, NULL),
		coredump_query(COREDUMP_QUERY_GET_ERROR, NULL));
	if (has_dump == 1) {
		ret = copy_coredump_to_file();
		if (ret == 0) {
			LOG_INF("dump pending / copied to %s (%u bytes)", CRASH_PATH,
				(unsigned int)saved_size);
		} else {
			LOG_ERR("coredump copy failed: %d", ret);
			return ret;
		}
	}
	return 0;
}

void dump_store_note(const char *fmt, ...)
{
	char line[160];
	va_list ap;
	va_start(ap, fmt);
	vsnprintk(line, sizeof(line), fmt, ap);
	va_end(ap);

	k_mutex_lock(&store_lock, K_FOREVER);
	struct fs_dirent log_entry;
	if (fs_stat(LOG_PATH, &log_entry) == 0 && log_entry.size > 16384) {
		(void)fs_unlink(LOG_PATH);
	}
	struct fs_file_t file;
	fs_file_t_init(&file);
	if (fs_open(&file, LOG_PATH, FS_O_CREATE | FS_O_WRITE | FS_O_APPEND) == 0) {
		(void)fs_write(&file, line, strlen(line));
		(void)fs_write(&file, "\n", 1);
		(void)fs_sync(&file);
		(void)fs_close(&file);
	}
	k_mutex_unlock(&store_lock);
}

int dump_store_arm(uint8_t type)
{
	const char *name = crash_type_name(type);
	int ret = write_text(ARMED_PATH, name);
	if (ret == 0) {
		dump_store_note("armed crash=%s uptime_ms=%lld", name, k_uptime_get());
	}
	return ret;
}

bool dump_store_pending(void) { return pending; }
size_t dump_store_size(void) { return saved_size; }
uint32_t dump_store_reset_reason(void) { return reset_reason; }
uint32_t dump_store_sequence(void) { return sequence; }

int dump_store_clear(void)
{
	(void)fs_unlink(CRASH_PATH);
	(void)fs_unlink(META_PATH);
	(void)fs_unlink(LOG_PATH);
	(void)fs_unlink(ARMED_PATH);
	pending = false;
	saved_size = 0;
	return coredump_cmd(COREDUMP_CMD_ERASE_STORED_DUMP, NULL);
}
