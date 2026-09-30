#include "crashes.h"
#include "dump_store.h"
#include "diagnostics.h"

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/logging/log_ctrl.h>
#include <zephyr/random/random.h>
#include <zephyr/sys/__assert.h>
#include <zephyr/sys/reboot.h>
#include <string.h>

LOG_MODULE_REGISTER(crashes, LOG_LEVEL_DBG);

#define CRASH_FN __attribute__((noinline, used, optimize("O0")))

static struct k_work crash_work;
static uint8_t scheduled_type;

CRASH_FN void crash_null_deref(void)
{
	volatile uint32_t *null_word = (volatile uint32_t *)0;

	*null_word = 0x4e554c4c;
	CODE_UNREACHABLE;
}

CRASH_FN void crash_div_by_zero(void)
{
	volatile int numerator = 12345;
	volatile int denominator = 0;
	volatile int result = numerator / denominator;

	ARG_UNUSED(result);
	CODE_UNREACHABLE;
}

CRASH_FN void crash_assert_fail(void)
{
	__ASSERT(false, "intentional crash_assert_fail");
	CODE_UNREACHABLE;
}

CRASH_FN void crash_bad_fn_ptr(void)
{
	void (*volatile bad_function)(void) = (void (*)(void))0xdeadbee1;

	bad_function();
	CODE_UNREACHABLE;
}

#if defined(CONFIG_CRASH_LAB_STACK_SMASH)
CRASH_FN static void stack_smash_recurse(unsigned int depth)
{
	volatile uint8_t consume[256];

	memset((void *)consume, (int)depth, sizeof(consume));
	stack_smash_recurse(depth + consume[0] + 1);
}

CRASH_FN void crash_stack_smash(void)
{
	stack_smash_recurse(0);
	CODE_UNREACHABLE;
}
#endif

const char *crash_type_name(uint8_t type)
{
	switch (type) {
	case CRASH_NULL_DEREF: return "null";
	case CRASH_DIV_ZERO: return "div0";
	case CRASH_ASSERT: return "assert";
	case CRASH_BAD_FN_PTR: return "fnptr";
	case CRASH_STACK_SMASH: return "stack";
	default: return "unknown";
	}
}

CRASH_FN static void crash_dispatch(uint8_t type)
{
	switch (type) {
	case CRASH_NULL_DEREF: crash_null_deref();
	case CRASH_DIV_ZERO: crash_div_by_zero();
	case CRASH_ASSERT: crash_assert_fail();
	case CRASH_BAD_FN_PTR: crash_bad_fn_ptr();
#if defined(CONFIG_CRASH_LAB_STACK_SMASH)
	case CRASH_STACK_SMASH: crash_stack_smash();
#endif
	default: k_panic();
	}
	CODE_UNREACHABLE;
}

CRASH_FN static void crash_scheduler_work(struct k_work *work)
{
	ARG_UNUSED(work);
	dump_store_note("dispatch crash=%s", crash_type_name(scheduled_type));
	printk("CRASHLAB dispatch=%s\n", crash_type_name(scheduled_type));
	crash_dispatch(scheduled_type);
}

int crash_schedule(uint8_t type)
{
	uint8_t max_type = IS_ENABLED(CONFIG_CRASH_LAB_STACK_SMASH) ? 4 : 3;

	if (type > max_type) {
		return -EINVAL;
	}
	if (k_work_busy_get(&crash_work) != 0) {
		return -EBUSY;
	}
	int ret = dump_store_arm(type);
	if (ret != 0) {
		diagnostics_record("storage", DIAG_COMMAND_FAILED, ret);
		return ret;
	}
	diagnostics_record("crash", DIAG_CRASH_ARMED, type);
	scheduled_type = type;
	return k_work_submit(&crash_work) < 0 ? -EIO : 0;
}

#if defined(CONFIG_CRASH_LAB_AUTO_CRASH)
static struct k_work auto_crash_work;

static void auto_crash_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	uint8_t count = IS_ENABLED(CONFIG_CRASH_LAB_STACK_SMASH) ? 5 : 4;
	(void)crash_schedule((uint8_t)(sys_rand32_get() % count));
}

static void auto_crash_timer(struct k_timer *timer)
{
	ARG_UNUSED(timer);
	k_work_submit(&auto_crash_work);
}
K_TIMER_DEFINE(auto_timer, auto_crash_timer, NULL);
#endif

void crash_auto_start(void)
{
	k_work_init(&crash_work, crash_scheduler_work);
#if defined(CONFIG_CRASH_LAB_AUTO_CRASH)
	k_work_init(&auto_crash_work, auto_crash_work_handler);
	k_timer_start(&auto_timer, K_SECONDS(CONFIG_CRASH_LAB_AUTO_CRASH_SECONDS), K_NO_WAIT);
	LOG_WRN("Automatic random crash enabled: %d seconds",
		CONFIG_CRASH_LAB_AUTO_CRASH_SECONDS);
#else
	LOG_INF("Automatic crash disabled; use BLE command or Button 1");
#endif
}

void k_sys_fatal_error_handler(unsigned int reason, const struct arch_esf *esf)
{
	ARG_UNUSED(esf);
	printk("CRASHLAB fatal=%u coredump_attempted; rebooting\n", reason);
	k_busy_wait(200000);
	sys_reboot(SYS_REBOOT_COLD);
	CODE_UNREACHABLE;
}
