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

## Final image recheck

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

This leaves the DK advertising with a valid archived dump for further testing.
