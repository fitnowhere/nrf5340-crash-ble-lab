# Zephyr diagnostic SDK module

Portable ARM Cortex-M event/log metadata module. Register the repository root
through west module discovery or `ZEPHYR_EXTRA_MODULES`, enable
`CONFIG_DIAG_SDK=y`, mount the configured filesystem, then call:

```c
diag_sdk_start(APP_BUILD_FINGERPRINT);
diag_sdk_record(DIAG_SDK_LEVEL_ERROR, "sensor", SENSOR_TIMEOUT, -ETIMEDOUT);
```

It writes bounded `events.ndjson`, persistent boot count, periodic stack-health
events, and enables Zephyr's configured filesystem log backend. It deliberately
does not own board partitions, filesystem mounting, transport, authentication,
or a SoC-specific fault-safe coredump backend.

See [`docs/INSTALL_ZEPHYR.md`](../../docs/INSTALL_ZEPHYR.md) for complete
integration and build-ID instructions. API: `include/diag_sdk/diag_sdk.h`.
