#include "build_id.h"
#include "crashes.h"
#include "dummy_ble.h"
#include "dump_store.h"

#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/reboot.h>

LOG_MODULE_REGISTER(main, LOG_LEVEL_DBG);

static const struct gpio_dt_spec crash_button = GPIO_DT_SPEC_GET(DT_ALIAS(sw0), gpios);
static const struct gpio_dt_spec reboot_button = GPIO_DT_SPEC_GET(DT_ALIAS(sw1), gpios);
static struct gpio_callback crash_button_cb;
static struct gpio_callback reboot_button_cb;
static struct k_work button_crash_work;
static struct k_work button_reboot_work;
static uint8_t next_button_crash;

static void button_crash_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	uint8_t count = IS_ENABLED(CONFIG_CRASH_LAB_STACK_SMASH) ? 5 : 4;
	uint8_t type = next_button_crash++ % count;
	LOG_WRN("Button 1 selected crash type %u (%s)", type, crash_type_name(type));
	(void)crash_schedule(type);
}

static void button_reboot_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	dump_store_note("clean reboot requested by Button 2");
	sys_reboot(SYS_REBOOT_COLD);
}

static void crash_button_pressed(const struct device *dev, struct gpio_callback *cb,
				 uint32_t pins)
{
	ARG_UNUSED(dev); ARG_UNUSED(cb); ARG_UNUSED(pins);
	k_work_submit(&button_crash_work);
}

static void reboot_button_pressed(const struct device *dev, struct gpio_callback *cb,
				  uint32_t pins)
{
	ARG_UNUSED(dev); ARG_UNUSED(cb); ARG_UNUSED(pins);
	k_work_submit(&button_reboot_work);
}

static int buttons_init(void)
{
	if (!gpio_is_ready_dt(&crash_button) || !gpio_is_ready_dt(&reboot_button)) {
		return -ENODEV;
	}
	int ret = gpio_pin_configure_dt(&crash_button, GPIO_INPUT);
	if (ret == 0) ret = gpio_pin_configure_dt(&reboot_button, GPIO_INPUT);
	if (ret == 0) ret = gpio_pin_interrupt_configure_dt(&crash_button, GPIO_INT_EDGE_TO_ACTIVE);
	if (ret == 0) ret = gpio_pin_interrupt_configure_dt(&reboot_button, GPIO_INT_EDGE_TO_ACTIVE);
	if (ret != 0) return ret;

	k_work_init(&button_crash_work, button_crash_handler);
	k_work_init(&button_reboot_work, button_reboot_handler);
	gpio_init_callback(&crash_button_cb, crash_button_pressed, BIT(crash_button.pin));
	gpio_init_callback(&reboot_button_cb, reboot_button_pressed, BIT(reboot_button.pin));
	gpio_add_callback(crash_button.port, &crash_button_cb);
	gpio_add_callback(reboot_button.port, &reboot_button_cb);
	return 0;
}

int main(void)
{
	printk("CRASHLAB build=%s+%s\n", CONFIG_CRASH_LAB_VERSION, CRASH_LAB_GIT_SHA);
	int ret = dump_store_init();
	if (ret != 0) {
		LOG_ERR("Dump storage initialization failed: %d", ret);
		return ret;
	}
	LOG_INF("Boot reset_reason=0x%x", dump_store_reset_reason());
	dump_store_note("boot build=%s+%s reset=0x%x", CONFIG_CRASH_LAB_VERSION,
		CRASH_LAB_GIT_SHA, dump_store_reset_reason());

	crash_auto_start();
	ret = buttons_init();
	if (ret != 0) {
		LOG_ERR("Buttons unavailable: %d", ret);
	}
	ret = dummy_ble_init();
	if (ret != 0) {
		return ret;
	}

	LOG_INF("Ready: BLE command 0=null 1=div0 2=assert 3=fnptr; Button 1 cycles; Button 2 reboots");
	return 0;
}
