# nRF5340 DK reference

## Target

- Board: `nrf5340dk/nrf5340/cpuapp`
- Network core: sysbuild `hci_ipc`
- Storage: 192 KiB LittleFS plus 64 KiB raw coredump partition
- Retrieval: MCUmgr SMP over BLE, read-only allowlisted diagnostic files

Build and flash from an NCS v3.4.0 environment:

```sh
west build -d build -b nrf5340dk/nrf5340/cpuapp --sysbuild . --pristine
west flash -d build --runner nrfjprog
```

The reference explicitly sets `CONFIG_CRASH_LAB_UNSAFE_BLE_COMMANDS=y` so the
gateway can induce faults on a development bench. This option defaults off and
must remain off in derived production firmware. MCUmgr writes and unrelated
paths are rejected by the filesystem access hook; diagnostic reads remain
unauthenticated.

## Crash provenance

The direct-NVMC header commits the faulting build's 20-character fingerprint
with length and checksum before its magic marker. After reboot, metadata uses
that stored fingerprint rather than silently relabeling a preserved dump with
new firmware. This is a mismatch guard—not cryptographic attestation.

## Capture constraint

The custom adapter exists because the stock flash driver takes a kernel
semaphore in fault context. It directly erases/programs NVMC and therefore
assumes no concurrent app-core flash operation. It is best-effort, not a
general “fault-safe” implementation. See [Roadmap](ROADMAP.md).
