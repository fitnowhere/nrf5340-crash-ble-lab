# Roadmap

These items are recommendations, not implemented claims.

## Capture integrity

- Pre-erase multiple generation/CRC crash slots outside fault context.
- Preserve the last valid slot when a new capture is interrupted.
- Add power-cut, watchdog and concurrent-LittleFS stress tests.
- Atomically seal event/log generations before retrieval.

## Identity and security

- Register immutable symbol artifacts by linker build ID and ELF SHA-256.
- Sign firmware/report manifests and enroll stable device identities.
- Require authenticated, encrypted BLE retrieval or use a secure transport.
- Add per-device credentials, rotation, RBAC and audit logs.

## Service operations

- Isolate ELF/GDB analysis in networkless resource-limited workers.
- Add retention quotas, export/deletion, backups and disk monitoring.
- Add PostgreSQL/object storage options and horizontal workers.
- Add alerts, fleet metrics, source links and release regressions.

## Product integrations

- OTA orchestration with signed images and rollback policy.
- Configurable heap/thread/battery/custom metric aggregation.
- Multi-product issue signatures using normalized source/module/frame context.
