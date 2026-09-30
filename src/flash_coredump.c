/* Standalone fault-safe nRF5340 internal-flash Zephyr coredump backend.
 *
 * soc_flash_nrf.c serializes writes using k_sem_take(K_FOREVER), which asserts
 * in exception context when CONFIG_ASSERT=y. The generic flash-partition
 * backend therefore cannot capture an actual Cortex-M fault on this build.
 * The fault path here calls synchronous nrfx NVMC directly, without kernel
 * locks, allocation, logging or the filesystem. The littlefs side never uses
 * this partition. This lab must not trigger faults during an active flash
 * operation or run another app-core writer while the dump is being saved.
 */
#include <errno.h>
#include <string.h>
#include <nrfx_nvmc.h>
#include <zephyr/debug/coredump.h>
#include <zephyr/storage/flash_map.h>

#define DUMP_NODE coredump_partition
#define DUMP_START PARTITION_OFFSET(DUMP_NODE)
#define DUMP_BYTES PARTITION_SIZE(DUMP_NODE)
#define HEADER_BYTES 16U
#define MAGIC 0x42444c43U /* "CLDB" in little endian */

BUILD_ASSERT(DUMP_BYTES == 65536U, "The lab uses a 64 KiB crash partition");
BUILD_ASSERT((DUMP_START % 4096) == 0, "NVMC erase requires page alignment");

static uint32_t length;
static uint32_t checksum;
static int last_error;
static uint8_t partial[4];
static uint8_t partial_len;

static inline uint32_t flash_word(uint32_t byte_offset)
{
	return *(volatile const uint32_t *)(uintptr_t)(DUMP_START + byte_offset);
}

static void program_word(uint32_t byte_offset, uint32_t value)
{
	nrfx_nvmc_word_write(DUMP_START + byte_offset, value);
	while (!nrfx_nvmc_write_done_check()) {
	}
}

static int erase_all(void)
{
	uint32_t page_size = nrfx_nvmc_flash_page_size_get();
	if (page_size == 0 || DUMP_START % page_size || DUMP_BYTES % page_size) {
		return -EINVAL;
	}
	for (uint32_t i = 0; i < DUMP_BYTES; i += page_size) {
		int ret = nrfx_nvmc_page_erase(DUMP_START + i);
		if (ret != 0) {
			return ret;
		}
	}
	return 0;
}

static void start(void)
{
	length = 0;
	checksum = 0;
	partial_len = 0;
	last_error = erase_all();
}

static void write_buffer(uint8_t *buffer, size_t size)
{
	if (last_error != 0) {
		return;
	}
	for (size_t i = 0; i < size; ++i) {
		if (length >= DUMP_BYTES - HEADER_BYTES) {
			last_error = -ENOSPC;
			return;
		}
		uint8_t byte = buffer[i];
		partial[partial_len++] = byte;
		checksum += byte;
		length++;
		if (partial_len == 4) {
			uint32_t word;
			memcpy(&word, partial, sizeof(word));
			program_word(HEADER_BYTES + length - 4, word);
			partial_len = 0;
		}
	}
}

static void end(void)
{
	if (last_error != 0) {
		return; /* no magic: never publish a partial dump */
	}
	if (partial_len != 0) {
		uint32_t word = UINT32_MAX;
		memcpy(&word, partial, partial_len);
		program_word(HEADER_BYTES + length - partial_len, word);
	}
	program_word(4, length);
	program_word(8, checksum);
	program_word(12, 0);
	program_word(0, MAGIC); /* commit marker, written only after all data */
}

static int stored_size(void)
{
	uint32_t size = flash_word(4);
	if (flash_word(0) != MAGIC || flash_word(12) != 0 ||
	    size < 17 || size > DUMP_BYTES - HEADER_BYTES) {
		return 0;
	}
	return (int)size;
}

static int verify(void)
{
	int size = stored_size();
	if (size <= 0) {
		return size;
	}
	uint32_t sum = 0;
	const uint8_t *payload = (const uint8_t *)(uintptr_t)(DUMP_START + HEADER_BYTES);
	for (int i = 0; i < size; ++i) {
		sum += payload[i];
	}
	return sum == flash_word(8) ? 1 : 0;
}

static int query(enum coredump_query_id id, void *arg)
{
	ARG_UNUSED(arg);
	switch (id) {
	case COREDUMP_QUERY_GET_ERROR: return last_error;
	case COREDUMP_QUERY_HAS_STORED_DUMP: return verify();
	case COREDUMP_QUERY_GET_STORED_DUMP_SIZE: return stored_size();
	default: return -ENOTSUP;
	}
}

static int command(enum coredump_cmd_id id, void *arg)
{
	switch (id) {
	case COREDUMP_CMD_CLEAR_ERROR:
		last_error = 0;
		return 0;
	case COREDUMP_CMD_VERIFY_STORED_DUMP:
		return verify();
	case COREDUMP_CMD_COPY_STORED_DUMP: {
		struct coredump_cmd_copy_arg *copy = arg;
		int size = stored_size();
		if (!copy || !copy->buffer || copy->offset < 0 ||
		    copy->offset > size || copy->length > size - copy->offset) {
			return -EINVAL;
		}
		memcpy(copy->buffer,
		       (const void *)(uintptr_t)(DUMP_START + HEADER_BYTES + copy->offset),
		       copy->length);
		return (int)copy->length;
	}
	case COREDUMP_CMD_INVALIDATE_STORED_DUMP:
		if (stored_size() > 0) {
			program_word(0, 0); /* only clear bits; no blocking kernel lock */
		}
		return 0;
	case COREDUMP_CMD_ERASE_STORED_DUMP:
		return erase_all();
	default: return -ENOTSUP;
	}
}

struct coredump_backend_api coredump_backend_other = {
	.start = start,
	.end = end,
	.buffer_output = write_buffer,
	.query = query,
	.cmd = command,
};
