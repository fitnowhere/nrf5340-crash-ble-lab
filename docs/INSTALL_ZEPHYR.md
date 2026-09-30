# Install the Zephyr diagnostic SDK

Scope: **Zephyr ARM Cortex-M**, tested with nRF5340 DK and NCS v3.4.0 /
Zephyr 4.4. The portable module is in `sdk/zephyr/`. No product firmware is
modified by this repository. Other Cortex-M boards need their own partition,
filesystem mount, coredump backend and transport configuration.

## 1. Add the module

Clone this repository and add its root directory as a Zephyr module.
For a local application, add this **before** `find_package(Zephyr ...)`:

```cmake
cmake_minimum_required(VERSION 3.20.0)
list(APPEND ZEPHYR_EXTRA_MODULES "/absolute/path/nrf5340-crash-ble-lab")
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(your_firmware)
target_sources(app PRIVATE src/main.c)
```

For a west workspace, add the repository as a project. Root
`zephyr/module.yml` enables automatic discovery; `sdk/zephyr/zephyr/module.yml`
also permits directly registering the SDK subdirectory. The application at the
repository root is a complete working example using this module.

## 2. Configure capture and storage

At minimum, the board/app must mount a filesystem before starting the SDK.
Choose your own storage partition; **do not** reuse a bootloader/OTA partition.

```conf
CONFIG_FILE_SYSTEM=y
CONFIG_FILE_SYSTEM_LITTLEFS=y
CONFIG_FLASH=y
CONFIG_FLASH_MAP=y
CONFIG_INIT_STACKS=y
CONFIG_THREAD_STACK_INFO=y
CONFIG_LOG=y
CONFIG_LOG_MODE_DEFERRED=y
CONFIG_LOG_BACKEND_FS=y
CONFIG_LOG_BACKEND_FS_AUTOSTART=n
CONFIG_LOG_BACKEND_FS_DIR="/lfs"
CONFIG_LOG_BACKEND_FS_FILE_PREFIX="zephyr."
CONFIG_LOG_BACKEND_FS_FILE_SIZE=8192
CONFIG_LOG_BACKEND_FS_FILES_LIMIT=4
CONFIG_DIAG_SDK=y
CONFIG_DIAG_SDK_MOUNT_POINT="/lfs"
CONFIG_DIAG_SDK_LOG_FILE_PREFIX="zephyr."
CONFIG_DIAG_SDK_METRICS_INTERVAL_SECONDS=60
CONFIG_DIAG_SDK_MAX_EVENTS_BYTES=24576
```

Set `CONFIG_LOG_MAX_LEVEL` to your desired compiled severity. Disabled logs
cannot be recovered later. Match the FS logger's directory/prefix to the SDK
and gateway. The SDK enables the FS backend **after** `diag_sdk_start()`; it
does not autoformat a corrupt filesystem or allocate a partition for you.

## 3. Give each build a unique fingerprint

The server requires an unstripped ELF with the same 20-hex-character ID
embedded in firmware events and status metadata. Never use a fixed release
name for this ID: a rebuild can change code addresses even when source labels
look identical. A CMake helper is included:

```cmake
include("/absolute/path/nrf5340-crash-ble-lab/sdk/zephyr/cmake/DiagSdkBuildId.cmake")
diag_sdk_fingerprint(APP_BUILD_ID SOURCES
  "${CMAKE_CURRENT_SOURCE_DIR}/src/main.c"
  "${CMAKE_CURRENT_SOURCE_DIR}/prj.conf"
  "${CMAKE_CURRENT_SOURCE_DIR}/boards/your_board.overlay")
configure_file(src/build_id.h.in ${CMAKE_CURRENT_BINARY_DIR}/generated/build_id.h @ONLY)
target_include_directories(app PRIVATE ${CMAKE_CURRENT_BINARY_DIR}/generated)
```

`src/build_id.h.in`:

```c
#define APP_BUILD_FINGERPRINT "@APP_BUILD_ID@"
```

List **all** application sources, overlays and config fragments that change
the image. The helper additionally hashes Zephyr's resolved `.config` and the
current Git revision, and tracks sources/branch ref for incremental CMake
reconfiguration. For external libraries, either add their files/revisions to
the fingerprint inputs or derive a stronger firmware build ID yourself.
Archive the **exact flashed app-core** `zephyr.elf` for every build; the
fingerprint alone is not a cryptographic attestation of what was flashed.

## 4. Start and record

```c
#include <diag_sdk/diag_sdk.h>
#include "build_id.h"

/* Your application has already mounted /lfs. */
int rc = diag_sdk_start(APP_BUILD_FINGERPRINT);
if (rc != 0) {
    /* Report via UART; flash logging may be unavailable. */
}

/* Thread context, static ASCII category, app-defined stable numeric code.
 * Saved event PC points back to this call site in the matching ELF. */
diag_sdk_record(DIAG_SDK_LEVEL_ERROR, "sensor", 42, -5);
```

`diag_sdk_start()` records a persistent boot count and enables the configured
filesystem logger; `diag_sdk_record()` synchronously appends one JSON-line
event with return PC and build ID. Read status via
`diag_sdk_boot_count()`, `diag_sdk_event_count()` and
`diag_sdk_log_files(buffer, length)`. Calls from an ISR or before startup are
ignored. **Never** call the filesystem API from a fatal exception.

## 5. Add crash capture and retrieval

The portable module collects events and logs, **not** a hardware flash
driver. For crash dumps, enable Zephyr coredump and select a backend safe in
fault context on your SoC. The nRF5340 lab demonstrates a synchronous NVMC
adapter in `src/flash_coredump.c`. It is **not** portable to STM32 or nRF54;
it also assumes no concurrent flash write when a fault occurs.

Expose `/lfs/events.ndjson`, `/lfs/zephyr.####`, and optionally
`/lfs/crash.bin`, `/lfs/meta.txt`, `/lfs/recent.log` via MCUmgr filesystem
over BLE (as the DK example does), another secure transport, or your own
gateway. The self-hosted service accepts a ZIP bundle containing `manifest.json`
and the matching `zephyr.elf` plus these optional files. It does **not**
require a Memfault account. See [SERVICE.md](SERVICE.md).

**Safety:** The DK example's MCUmgr FS and crash commands are unauthenticated.
Do not copy that security policy into deployed devices. Authenticate/bond the
transport and restrict filesystem paths before sending production diagnostics.
