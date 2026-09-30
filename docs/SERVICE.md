# Self-hosted diagnostic service

The Python service in `service/` receives the BLE gateway's archived evidence,
verifies the matching unstripped ELF, identifies Zephyr ARM Cortex-M fatal
reasons, symbolicates PC/LR and event call sites, groups repeated crashes by
reason/function, and shows devices/releases/reports, events, kernel logs and
stack traces in a small authenticated browser UI. It is local software, **not
Memfault cloud feature parity**.

## Run locally

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[gateway,test]'
export DIAG_SERVICE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export DIAG_SERVICE_DATA="$PWD/service/data"
export ZEPHYR_BASE=/opt/nordic/ncs/v3.4.0/zephyr
export ZEPHYR_SDK_INSTALL_DIR=/opt/nordic/ncs/toolchains/ccc010f809/opt/zephyr-sdk
.venv/bin/zephyr-diag-service --host 127.0.0.1 --port 8765
```

In another terminal, use the **same** `DIAG_SERVICE_TOKEN`:

```sh
export DIAG_SERVICE_URL=http://127.0.0.1:8765
export DIAG_SERVICE_TOKEN='<the token from the server terminal>'
python3 host/pull_crash.py logs
# Or after a crash:
python3 host/pull_crash.py pull
# Or upload an earlier, Git-ignored local archive without connecting to BLE:
python3 host/pull_crash.py upload host/out/<timestamp>
```

Open `http://127.0.0.1:8765/`. Your browser will ask for HTTP Basic
credentials: username `admin`, password the same token. The gateway uses a
Bearer token. The service binds to loopback by default. Use HTTPS and a
proper reverse proxy/identity layer for any nonlocal deployment; the simple
shared token and SQLite database are **not** a multi-tenant cloud service.

### Docker Compose (optional)

```sh
export DIAG_SERVICE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
docker compose up --build -d
```

The port binds to `127.0.0.1:8765` and a named volume stores SQLite and raw
artifacts. The container includes ARM `addr2line` for file/line mapping. For
GDB backtraces, also supply a compatible GDB and Zephyr tree via `DIAG_GDB`
and `ZEPHYR_BASE`, or inspect the archived dump locally with the host tool.

## API and evidence format

- `POST /api/v1/reports`: ZIP body, Bearer token. Returns report ID, grouped
  issue key and reason. Duplicate ZIP uploads return the same ID.
- `GET /api/v1/reports`, `GET /api/v1/reports/{id}`: summaries and paged raw
  logs/events plus crash locations. `GET /api/v1/reports/{id}/files/{name}`
  downloads original artifacts.
- `GET /api/v1/issues`, `/api/v1/devices`, `/api/v1/releases`: grouped views.
  `GET /health` reports service readiness without disclosing evidence.

The ZIP must contain:

```
manifest.json  # device, status.fingerprint, status.build, elf_sha256
zephyr.elf     # unstripped application ELF; not the HCI IPC/net-core ELF
```

It may also contain `events.ndjson`, `recent.log`, `zephyr.####`, and the
`crash.bin` **together with** `meta.txt`. Server analysis ignores the host's
precomputed symbolication files and recomputes every location itself. It
checks the ELF debug section, build fingerprint, SHA-256, crash sequence and
dump length. Events from other builds remain visible but are **not** resolved
against the wrong ELF. Zephyr log lines are text and provide timestamp,
severity and module; only PC-bearing events/dumps get exact file/line.

For Zephyr 4.4 ARM Cortex-M, fatal codes such as `30` are mapped to
"Divide-by-zero UsageFault" using the Zephyr enum; the claimed crash trigger
in `meta.txt` is **not** treated as proof of the fault reason. Invalid or
outside-ELF PCs show the LR call site and raw address instead of a fabricated
line number. Issue keys group the same reason/function across builds.

## Limits, retention and security

ZIP upload limit: 20 MiB, ELF: 16 MiB, coredump: 64 KiB, events: 4096 records,
logs: 10,000 nonempty lines, individual event/log line: 4096 bytes. Archive members must
be flat allowlisted filenames; traversal, duplicate entries, build mismatch
and malformed logs are rejected. Persistent storage lives under
`DIAG_SERVICE_DATA`; back it up and define a retention policy yourself. The
service does not yet include SSO, encrypted-at-rest storage, hosted source
viewing, fleet alerting, OTA orchestration, automated production uploads,
RBAC, tenant isolation, or guaranteed delivery across power loss. HTTP Basic
and Bearer tokens must never traverse an unencrypted public connection.

The provided container runs as a non-root user; Compose drops capabilities,
uses a read-only root filesystem, and sets CPU/memory/PID limits. ELF and GDB
tools still parse attacker-controlled input, so an Internet-facing production
deployment should move analysis into a separate networkless sandbox and add
rate limits, monitoring, backups, and an immutable symbol-artifact registry.

The server can decode an uploaded ELF only if `DIAG_ADDR2LINE` (or the Zephyr
SDK toolchain) is installed. GDB's full backtrace additionally needs
`DIAG_GDB` and `ZEPHYR_BASE`. Data uploaded from the DK via unauthenticated
MCUmgr must be treated as untrusted—even when the gateway supplies a token.

## Tests

```sh
python3 -m pytest service/tests -q
python3 -m unittest discover -s host/tests -q
```

The API tests check auth, malicious ZIP names, missing/mismatched ELF, bad
dump size, issue grouping, replay/idempotency, XSS escaping, and a real DK
archive when one is available locally. Hardware proof transcripts are in
[`LAB.md`](https://github.com/fitnowhere/nrf5340-crash-ble-lab/blob/main/LAB.md).
