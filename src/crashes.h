#ifndef CRASH_LAB_CRASHES_H
#define CRASH_LAB_CRASHES_H

#include <stdint.h>

enum crash_lab_type {
	CRASH_NULL_DEREF = 0,
	CRASH_DIV_ZERO = 1,
	CRASH_ASSERT = 2,
	CRASH_BAD_FN_PTR = 3,
	CRASH_STACK_SMASH = 4,
};

const char *crash_type_name(uint8_t type);
int crash_schedule(uint8_t type);
void crash_auto_start(void);

#endif
