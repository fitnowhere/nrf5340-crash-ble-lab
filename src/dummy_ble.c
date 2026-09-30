#include "dummy_ble.h"
#include "crashes.h"
#include "dump_store.h"
#include "build_id.h"

#include <errno.h>
#include <string.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/logging/log.h>
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
static char status_json[192];

static void update_status(void)
{
	snprintk(status_json, sizeof(status_json),
		"{\"build\":\"%s+%s\",\"fingerprint\":\"%s\",\"uptime_ms\":%lld,\"reset_reason\":%u,"
		"\"dump_pending\":%s,\"dump_size\":%u,\"crash_seq\":%u}",
		CONFIG_CRASH_LAB_VERSION, CRASH_LAB_GIT_SHA, CRASH_LAB_FINGERPRINT, k_uptime_get(),
		dump_store_reset_reason(), dump_store_pending() ? "true" : "false",
		(unsigned int)dump_store_size(), dump_store_sequence());
}

static ssize_t read_status(struct bt_conn *conn, const struct bt_gatt_attr *attr,
			   void *buf, uint16_t len, uint16_t offset)
{
	ARG_UNUSED(attr);
	update_status();
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
		}
	} else if (command == 0x10) {
		dump_store_note("clean reboot requested over BLE");
		/* Let the ATT Write Response leave the radio before tearing down HCI. */
		k_sleep(K_MSEC(300));
		sys_reboot(SYS_REBOOT_COLD);
	} else if (command == 0xfe) {
		int ret = dump_store_clear();
		LOG_INF("Dump clear result: %d", ret);
		dummy_ble_status_changed();
	}
}

static ssize_t write_command(struct bt_conn *conn, const struct bt_gatt_attr *attr,
			     const void *buf, uint16_t len, uint16_t offset, uint8_t flags)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(flags);
	if (offset != 0 || len != 1) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}
	uint8_t command = *(const uint8_t *)buf;
	if (command > (IS_ENABLED(CONFIG_CRASH_LAB_STACK_SMASH) ? 4 : 3) &&
	    command != 0x10 && command != 0xfe) {
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
	update_status();
	(void)bt_gatt_notify(NULL, &crash_service.attrs[4], status_json, strlen(status_json));
}

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
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	ARG_UNUSED(conn);
	LOG_INF("BLE disconnected, reason=%u", reason);
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
	int ret = bt_enable(NULL);
	if (ret != 0) {
		LOG_ERR("Bluetooth enable failed: %d", ret);
		return ret;
	}
	start_advertising();
	return 0;
}
