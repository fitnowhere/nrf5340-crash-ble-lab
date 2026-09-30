#ifndef CRASH_LAB_DUMP_STORE_H
#define CRASH_LAB_DUMP_STORE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

int dump_store_init(void);
void dump_store_note(const char *fmt, ...);
int dump_store_arm(uint8_t type);
bool dump_store_pending(void);
size_t dump_store_size(void);
uint32_t dump_store_reset_reason(void);
uint32_t dump_store_sequence(void);
int dump_store_clear(void);

#endif
