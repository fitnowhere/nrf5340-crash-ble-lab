# Self-hosted diagnostic backend

Install from the repository root:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[gateway]'
export DIAG_SERVICE_TOKEN='<at least 16 random characters>'
.venv/bin/zephyr-diag-service
```

The authenticated API/UI ingests gateway ZIP evidence, verifies the exact
unstripped ELF, identifies ARM Cortex-M fatal reasons, maps crashes/events to
function/file/line/section, groups crash issues, and stores original evidence
plus SQLite indexes. See [`docs/SERVICE.md`](../docs/SERVICE.md) for deployment,
API, limits, security and unsupported commercial-cloud features.
