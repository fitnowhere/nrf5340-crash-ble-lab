#ifndef CRASH_LAB_FLASH_COREDUMP_H
#define CRASH_LAB_FLASH_COREDUMP_H

/* Returns the build fingerprint committed with the raw fault-time dump. */
int flash_coredump_build_fingerprint(char output[21]);

#endif
