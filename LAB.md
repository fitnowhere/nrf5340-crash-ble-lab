# nRF5340 DK lab notebook

Environment: NCS v3.4.0, Zephyr 4.4.0 (NCS tree), on-board DK probe
`1050058004`, app core `nrf5340dk/nrf5340/cpuapp`, sysbuild net core `hci_ipc`.
DEBUG OUT is disconnected. Python 3.13, Bleak 2.1.1, smpclient 7.3.0.
Mac CoreBluetooth address observed:
`B08D17D4-7714-3351-C3A4-A0444837A460` (machine-specific).
Automatic crashing disabled.

## Bring-up

`west build -b nrf5340dk/nrf5340/cpuapp --sysbuild . --pristine` completed
with an app ELF of ~253 KiB flash and an `hci_ipc` ELF of ~131 KiB. The first
attempt at `west flash` with `nrfutil` was blocked (not installed). Repeating
with `west flash -d build --runner nrfjprog` flashed and verified both cores.

The first image hit a BLE TX stack overflow during debug-level kernel logs;
lowered log level and enlarged the TX/RX thread stacks. The original Zephyr
flash-partition backend hit a secondary `sem.c:136` assertion on every real
fault (`k_sem_take(K_FOREVER)` in exception context), leaving the flash header
erased. Replaced its *fault path* with `src/flash_coredump.c`; subsequent fault
captures survived reboots. These initial failures were **not** counted as
successful coredumps.

Sample working boot:

```text
CRASHLAB build=lab-1+uncommitted
CRASHLAB flash_dump_valid=1 raw_size=1060 backend_error=0
<inf> dump_store: dump pending / copied to /lfs/crash.bin (1060 bytes)
<inf> bt_hci_core: HCI transport: IPC
<inf> dummy_ble: advertising crash-lab
```

## Four distinct hardware crash proofs (September 30, 2026)

Each transfer produced `meta.txt`, `recent.log`, `crash.bin`, `zephyr.elf`,
`manifest.json` and `symbolication.json` under its listed local directory.
Build fingerprint `245387f13f3cb1a5e402`; archived app ELF SHA-256
`9990cfd2a055605acecd095d78738af1bd6370aaed44ea235a8cf15a720a8dfe`.
This fingerprint refers to the tested image, not necessarily the latest source
after subsequent lab cleanup. Keep the **archived** ELF for reproducing these
line numbers.

### Assert (saved `host/out/20260930T124019Z/`)

```text
$ python3 host/pull_crash.py crash assert
Requested assert; ... Wait for reboot, then pull.
UART: CRASHLAB dispatch=assert
UART: ASSERTION FAIL [0] @ CMAKE_SOURCE_DIR/src/crashes.c:39
UART: CRASHLAB fatal=4 coredump_attempted; rebooting
UART (next boot): CRASHLAB flash_dump_valid=1 raw_size=1060 backend_error=0
$ python3 host/pull_crash.py --addr2line /opt/nordic/ncs/toolchains/ccc010f809/opt/zephyr-sdk/gnu/arm-zephyr-eabi/bin/arm-zephyr-eabi-addr2line symbolicate host/out/20260930T124019Z/crash.bin
PC 0x00026ada: assert_post_action
/opt/nordic/ncs/v3.4.0/zephyr/lib/os/assert.c:44
LR call site 0x0000251d: crash_assert_fail
/Users/rudraksh/Desktop/Moware/Firmware/MoCore/nrf5340-crash-ble-lab/src/crashes.c:39 (discriminator 2)
VERIFIED crash_assert_fail ... src/crashes.c:39
```

GDB pipe replay with Zephyr `coredump_gdbserver.py` also returned:

```text
#0  0x00026ada in assert_post_action (...) at .../zephyr/lib/os/assert.c:44
#1  0x0000251e in crash_assert_fail () at .../src/crashes.c:39
#2  0x00002578 in crash_dispatch (type=2) at .../src/crashes.c:84
```

### Null dereference (saved `host/out/20260930T124102Z/`)

```text
$ python3 host/pull_crash.py crash null
Requested null; ... Wait for reboot, then pull.
$ python3 host/pull_crash.py pull
.../crash.bin: 1068 bytes
PC 0x000024fa: crash_null_deref
/Users/rudraksh/Desktop/Moware/Firmware/MoCore/nrf5340-crash-ble-lab/src/crashes.c:23
LR call site 0x0000256f: crash_dispatch
.../src/crashes.c:82
VERIFIED crash_null_deref ... src/crashes.c:23 (reason=25, ELF sha256=9990cfd2...)
```

### Divide by zero (saved `host/out/20260930T124219Z/`)

```text
$ python3 host/pull_crash.py crash div0
Requested div0; ... Wait for reboot, then pull.
$ python3 host/pull_crash.py pull
.../crash.bin: 1076 bytes
PC 0x00026400: crash_div_by_zero
/Users/rudraksh/Desktop/Moware/Firmware/MoCore/nrf5340-crash-ble-lab/src/crashes.c:31
VERIFIED crash_div_by_zero ... src/crashes.c:31 (reason=30, ELF sha256=9990cfd2...)
```

### Bad function pointer (saved `host/out/20260930T124252Z/`)

```text
$ python3 host/pull_crash.py crash fnptr
Requested fnptr; ... Wait for reboot, then pull.
$ python3 host/pull_crash.py pull
.../crash.bin: 1068 bytes
GDB backtrace:
#0  0xdeadbee0 in ?? ()
#1  0x0000253e in crash_bad_fn_ptr () at .../src/crashes.c:47
PC 0xdeadbee0: ??
??:0
LR call site 0x0000253d: crash_bad_fn_ptr
.../src/crashes.c:47
VERIFIED crash_bad_fn_ptr ... src/crashes.c:47 (reason=20, ELF sha256=9990cfd2...)
```

## Negative paths and follow-ups

| Case | Observed result / how to exercise safely |
| --- | --- |
| Wrong app ELF / net-core ELF | `python3 host/pull_crash.py --elf build/hci_ipc/zephyr/zephyr.elf symbolicate host/out/20260930T124102Z/crash.bin` returned `Wrong ELF for dump fingerprint ...`. Expected and tested. |
| OTA/image management without bootloader | MCUmgr `ImageStatesRead()` against the DK returned SMP group 1 `MGMT_ERR.ENOTSUP (8)`; tested. No image upload should be attempted because the image group and MCUboot are intentionally absent. |
| Clean reboot persistence | Initially `reboot` disconnected before the ATT Write Response. Added a 300 ms response grace period. **Retested on final image**: `reboot` returned success and `status` after restart showed `dump_pending: true`, `dump_size: 1060`, `crash_seq: 7`, `reset_reason: 2` (software reset). |
| BLE loss / absent device | `--address 00000000-0000-0000-0000-000000000000 --timeout 2 status` returned `Error: BLE device ... not found` and exit 1, tested. Interrupting SMP transfer mid-file remains an untested recovery scenario; retry `pull` after reconnection. |
| Corrupt/truncated binary | `python3 -m unittest discover -s host/tests -v` tests invalid `ZE` magic, truncated register section and wrong target; `symbolicate` checks dump size against metadata and never calls addr2line for a truncated dump. |
| Fault while LittleFS writes | Direct NVMC fault backend is not safe if another app-core flash operation holds NVMC; no hard guarantee. This is deliberately a documented limitation, not a passed test. |
| Full partition / unmountable filesystem | Backend leaves its commit magic erased on overflow; unmountable nonblank LittleFS is preserved, **not** autoformatted. These physical failure injections have not been run. |

The lab is intentionally unauthenticated and unsuitable for production deployment.
Avoid mass erase if retaining the local flash evidence matters. The Git-ignored
`host/out/` archives are the authoritative binaries for the above measurements.

## Earlier crash-only image recheck

After cleanup, pristine sysbuild succeeded, both images still resolved, six
offline `unittest` checks passed, and app-core-only programming verified.
Programming live flash while the app core was still running yielded an
unwanted 134-byte fault dump. Cleared it using `host/pull_crash.py clear`
(`dump_pending: false`), then requested a new assert on the final image:

```text
fingerprint: f752df22842aed60fdc9
host/out/20260930T124823Z/crash.bin: 1060 bytes
GDB #1  0x0000251e in crash_assert_fail () at .../src/crashes.c:39
VERIFIED crash_assert_fail ... src/crashes.c:39
ELF sha256=79839b9d3215adf8caeca442cbabe7b377571297c320b0746805b2e94ded7243
$ python3 host/pull_crash.py reboot
Requested clean reboot
$ python3 host/pull_crash.py status
"reset_reason": 2, "dump_pending": true, "dump_size": 1060, "crash_seq": 7
```

This measured image left the DK advertising with a valid archived dump for
further testing; newer builds have their own ELF fingerprint.

## Persistent diagnostics extension (DK hardware, September 30, 2026)

Tested from source commit `1bca685` on the same DK with NCS v3.4.0. The
sysbuild compiled both images without Kconfig/compile warnings; **nine** host
parser/error-path tests passed. Build fingerprint:
`7a790e03e5b53f9677bb`. Flashing a running app generated the known spurious
134-byte partial dump; `clear` removed it before this test. No simulated event
below is described as a real storage or OTA failure.

### Logs and recoverable events without a crash

```text
$ python3 host/pull_crash.py emit storage_failure
Injected synthetic storage_failure diagnostic (not a real failure)
$ python3 host/pull_crash.py logs
host/out/20260930T131100Z/events.ndjson: 3893 bytes
host/out/20260930T131100Z/zephyr.0000: 7089 bytes
simulated warning code=102 value=-5 boot=3 ... section=text:
  command_work_handler .../src/dummy_ble.c:84
health info code=20 value=2524 boot=3 t=60677ms section=text:
  heartbeat_handler .../src/diagnostics.c:27
ble info code=11 value=19 boot=3 ... section=text:
  disconnected .../src/dummy_ble.c:168
```

The plain-text filesystem log contains the matching Zephyr message
`<wrn> dummy_ble: Synthetic diagnostic 102 (not a hardware failure)` and
`<wrn> bt_l2cap: Ignoring data for unknown channel ID 0x003a`. Those lines
have timestamp/module/severity but no call-site PC; the structured event is
what enables ELF file/line lookup. Earlier events on the same flash used a
different fingerprint; the host explicitly printed `different build (requires
its own archived ELF)` rather than mis-symbolicating them.

`python3 host/pull_crash.py ota-check` returned MCUmgr image group 1
`MGMT_ERR.ENOTSUP (8)` as designed (no MCUboot/OTA slot).

### Crash, logs, and code line together

```text
$ python3 host/pull_crash.py crash assert
Requested assert; ... Wait for reboot, then pull.
$ python3 host/pull_crash.py pull
host/out/20260930T131224Z/crash.bin: 444 bytes
host/out/20260930T131224Z/events.ndjson: 5329 bytes
host/out/20260930T131224Z/zephyr.0000: 8119 bytes
host/out/20260930T131224Z/zephyr.0001: 1156 bytes
GDB #1  0x00002582 in crash_assert_fail () at .../src/crashes.c:40
VERIFIED crash_assert_fail ... src/crashes.c:40
crash warning code=2 value=2 boot=3 ... section=text:
  crash_schedule .../src/crashes.c:119
ELF sha256=4dd8da58f90dfdaf13c26b7bf84c9ff0f0c4cd82e49f8bafde86e7400b2985e9
$ python3 host/pull_crash.py reboot
Requested clean reboot
$ python3 host/pull_crash.py status
"reset_reason": 2, "dump_pending": true, "dump_size": 444,
"crash_seq": 13, "boot_count": 5,
"log_files": ["zephyr.0000", "zephyr.0001"]
```

The ELF and event files for these measurements are in the Git-ignored
`host/out/` folders above. Rebuilds after documentation commits receive a new
fingerprint; replay the saved dumps with each folder's archived `zephyr.elf`.

## Reusable SDK and service validation

> These are dated measurements, not production guarantees. The current
> architecture/security qualifications in `docs/ARCHITECTURE.md` supersede
> earlier uses of “fault-safe,” “independent verification,” or “persistent.”

The diagnostic implementation was subsequently extracted to the discoverable
Zephyr module at `sdk/zephyr/`; a pristine nRF5340 sysbuild completed with no
warnings and exported `diag_sdk_start`, `diag_sdk_record`, status, and log-file
enumeration symbols. The extracted module was flashed and a live logs-only
BLE collection uploaded directly to a fresh local service instance:

```text
Uploaded report bf2b54775d44485b93379191c4d55948 (issue=None)
device: B08D17D4-7714-3351-C3A4-A0444837A460
fingerprint: 6d733011fecd970e2134
event_count: 104
log_count: 270
```

An earlier real divide-by-zero archive was also uploaded through the gateway;
the server checked the archive's ELF/fingerprint/SHA consistency and returned:

```text
reason: Divide-by-zero UsageFault at crash_div_by_zero
issue: 7e1948b682dbe600379c
events: 64
logs: 205
```

Service tests use the real null/div0/assert/log-only DK archives when present
and cover authentication, invalid ZIP paths, wrong ELF/build ID, wrong dump
size, issue grouping, idempotent replay, and HTML escaping. Compose syntax and
the installable Python wheel were validated. Docker image execution was not
tested because Docker Desktop's daemon was not running in this environment.
