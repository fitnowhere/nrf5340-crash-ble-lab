# Architecture and trust boundaries

```text
Zephyr application (ARM Cortex-M)
  LOG_* ── Zephyr deferred FS backend ──── /lfs/zephyr.####
  diag_sdk_record() ──────────────── /lfs/events.ndjson (PC + build ID)
  fatal exception ── SoC adapter ── raw flash dump ── /lfs/crash.bin
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
controller. The DK example composes the module with a best-effort nRF-specific
direct-NVMC adapter, littlefs, custom crash GATT, and MCUmgr BLE. The service
ignores host-precomputed symbolication and checks archive consistency itself.

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
| Fatal crash capture/ELF attribution | DK-proven under documented no-concurrent-flash assumptions; capture adapter is SoC-specific |
| Recoverable breadcrumbs and health metrics | Portable Zephyr Cortex-M module; app instruments error sites |
| Buffered kernel/app logs | Zephyr FS backend with bounded rotation |
| Retrieval | DK MCUmgr BLE gateway; other apps provide transport |
| Backend issue grouping/diagnosis/UI | Local, authenticated single-instance service |
| Hosted fleet auth/alerts/source hosting/OTA | **Not implemented** |

Do not claim feature parity with a commercial service until each missing area
has its own implementation, security review, and hardware validation.

## Known limitations

- The nRF5340 adapter erases and writes NVMC in fatal context. It can fail or
  lose the previous raw slot if a fault overlaps another flash operation,
  watchdog expiry, or power loss. A production design needs pre-erased,
  generation-tagged slots prepared outside fault context.
- LittleFS metadata updates use temporary-file replacement where practical,
  but a crash archive and its metadata are not one atomic transaction.
- Event/log retrieval is not a frozen device snapshot; the gateway detects
  crash sequence changes but concurrent event/log appends may require retry.
- Fingerprints and uploaded ELF hashes detect accidental mismatch. They are
  supplied by the gateway and are not signing, attestation, or device identity.
- Device names are gateway-observed BLE identifiers and can vary by host.
- The backend is a single-instance SQLite service. Uploaded ELF processing is
  bounded, but production deployments should add isolation, quotas, monitoring,
  backups, and an artifact registration/signing flow.
