# Troubleshooting

## Device not found

Run `scan`, confirm the network-core `hci_ipc` image was flashed, and pass the
reported address with `--address`. macOS uses a CoreBluetooth UUID.

## Wrong ELF or fingerprint mismatch

Use the unstripped `zephyr.elf` from the exact flashed app build. A crash kept
across reflashing intentionally retains its fault-time fingerprint and will be
rejected against the new ELF.

## No source line

Install `arm-none-eabi-addr2line` or set `DIAG_ADDR2LINE`. A plain text log has
no caller address; only a PC-bearing event or coredump can map exactly.

## No GDB backtrace

Set `ZEPHYR_BASE` and provide compatible `arm-zephyr-eabi-gdb`. The service
still reports register-based PC/LR locations when GDB integration is absent.

## Upload rejected

Check the bearer token, `Content-Type: application/zip`, 20 MiB request limit,
flat allowlisted names, 16 MiB ELF limit, 64 KiB dump limit, and event/log line
limits. Non-loopback HTTP uploads are intentionally refused by the gateway.

## Docker unavailable

Start Docker Desktop, or run the loopback-only Python CLI described in
[Service deployment](SERVICE.md).
