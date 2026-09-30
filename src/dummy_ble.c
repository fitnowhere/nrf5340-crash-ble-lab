#include "dummy_ble.h"
#include "crashes.h"
#include "dump_store.h"
#include "diagnostics.h"
#include "build_id.h"

#include <errno.h>
#include <string.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/logging/log.h>
#include <zephyr/mgmt/mcumgr/mgmt/callbacks.h>
#include <zephyr/mgmt/mcumgr/transport/smp_bt.h>
#include <zephyr/sys/reboot.h>

LOG_MODULE_REGISTER(dummy_ble, LOG_LEVEL_DBG);

/* 7a4e0001 = service, 0002 = command, 0003 = status. */
#define CRASH_SERVICE_UUID BT_UUID_128_ENCODE(0x7a4e0001, 0x7b2d, 0x4c19, 0x9a71, 0x1db95c873510)
#define CRASH_COMMAND_UUID BT_UUID_128_ENCODE(0x7a4e0002, 0x7b2d, 0x4c19, 0x9a71, 0x1db95c873510)
#define CRASH_STATUS_UUID  BT_UUID_128_ENCODE(0x7a4e0003, 0x7b2d, 0x4c19, 0x9a71, 0x1db95c873510)

static struct bt_uuid_128 service_uuid = BT_UUID_INIT_128(CRASH_SERVICE_UUID);
static struct bt_uuid_128 command_uuid = BT_UUID_INIT_128(CRASH_COMMAND_UUID);
static struct bt_uuid_128 status_uuid = BT_UUID_INIT_128(CRASH_STATUS_UUID);

static uint8_t pending_command;
static struct k_work command_work;
static struct k_work advertise_work;
static char status_json[384];

static int json_escape(char *output, size_t output_size, const char *input)
{
	size_t used = 0;
	for (; *input != '\0'; ++input) {
		const char *replacement = NULL;
		if (*input == '"') {
			replacement = "\\\"";
		} else if (*input == '\\') {
			replacement = "\\\\";
		} else if ((unsigned char)*input < 0x20) {
			return -EINVAL;
		}
		size_t added = replacement != NULL ? 2 : 1;
		if (used + added >= output_size) {
			return -ENOSPC;
		}
		if (replacement != NULL) {
			memcpy(output + used, replacement, added);
		} else {
			output[used] = *input;
		}
		used += added;
	}
	output[used] = '\0';
	return 0;
}

static int format_status(char *buffer, size_t buffer_size)
{
	char log_files[80];
	char version[96];
	diagnostics_log_files(log_files, sizeof(log_files));
	if (json_escape(version, sizeof(version), CONFIG_CRASH_LAB_VERSION) != 0) {
		return -EINVAL;
	}
	int length = snprintk(buffer, buffer_size,
		"{\"build\":\"%s+%s\",\"fingerprint\":\"%s\",\"uptime_ms\":%lld,\"reset_reason\":%u,"
		"\"dump_pending\":%s,\"dump_size\":%u,\"crash_seq\":%u,"
		"\"boot_count\":%u,\"event_count\":%u,\"unsafe_commands\":%s,\"log_files\":%s}",
		version, CRASH_LAB_GIT_SHA, CRASH_LAB_FINGERPRINT, k_uptime_get(),
		dump_store_reset_reason(), dump_store_pending() ? "true" : "false",
		(unsigned int)dump_store_size(), dump_store_sequence(),
		diagnostics_boot_count(), diagnostics_event_count(),
		IS_ENABLED(CONFIG_CRASH_LAB_UNSAFE_BLE_COMMANDS) ? "true" : "false", log_files);
	return length < 0 || (size_t)length >= buffer_size ? -ENOSPC : length;
}

static ssize_t read_status(struct bt_conn *conn, const struct bt_gatt_attr *attr,
			   void *buf, uint16_t len, uint16_t offset)
{
	ARG_UNUSED(attr);
	/* Freeze one JSON value for all offset reads in this ATT long-read. */
	if (offset == 0 && format_status(status_json, sizeof(status_json)) < 0) {
		return BT_GATT_ERR(BT_ATT_ERR_UNLIKELY);
	}
	return bt_gatt_attr_read(conn, attr, buf, len, offset, status_json, strlen(status_json));
}

static void command_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	uint8_t command = pending_command;

	if (command <= (IS_ENABLED(CONFIG_CRASH_LAB_STACK_SMASH) ? 4 : 3)) {
		int ret = crash_schedule(command);
		if (ret != 0) {
			LOG_ERR("Crash schedule failed: %d", ret);
			diagnostics_record("command", DIAG_COMMAND_FAILED, ret);
		}
	} else if (command == 0x10) {
		dump_store_note("clean reboot requested over BLE");
		/* Let the ATT Write Response leave the radio before tearing down HCI. */
		k_sleep(K_MSEC(300));
		sys_reboot(SYS_REBOOT_COLD);
	} else if (command == 0xfe) {
		int ret = dump_store_clear();
		LOG_INF("Dump clear result: %d", ret);
		if (ret != 0) {
			diagnostics_record("storage", DIAG_COMMAND_FAILED, ret);
		}
		dummy_ble_status_changed();
	} else if (command >= 0x20 && command <= 0x23) {
		/* Sample recoverable paths, not claims of real hardware failures. */
		static const enum diag_code cases[] = {
			DIAG_INJECT_RECOVERABLE, DIAG_INJECT_BLE_FAILURE,
			DIAG_INJECT_STORAGE_FAILURE, DIAG_INJECT_OTA_REJECTED,
		};
		diagnostics_record("simulated", cases[command - 0x20], -EIO);
		LOG_WRN("Synthetic diagnostic %u (not a hardware failure)",
			(unsigned int)cases[command - 0x20]);
	}
}

static ssize_t write_command(struct bt_conn *conn, const struct bt_gatt_attr *attr,
			     const void *buf, uint16_t len, uint16_t offset, uint8_t flags)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(flags);
	if (!IS_ENABLED(CONFIG_CRASH_LAB_UNSAFE_BLE_COMMANDS)) {
		return BT_GATT_ERR(BT_ATT_ERR_AUTHORIZATION);
	}
	if (offset != 0 || len != 1) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}
	uint8_t command = *(const uint8_t *)buf;
	if (command > (IS_ENABLED(CONFIG_CRASH_LAB_STACK_SMASH) ? 4 : 3) &&
	    command != 0x10 && command != 0xfe &&
	    (command < 0x20 || command > 0x23)) {
		diagnostics_record("command", DIAG_COMMAND_FAILED, -EINVAL);
		return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
	}
	if (k_work_busy_get(&command_work) != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_UNLIKELY);
	}
	pending_command = command;
	k_work_submit(&command_work);
	return len;
}

BT_GATT_SERVICE_DEFINE(crash_service,
	BT_GATT_PRIMARY_SERVICE(&service_uuid),
	BT_GATT_CHARACTERISTIC(&command_uuid.uuid, BT_GATT_CHRC_WRITE,
		BT_GATT_PERM_WRITE, NULL, write_command, NULL),
	BT_GATT_CHARACTERISTIC(&status_uuid.uuid, BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
		BT_GATT_PERM_READ, read_status, NULL, status_json),
	BT_GATT_CCC(NULL, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE)
);

void dummy_ble_status_changed(void)
{
	char notification[sizeof(status_json)];
	int length = format_status(notification, sizeof(notification));
	if (length >= 0) {
		(void)bt_gatt_notify(NULL, &crash_service.attrs[4], notification, length);
	}
}

static bool diagnostic_path(const char *filename)
{
	if (strcmp(filename, "/lfs/crash.bin") == 0 ||
	    strcmp(filename, "/lfs/meta.txt") == 0 ||
	    strcmp(filename, "/lfs/recent.log") == 0 ||
	    strcmp(filename, "/lfs/events.ndjson") == 0) {
		return true;
	}
	return strncmp(filename, "/lfs/zephyr.", 12) == 0 && strlen(filename) == 16 &&
		filename[12] >= '0' && filename[12] <= '9' &&
		filename[13] >= '0' && filename[13] <= '9' &&
		filename[14] >= '0' && filename[14] <= '9' &&
		filename[15] >= '0' && filename[15] <= '9';
}

static enum mgmt_cb_return fs_access(uint32_t event, enum mgmt_cb_return previous,
				     int32_t *rc, uint16_t *group, bool *abort_more,
				     void *data, size_t data_size)
{
	ARG_UNUSED(group);
	if (event == MGMT_EVT_OP_FS_MGMT_FILE_ACCESS && previous == MGMT_CB_OK &&
	    data_size == sizeof(struct fs_mgmt_file_access)) {
		struct fs_mgmt_file_access *access = data;
		if ((access->access == FS_MGMT_FILE_ACCESS_READ ||
		     access->access == FS_MGMT_FILE_ACCESS_STATUS) &&
		    diagnostic_path(access->filename)) {
			return MGMT_CB_OK;
		}
		*rc = MGMT_ERR_EACCESSDENIED;
		*abort_more = true;
		return MGMT_CB_ERROR_RC;
	}
	return MGMT_CB_OK;
}

static struct mgmt_callback fs_access_callback = {
	.callback = fs_access,
	.event_id = MGMT_EVT_OP_FS_MGMT_FILE_ACCESS,
};

static const struct bt_data advertising[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR),
	BT_DATA_BYTES(BT_DATA_UUID128_ALL, SMP_BT_SVC_UUID_VAL),
};

static const struct bt_data scan_response[] = {
	BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME,
		sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

static void start_advertising(void)
{
	int ret = bt_le_adv_start(BT_LE_ADV_CONN_FAST_2, advertising,
		ARRAY_SIZE(advertising), scan_response, ARRAY_SIZE(scan_response));
	if (ret != 0 && ret != -EALREADY) {
		LOG_ERR("Advertising failed: %d", ret);
		diagnostics_record("ble", DIAG_BLE_START_FAILED, ret);
	} else {
		LOG_INF("advertising crash-lab");
	}
}

static void advertise_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	start_advertising();
}

static void connected(struct bt_conn *conn, uint8_t err)
{
	ARG_UNUSED(conn);
	LOG_INF("BLE connected, err=%u", err);
	diagnostics_record("ble", DIAG_BLE_CONNECTED, err);
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	ARG_UNUSED(conn);
	LOG_INF("BLE disconnected, reason=%u", reason);
	diagnostics_record("ble", DIAG_BLE_DISCONNECTED, reason);
}

static void recycled(void)
{
	k_work_submit(&advertise_work);
}

BT_CONN_CB_DEFINE(connection_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
	.recycled = recycled,
};

int dummy_ble_init(void)
{
	k_work_init(&command_work, command_work_handler);
	k_work_init(&advertise_work, advertise_work_handler);
	mgmt_callback_register(&fs_access_callback);
	(void)format_status(status_json, sizeof(status_json));
	int ret = bt_enable(NULL);
	if (ret != 0) {
		LOG_ERR("Bluetooth enable failed: %d", ret);
		diagnostics_record("ble", DIAG_BLE_ENABLE_FAILED, ret);
		return ret;
	}
	start_advertising();
	return 0;
}
