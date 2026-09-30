# Architecture and trust boundaries

```text
Zephyr application (ARM Cortex-M)
  LOG_* ── Zephyr deferred FS backend ──── /lfs/zephyr.####
  diag_sdk_record() ──────────────── /lfs/events.ndjson (PC + build ID)
  fatal exception ── safe SoC backend ── flash dump ── /lfs/crash.bin
  reset/start ─────────────────────── /lfs/boot_count.txt + meta.txt
                   │
       board-specific BLE MCUmgr FS or another transport
                   │
          host gateway archives raw files + exact application ELF
                   │ authenticated ZIP, HTTPS for remote use
                   ▼
     self-hosted FastAPI service → SQLite index + raw artifact store
                   │ Zephyr fatal reason + addr2line/GDB + event PCs
                   ▼
        browser/API: devices, releases, grouped issues, reports
```

The SDK is a Zephyr module and **does not** own the transport or the SoC flash
controller. The DK example composes the module with nRF-specific fault-safe
flash, littlefs, custom crash GATT, and MCUmgr BLE. The service never trusts
host-precomputed symbolication; it revalidates the unstripped ELF and dump.

`status.fingerprint` (20 lowercase hex characters) tags one build. It must be
generated from all compilation inputs and embedded in the flashed image; the
gateway also records the ELF SHA-256. The server verifies the fingerprint in
the ELF bytes and the reported SHA. This detects accidental mismatches, but
is **not cryptographic device attestation**. Older-build events on a persistent
filesystem require *their own* archived ELF before they can be symbolicated.

Fatal dumps provide stack/register context. Recoverable events give one
explicit call-site PC and a numeric error value, **not** an inferred backtrace.
Ordinary Zephyr text logs carry module/severity/time but no PC; no service can
recover a uniquely correct source line from text that lacks a site identifier.

## Feature boundary relative to Memfault

| Area | This project |
| --- | --- |
| Fatal crash capture/ELF attribution | DK-proven; generic capture backend must be provided per SoC |
| Recoverable breadcrumbs and health metrics | Portable Zephyr Cortex-M module; app instruments error sites |
| Buffered kernel/app logs | Zephyr FS backend with bounded rotation |
| Retrieval | DK MCUmgr BLE gateway; other apps provide transport |
| Backend issue grouping/diagnosis/UI | Local, authenticated single-instance service |
| Hosted fleet auth/alerts/source hosting/OTA | **Not implemented** |

Do not claim feature parity with a commercial service until each missing area
has its own implementation, security review, and hardware validation.
