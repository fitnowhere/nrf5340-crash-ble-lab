# Open Zephyr Diagnostics

An experimental, self-hosted diagnostics pipeline for Zephyr ARM Cortex-M:

1. an installable Zephyr event/log SDK;
2. an nRF5340 DK crash-capture and BLE reference application;
3. a host gateway that archives the exact ELF; and
4. an authenticated FastAPI service for ingestion, issue grouping and
   function/file/line symbolication.

## Start here

- [Install the Zephyr SDK](INSTALL_ZEPHYR.md)
- [Use the gateway](GATEWAY.md)
- [Deploy the backend](SERVICE.md)
- [Understand architecture and limitations](ARCHITECTURE.md)
- [Reproduce the nRF5340 reference](NRF5340_REFERENCE.md)
- [Review test and hardware evidence](TESTING.md)
- [Troubleshoot common failures](TROUBLESHOOTING.md)
- [Review planned production features](ROADMAP.md)

!!! warning
    This is not the Memfault SDK or a commercial cloud replacement. It has no
    device attestation, tenant isolation, OTA orchestration, alert delivery,
    SSO/RBAC, or guaranteed crash delivery. The reference BLE control channel
    is intentionally unsafe when enabled for bench testing.
