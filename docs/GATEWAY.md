# Gateway

Install with `.venv/bin/pip install -e '.[gateway]'`, then use
`python host/pull_crash.py --help`.

| Command | Purpose |
| --- | --- |
| `scan`, `status` | Discover a DK and read its current diagnostic status |
| `crash TYPE` | Trigger a lab fault (`null`, `div0`, `assert`, `fnptr`) |
| `pull` | Download a pending crash, events and logs; archive the ELF |
| `logs` | Collect events/logs without requiring a crash |
| `symbolicate PATH` | Re-analyze a saved dump with its archived ELF |
| `decode-events PATH` | Resolve saved event PCs |
| `upload PATH` | Upload an existing archive to the service |
| `clear`, `reboot`, `emit` | Destructive/synthetic bench operations |

Use `--address` when more than one matching device is visible. On macOS this
is normally a CoreBluetooth UUID rather than the radio MAC address.

Downloads are size-bounded and status is schema-validated. The gateway rejects
a crash whose fault-time fingerprint differs from current firmware status; use
the ELF archived for the faulting build. A stale `expected` test marker causes
a warning, not rejection of genuine spontaneous evidence.

Remote backend uploads require HTTPS. Plain HTTP is accepted only for loopback.
The device-to-gateway BLE link in the reference app is not authenticated.
