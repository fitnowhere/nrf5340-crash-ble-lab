# Testing and evidence

## Automated

```sh
python -m pytest service/tests -q
python -m unittest discover -s host/tests -q
python -m pip wheel . --no-deps --wheel-dir dist
DIAG_SERVICE_TOKEN=integration-test-token-12345 docker compose config --quiet
```

The repository tracks a small ARM ELF/DWARF fixture and synthetic Cortex-M dump
so clean clones exercise successful ingestion and symbolication. Local ignored
DK archives add optional regression cases. GitHub CI runs Python tests, wheel
packaging, Compose validation and a container build.

## Firmware

The reference is validated with a pristine NCS v3.4.0 sysbuild. Hardware is not
available to GitHub-hosted CI, so compilation does not replace board testing.
Measured fault, reboot persistence, BLE retrieval and exact source mappings are
recorded in the repository's
[`LAB.md`](https://github.com/fitnowhere/nrf5340-crash-ble-lab/blob/main/LAB.md),
including firmware fingerprints and caveats.

Before a release, repeat at least: clean flash, status, logs-only upload, each
fault type, pull/upload, exact ELF rejection, and reboot persistence. A future
production backend also needs power-cut, concurrent-flash and malformed-ELF
fuzz testing.
