# Open Zephyr diagnostics: SDK, gateway, service, DK lab

This repository now has three reusable pieces:

1. [`sdk/zephyr/`](sdk/zephyr/) — installable Cortex-M Zephyr module for
   persistent PC-bearing events, reboot count and health metrics.
2. [`host/pull_crash.py`](host/pull_crash.py) — BLE MCUmgr gateway that archives
   the precise ELF and optionally uploads raw evidence.
3. [`service/`](service/) — self-hosted API and browser UI that independently
   verifies the ELF and diagnoses crashes/events/logs.

Start with [Zephyr installation](docs/INSTALL_ZEPHYR.md),
[service setup/API](docs/SERVICE.md), and [architecture/feature boundary](docs/ARCHITECTURE.md).
The DK example below remains a ready-to-flash end-to-end reference.
All newly authored SDK/service code is licensed under Apache-2.0; see
[`LICENSE`](LICENSE).

Standalone Zephyr/NCS v3.4.0 experiment for the **onboard** nRF5340 DK
(`nrf5340dk/nrf5340/cpuapp`). It does not use or alter product firmware. The
sysbuild net-core image is `hci_ipc`. No MCUboot, DFU slot, or OTA update is
included; flash both cores over the DK's onboard debugger.

## Quick start

In an NCS v3.4.0 shell with Zephyr SDK, `west`, and `nrfjprog` available:

```sh
export ZEPHYR_BASE=/opt/nordic/ncs/v3.4.0/zephyr
export ZEPHYR_TOOLCHAIN_VARIANT=zephyr/gnu
export ZEPHYR_SDK_INSTALL_DIR=/opt/nordic/ncs/toolchains/ccc010f809/opt/zephyr-sdk
west build -b nrf5340dk/nrf5340/cpuapp --sysbuild . --pristine
west flash -d build --runner nrfjprog
python3 -m venv .venv
.venv/bin/python -m pip install -r host/requirements.txt
.venv/bin/python host/pull_crash.py scan
.venv/bin/python host/pull_crash.py status
.venv/bin/python host/pull_crash.py crash null
# After the device reboots and advertises again:
.venv/bin/python host/pull_crash.py pull
.venv/bin/python host/pull_crash.py logs
```

If the SDK tool binaries are not on `PATH`, add
`$ZEPHYR_SDK_INSTALL_DIR/gnu/arm-zephyr-eabi/bin`. `pull_crash.py` discovers
Nordic's standard SDK path automatically or accepts `--addr2line`. On macOS,
`--address` is a CoreBluetooth UUID; use the value printed by `scan` if more
than one lab device is present. The DK's two USB serial ports expose HCI IPC
and application UART at 115200 baud (identify by the `CRASHLAB` banner).

Crash commands: `null`, `div0`, `assert`, `fnptr`; optional `stack` with
`CONFIG_CRASH_LAB_STACK_SMASH=y`. Button 1 cycles the crash types; Button 2
does a clean reboot. `reboot` and `clear` are also host commands. Automatic
random crashes are **off** by default; opt in using
`CONFIG_CRASH_LAB_AUTO_CRASH=y` and `CONFIG_CRASH_LAB_AUTO_CRASH_SECONDS`.
For recoverable *examples* (clearly marked synthetic), run
`python3 host/pull_crash.py emit recoverable` or choose `ble_failure`,
`storage_failure`, `ota_rejected`; then `python3 host/pull_crash.py logs`.
`python3 host/pull_crash.py ota-check` checks that real MCUmgr image
management is unsupported. `logs` works even without a dump.

## Data path

| nRF5340 app-core flash | Address | Length |
| --- | ---: | ---: |
| Application | `0x00000` | 768 KiB |
| LittleFS | `0xC0000` | 192 KiB |
| Fault-safe Zephyr coredump | `0xF0000` | 64 KiB |

An intentional fault enters Zephyr's coredump core. `src/flash_coredump.c`
commits the raw binary dump to the dedicated partition with synchronous NVMC
writes and a checksum, then the fatal hook reboots. Early at boot, the app
verifies the checksum, copies the dump into `/lfs/crash.bin`, writes
`/lfs/meta.txt`, and only then invalidates the flash copy. `/lfs/recent.log`
records the armed crash and boots. A failed copy leaves the flash copy intact.
MCUmgr SMP over BLE serves the files; custom GATT handles crash commands and
JSON status. The host saves all three files and the **matching unstripped app
ELF** under `host/out/<UTC timestamp>/`, with a SHA-256 manifest.

## Diagnostics and ELF attribution

- Each boot records the reset-cause flag and a persistent boot count. Fatal
  faults also retain CPU registers and the full GDB backtrace.
- Real BLE connection/disconnection events and application errors go into
  `/lfs/events.ndjson` with code, value, uptime, boot number, build fingerprint
  and **caller PC**. A periodic health event records unused system-workqueue
  stack bytes (default every 60 seconds). Synthetic sample events use IDs
  100–103 and are never presented as actual failures.
- Zephyr's filesystem log backend saves kernel, Bluetooth and application
  messages in bounded `/lfs/zephyr.####` text files (four 8-KiB files). These
  retain timestamp, module and severity. A plain text log line does **not**
  contain a caller PC: instrument a recoverable error path with
  `diagnostics_record()` if it needs exact ELF file/line attribution.
- `logs` fetches the above data independently; `pull` includes it with a crash.
  Both archive the unstripped ELF. The host resolves event PCs only against
  an ELF whose source/config/commit fingerprint matches each event, printing
  function, file, line and ELF section. Old-build events remain visible but
  **are not** symbolicated with a newer ELF. Offline replay:
  `python3 host/pull_crash.py decode-events host/out/<timestamp>`.
- BLE/SMP transfer errors during a pull save `host_failure.json` alongside any
  partial files. Those are host-side errors and have no device PC.

This is a local Memfault-*style* diagnostic pipeline, **not** the Memfault SDK
or full cloud feature parity. It cannot derive a code line for arbitrary text
logs, capture faults that block flash writes, infer heap leaks or battery
health, upload to a cloud service or perform OTA. New subsystems need their
own instrumented events and metrics; synthetic failures are not proof of
hardware faults.

`pull` verifies firmware status, archive metadata, coredump size, and the
source/config fingerprint embedded in the ELF. It then runs Zephyr's
`coredump_gdbserver.py` via GDB's pipe transport for a backtrace, plus direct
PC/LR symbolication using `arm-zephyr-eabi-addr2line` (useful when GDB is
unavailable or the PC is outside the ELF). For an offline replay:

```sh
python3 host/pull_crash.py symbolicate host/out/<timestamp>/crash.bin
```

**Preserve the ELF before rebuilding**: a new ELF can have different addresses
even with the same source version. An older crash pulled after reflashing may
not match the current status fingerprint; use the archived ELF for offline
symbolication. Source fingerprint checks cannot cryptographically prove what
was flashed, so record the flashed ELF SHA-256 in your lab notebook. The
`host/out/` directory and `build/` are Git-ignored intentionally.

## Important limitations

- This is deliberately **not** a secure or production-ready BLE service. SMP
  filesystem access and crash commands are unauthenticated; do not put secrets
  in this lab image. There is no OTA updater/MCUboot, and unsupported image
  management requests must be rejected.
- Zephyr's standard flash-partition coredump backend uses the nRF flash driver,
  which takes `k_sem_take(K_FOREVER)` during the Cortex-M fault. With assertions
  enabled this causes a second exception. This lab's fault-only backend uses
  direct NVMC writes. It assumes no concurrent flash operation when a fault
  happens; failures during an active LittleFS write can still prevent a dump.
- Only the most recent flash crash is captured. Triggering another crash can
  replace a previously archived device dump. Pull each crash before testing
  the next one. `recent.log` rotates at ~16 KiB.
- A corrupt *existing* LittleFS partition is not auto-formatted. Only an
  erased/blank partition is formatted; investigate corruption rather than
  erasing crash evidence.

See [LAB.md](LAB.md) for measured DK transcripts, failures, and recovery.
