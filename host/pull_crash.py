#!/usr/bin/env python3
"""nRF5340 crash lab: BLE scan/control, SMP download and offline symbolication."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

SERVICE = "7a4e0001-7b2d-4c19-9a71-1db95c873510"
COMMAND = "7a4e0002-7b2d-4c19-9a71-1db95c873510"
STATUS = "7a4e0003-7b2d-4c19-9a71-1db95c873510"
NAMES = {"null": 0, "div0": 1, "assert": 2, "fnptr": 3, "stack": 4}
FILES = ("meta.txt", "recent.log", "crash.bin")
DIAGNOSTIC_FILES = ("events.ndjson", "recent.log")
SAMPLES = {"recoverable": 0x20, "ble_failure": 0x21,
           "storage_failure": 0x22, "ota_rejected": 0x23}
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ELF = ROOT / "build" / "nrf5340-crash-ble-lab" / "zephyr" / "zephyr.elf"
UPLOAD_FILES = {"manifest.json", "zephyr.elf", "crash.bin", "meta.txt",
                 "events.ndjson", "recent.log"}
MAX_DEVICE_FILE = 1024 * 1024
MAX_CRASH_FILE = 65536
MAX_EVENT_LINES = 4096
MAX_EVENT_LINE = 4096


def find_zephyr_tool(name: str) -> str:
    direct = shutil.which(name)
    if direct:
        return direct
    sdk = os.environ.get("ZEPHYR_SDK_INSTALL_DIR")
    directories = [Path(sdk)] if sdk else []
    directories.extend(Path("/opt/nordic/ncs/toolchains").glob("*/opt/zephyr-sdk"))
    for directory in directories:
        candidate = directory / "gnu" / "arm-zephyr-eabi" / "bin" / name
        if candidate.is_file():
            return str(candidate)
    return name


def default_addr2line() -> str:
    return find_zephyr_tool("arm-zephyr-eabi-addr2line")


def gdb_backtrace(dump: Path, elf: Path) -> str | None:
    script = Path(os.environ.get("ZEPHYR_BASE", "/opt/nordic/ncs/v3.4.0/zephyr")) / \
        "scripts/coredump/coredump_gdbserver.py"
    gdb = find_zephyr_tool("arm-zephyr-eabi-gdb")
    if not script.is_file() or not (shutil.which(gdb) or Path(gdb).is_file()):
        return None
    # GDB's pipe transport avoids races over a single-use TCP gdbserver.
    pipe = f"target remote | {sys.executable} {script} --pipe {elf.resolve()} {dump.resolve()}"
    try:
        result = subprocess.run([gdb, "-q", "-batch", str(elf), "-ex", pipe,
                                 "-ex", "bt 12"], capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"GDB unavailable ({exc}); using register-based addr2line", file=sys.stderr)
        return None
    if result.returncode == 0 and re.search(r"(?m)^#0 ", result.stdout):
        return result.stdout.strip()
    print(f"GDB backtrace failed ({result.stderr.strip()}); using registers", file=sys.stderr)
    return None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive_dir() -> Path:
    folder = ROOT / "host" / "out" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if folder.exists():
        folder = folder.with_name(folder.name + "-" + datetime.now(timezone.utc).strftime("%f"))
    folder.mkdir(parents=True)
    return folder


def record_host_failure(folder: Path, device, status: dict, exc: Exception):
    (folder / "host_failure.json").write_text(json.dumps({
        "device": device.address, "status_before_transfer": status,
        "error_type": type(exc).__name__, "error": str(exc),
        "time_utc": datetime.now(timezone.utc).isoformat(),
    }, indent=2) + "\n")


def upload_archive(folder: Path, server_url: str, token: str) -> dict:
    """Upload only raw evidence. Server independently re-verifies ELF/dump."""
    target = urlsplit(server_url)
    if target.scheme not in {"http", "https"} or not target.netloc:
        raise ValueError("Server URL must be http(s)://host:port")
    if target.scheme == "http" and target.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Use HTTPS when uploading to a non-local server")
    if not token:
        raise ValueError("Server upload requires --api-token or DIAG_SERVICE_TOKEN")
    if not (folder / "manifest.json").is_file() or not (folder / "zephyr.elf").is_file():
        raise ValueError("Archive must include a manifest and unstripped ELF")
    bundle = BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(folder.iterdir()):
            if file.name in UPLOAD_FILES or re.fullmatch(r"zephyr\.\d{4}", file.name):
                archive.write(file, arcname=file.name)
    request = Request(server_url.rstrip("/") + "/api/v1/reports", bundle.getvalue(),
                      {"Content-Type": "application/zip",
                       "Authorization": f"Bearer {token}"}, method="POST")
    try:
        with urlopen(request, timeout=75) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Diagnostic server rejected archive ({exc.code}): "
                           f"{exc.read(500).decode(errors='replace')}") from exc
    except URLError as exc:
        raise RuntimeError(f"Diagnostic server unavailable: {exc}") from exc
    print(f"Uploaded report {result['id']} (issue={result.get('issue_key')})")
    return result


def validate_elf(elf: Path, meta: dict):
    from elftools.elf.elffile import ELFFile

    with elf.open("rb") as stream:
        image = ELFFile(stream)
        if image.get_section_by_name(".debug_info") is None:
            raise ValueError(f"Unstripped app-core ELF required: {elf}")
        if (image["e_machine"] != "EM_ARM" or image.elfclass != 32 or
                not image.little_endian or image["e_type"] != "ET_EXEC"):
            raise ValueError(f"Expected a 32-bit little-endian executable ARM ELF: {elf}")
        fingerprint = meta.get("fingerprint")
        if not fingerprint or not re.fullmatch(r"[0-9a-f]{20}", fingerprint):
            raise ValueError("Dump metadata has no valid build fingerprint")
        if fingerprint.encode("ascii") not in elf.read_bytes():
            raise ValueError(f"Wrong ELF for dump fingerprint {fingerprint}: {elf}")
    return sha256(elf)


def parse_dump(data: bytes) -> dict:
    """Parse only the fixed Zephyr v2 Cortex-M register header; no guessed offsets."""
    if len(data) < 17 or data[:2] != b"ZE":
        raise ValueError("Not a Zephyr raw binary coredump (ZE header missing)")
    version, target, pointer_bits, flags, reason = struct.unpack_from("<HHBBI", data, 2)
    if version != 2 or target != 3 or pointer_bits != 5:
        raise ValueError(f"Unsupported dump version/target/pointer bits: {version}/{target}/{pointer_bits}")
    if data[12:13] != b"A":
        raise ValueError("Missing Cortex-M register block")
    arch_version, length = struct.unpack_from("<HH", data, 13)
    if arch_version != 3 or length < 36 or len(data) < 17 + length:
        raise ValueError("Invalid Cortex-M register block")
    regs = struct.unpack_from("<9I", data, 17)
    return {"reason": reason, "flags": flags, "pc": regs[6], "lr": regs[5], "sp": regs[8]}


def addr2line(elf: Path, addr: int, tool: str) -> str:
    # LR is the return address after a BL/BLX and so must be reduced to its call site.
    result = subprocess.run(
        [tool, "-e", str(elf), "-f", "-C", "-i", f"0x{addr:x}"],
        capture_output=True, text=True, check=True, timeout=10,
    )
    return result.stdout.strip()


def addr2line_many(elf: Path, addresses: set[int], tool: str) -> dict[int, str]:
    ordered = sorted(addresses)
    if not ordered:
        return {}
    result = subprocess.run(
        [tool, "-e", str(elf), "-f", "-C", *(f"0x{address:x}" for address in ordered)],
        capture_output=True, text=True, check=True, timeout=15,
    )
    lines = result.stdout.splitlines()
    if len(lines) != len(ordered) * 2:
        raise ValueError("Unexpected batched addr2line output")
    return {address: "\n".join(lines[index * 2:index * 2 + 2])
            for index, address in enumerate(ordered)}


def symbolicate(dump: Path, elf: Path, addr2line_tool: str) -> dict:
    meta_path = dump.with_name("meta.txt")
    if not meta_path.exists():
        raise ValueError(f"Missing metadata next to dump: {meta_path}")
    meta = json.loads(meta_path.read_text())
    digest = validate_elf(elf, meta)
    raw = dump.read_bytes()
    if len(raw) != meta["dump_size"]:
        raise ValueError(f"Truncated or mismatched dump: {len(raw)} != {meta['dump_size']}")
    regs = parse_dump(raw)
    trace = gdb_backtrace(dump, elf)
    if trace:
        print("GDB backtrace (Zephyr coredump_gdbserver.py):\n" + trace)
    candidates = [
        ("PC", regs["pc"] & ~1),
        ("LR call site", (regs["lr"] & ~1) - 1),
    ]
    frames = [(label, addr, addr2line(elf, addr, addr2line_tool)) for label, addr in candidates]
    expected = meta.get("expected", "")
    symbol = "crash_" + {
        "null": "null_deref", "div0": "div_by_zero", "assert": "assert_fail",
        "fnptr": "bad_fn_ptr", "stack": "stack_smash",
    }.get(expected, "<unknown>")
    matching = [f for f in frames if symbol in f[2]]
    for label, addr, location in frames:
        print(f"{label} 0x{addr:08x}: {location}")
    gdb_match = next((line for line in (trace or "").splitlines()
                      if re.match(r"^#\d+\s", line) and symbol in line), None)
    if not matching and not gdb_match:
        print(f"WARNING: requested test marker {symbol} does not match the captured fault; "
              "preserving spontaneous crash evidence", file=sys.stderr)
    location = gdb_match or (matching[0][2] if matching else frames[0][2])
    print(f"VERIFIED ELF/dump at {location} (reason={regs['reason']}, ELF sha256={digest})")
    return {"registers": regs, "symbol": symbol, "frame": location,
            "elf_sha256": digest, "meta": meta, "gdb_backtrace": trace}


def decode_events(folder: Path, elf: Path, tool: str, fingerprint: str) -> list[dict]:
    """ELF-resolve only actual recorded call-site PCs for the same build."""
    from elftools.elf.elffile import ELFFile

    source = folder / "events.ndjson"
    if not source.is_file():
        return []
    validate_elf(elf, {"fingerprint": fingerprint})
    with elf.open("rb") as stream:
        image = ELFFile(stream)
        sections = [(s.name, s["sh_addr"], s["sh_addr"] + s["sh_size"])
                    for s in image.iter_sections() if s["sh_flags"] & 4]
    decoded = []
    addresses = set()
    for number, raw in enumerate(BytesIO(source.read_bytes()), 1):
        if number > MAX_EVENT_LINES:
            raise ValueError(f"Diagnostic event count exceeds {MAX_EVENT_LINES}")
        raw = raw.rstrip(b"\r\n")
        if not raw:
            continue
        if len(raw) > MAX_EVENT_LINE:
            raise ValueError(f"Diagnostic event line {number} too long")
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Corrupt diagnostic event line {number}: {exc}") from exc
        if event.get("fingerprint") != fingerprint:
            event["location"] = "different build (requires its own archived ELF)"
        else:
            addr = int(event["pc"], 16) - 1
            section = next((name for name, start, end in sections if start <= addr < end), None)
            if section is None:
                event["location"] = "PC outside executable ELF sections"
            else:
                event["section"] = section
                event["_address"] = addr
                addresses.add(addr)
        decoded.append(event)
    locations = addr2line_many(elf, addresses, tool)
    for event in decoded:
        if "_address" in event:
            event["location"] = locations[event.pop("_address")]
    for event in decoded:
        print(f"{event['category']} {event.get('level', 'unknown')} "
              f"code={event['code']} value={event['value']} boot={event['boot']} "
              f"t={event['uptime_ms']}ms section={event.get('section', 'n/a')}: "
              f"{event['location']}")
    (folder / "events_symbolicated.json").write_text(json.dumps(decoded, indent=2) + "\n")
    return decoded


async def download_optional(smp, folder: Path, filename: str):
    from smpclient.generics import error
    from smpclient.requests.file_management import FileStatus

    name = "/lfs/" + filename
    status = await smp.request(FileStatus(name=name), timeout_s=12)
    if error(status):
        if "FILE_NOT_FOUND" in str(status):
            return
        raise RuntimeError(f"Filesystem status failed for {name}: {status}")
    limit = MAX_CRASH_FILE if filename == "crash.bin" else MAX_DEVICE_FILE
    if status.len < 0 or status.len > limit:
        raise ValueError(f"Device file {name} exceeds {limit} bytes")
    payload = await smp.download_file(name, timeout_s=12)
    if len(payload) != status.len:
        raise ValueError(f"Device file {name} changed during transfer")
    (folder / filename).write_bytes(payload)
    print(f"{folder / filename}: {len(payload)} bytes")


async def download_diagnostics(smp, folder: Path, status: dict):
    for filename in (*DIAGNOSTIC_FILES, *status.get("log_files", [])):
        if filename.startswith("zephyr.") and (not filename[7:].isdigit() or "/" in filename):
            raise ValueError(f"Invalid log filename from device: {filename!r}")
        if filename not in FILES:
            await download_optional(smp, folder, filename)
        elif filename == "recent.log" and not (folder / filename).exists():
            await download_optional(smp, folder, filename)


async def scan(timeout: float, all_devices: bool = False):
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    devices = []
    for device, advertisement in found.values():
        uuids = [u.lower() for u in advertisement.service_uuids]
        if all_devices or advertisement.local_name == "crash-lab" or SERVICE in uuids:
            print(f"{device.address}  name={advertisement.local_name!r}  RSSI={advertisement.rssi}")
            devices.append(device)
    return devices


async def resolve_device(address: str | None, timeout: float):
    from bleak import BleakScanner

    if address:
        device = await BleakScanner.find_device_by_address(address, timeout=timeout)
        if device is None:
            raise RuntimeError(f"BLE device {address} not found")
        return device
    found = await scan(timeout)
    if len(found) != 1:
        raise RuntimeError(f"Expected one crash-lab device, found {len(found)}; use --address")
    return found[0]


async def read_status(device):
    from bleak import BleakClient

    async with BleakClient(device, timeout=20) as client:
        status = json.loads(bytes(await client.read_gatt_char(STATUS)))
    required = {"build": str, "fingerprint": str, "dump_pending": bool,
                "dump_size": int, "crash_seq": int, "log_files": list}
    if any(not isinstance(status.get(key), kind) for key, kind in required.items()):
        raise ValueError("Malformed diagnostic status schema")
    if (not re.fullmatch(r"[0-9a-f]{20}", status["fingerprint"]) or
            not 1 <= len(status["build"]) <= 128 or
            not 0 <= status["dump_size"] <= MAX_CRASH_FILE or
            len(status["log_files"]) > 8 or
            any(not isinstance(name, str) for name in status["log_files"])):
        raise ValueError("Invalid diagnostic status values")
    return status


async def command(device, value: int):
    from bleak import BleakClient

    async with BleakClient(device, timeout=20) as client:
        status = json.loads(bytes(await client.read_gatt_char(STATUS)))
        await client.write_gatt_char(COMMAND, bytes([value]), response=True)
    return status


async def pull(device, elf: Path, tool: str, skip_symbolicate: bool,
               server_url: str | None = None, token: str = ""):
    from smpclient import SMPClient
    from smpclient.transport.ble import SMPBLETransport

    # Bleak and SMP use separate connections, never concurrent on the single-connection DK.
    status = await read_status(device)
    if not status["dump_pending"]:
        raise RuntimeError(f"No crash dump pending: {status}")
    folder = archive_dir()
    try:
        async with SMPClient(SMPBLETransport(), device.address, timeout_s=12) as smp:
            for name in FILES:
                await download_optional(smp, folder, name)
            await download_diagnostics(smp, folder, status)
        if not all((folder / name).is_file() for name in ("meta.txt", "crash.bin")):
            raise ValueError("Device has no complete crash.bin/meta.txt pair")
        meta = json.loads((folder / "meta.txt").read_text())
        if status["fingerprint"] != meta["fingerprint"] or status["crash_seq"] != meta["crash_seq"]:
            raise ValueError("Dump changed or belongs to another firmware build; use its archived ELF")
        if status["dump_size"] != len((folder / "crash.bin").read_bytes()):
            raise ValueError("SMP transfer size does not match firmware status")
        digest = validate_elf(elf, meta)
        shutil.copy2(elf, folder / "zephyr.elf")
        (folder / "manifest.json").write_text(json.dumps({"device": device.address,
            "elf_sha256": digest, "status": status}, indent=2) + "\n")
        print(f"Archived matching unstripped app ELF: {folder / 'zephyr.elf'}")
        if server_url:
            upload_archive(folder, server_url, token)
        if not skip_symbolicate:
            result = symbolicate(folder / "crash.bin", folder / "zephyr.elf", tool)
            (folder / "symbolication.json").write_text(json.dumps(result, indent=2) + "\n")
            decode_events(folder, folder / "zephyr.elf", tool, meta["fingerprint"])
        return folder
    except Exception as exc:
        record_host_failure(folder, device, status, exc)
        raise


async def logs(device, elf: Path, tool: str, server_url: str | None = None,
               token: str = ""):
    from smpclient import SMPClient
    from smpclient.transport.ble import SMPBLETransport

    status = await read_status(device)
    digest = validate_elf(elf, {"fingerprint": status["fingerprint"]})
    folder = archive_dir()
    try:
        async with SMPClient(SMPBLETransport(), device.address, timeout_s=12) as smp:
            await download_diagnostics(smp, folder, status)
        shutil.copy2(elf, folder / "zephyr.elf")
        (folder / "manifest.json").write_text(json.dumps({"device": device.address,
            "elf_sha256": digest, "status": status}, indent=2) + "\n")
        if server_url:
            upload_archive(folder, server_url, token)
        decode_events(folder, folder / "zephyr.elf", tool, status["fingerprint"])
        print(f"Saved diagnostics to {folder}")
    except Exception as exc:
        record_host_failure(folder, device, status, exc)
        raise


async def main(args):
    if args.action == "symbolicate":
        elf = args.elf or args.dump.with_name("zephyr.elf")
        result = symbolicate(args.dump, elf, args.addr2line)
        args.dump.with_name("symbolication.json").write_text(json.dumps(result, indent=2) + "\n")
        decode_events(args.dump.parent, elf, args.addr2line, result["meta"]["fingerprint"])
        return
    if args.action == "decode-events":
        elf = args.elf or args.folder / "zephyr.elf"
        manifest = json.loads((args.folder / "manifest.json").read_text())
        decode_events(args.folder, elf, args.addr2line, manifest["status"]["fingerprint"])
        return
    if args.action == "upload":
        if not args.server_url:
            raise ValueError("Set --server-url or DIAG_SERVICE_URL before upload")
        upload_archive(args.folder, args.server_url, args.api_token)
        return
    if args.action == "scan":
        await scan(args.timeout, args.all)
        return
    device = await resolve_device(args.address, args.timeout)
    if args.action == "status":
        print(json.dumps(await read_status(device), indent=2))
    elif args.action == "crash":
        status = await command(device, NAMES[args.type])
        print(f"Requested {args.type}; preceding status: {status}. Wait for reboot, then pull.")
    elif args.action == "reboot":
        await command(device, 0x10)
        print("Requested clean reboot")
    elif args.action == "clear":
        await command(device, 0xFE)
        print("Requested deletion of archived dump on device")
    elif args.action == "pull":
        await pull(device, args.elf or DEFAULT_ELF, args.addr2line, args.skip_symbolicate,
                   args.server_url, args.api_token)
    elif args.action == "logs":
        await logs(device, args.elf or DEFAULT_ELF, args.addr2line,
                   args.server_url, args.api_token)
    elif args.action == "emit":
        await command(device, SAMPLES[args.type])
        print(f"Injected synthetic {args.type} diagnostic (not a real failure)")
    elif args.action == "ota-check":
        from smpclient import SMPClient
        from smpclient.generics import error
        from smpclient.requests.image_management import ImageStatesRead
        from smpclient.transport.ble import SMPBLETransport

        async with SMPClient(SMPBLETransport(), device.address, timeout_s=12) as smp:
            reply = await smp.request(ImageStatesRead(), timeout_s=12)
        print(f"MCUmgr OTA/image-group response: {reply}")
        if not error(reply):
            raise RuntimeError("Unexpected image management support (no MCUboot in this lab)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--address", help="BLE address or macOS CoreBluetooth UUID")
    p.add_argument("--timeout", type=float, default=8, help="scan timeout seconds")
    p.add_argument("--elf", type=Path, help="unstripped app-core zephyr.elf (not HCI IPC)")
    p.add_argument("--addr2line", default=default_addr2line())
    p.add_argument("--server-url", default=os.getenv("DIAG_SERVICE_URL"))
    p.add_argument("--api-token", default=os.getenv("DIAG_SERVICE_TOKEN", ""))
    actions = p.add_subparsers(dest="action", required=True)
    actions.add_parser("scan").add_argument("--all", action="store_true")
    actions.add_parser("status")
    crashes = actions.add_parser("crash")
    crashes.add_argument("type", choices=NAMES)
    actions.add_parser("reboot")
    actions.add_parser("clear")
    actions.add_parser("logs")
    actions.add_parser("ota-check")
    emit = actions.add_parser("emit")
    emit.add_argument("type", choices=SAMPLES)
    pull_parser = actions.add_parser("pull")
    pull_parser.add_argument("--skip-symbolicate", action="store_true")
    sym = actions.add_parser("symbolicate")
    sym.add_argument("dump", type=Path, help="host/out/<timestamp>/crash.bin")
    dec = actions.add_parser("decode-events")
    dec.add_argument("folder", type=Path, help="host/out/<timestamp> with manifest.json")
    up = actions.add_parser("upload")
    up.add_argument("folder", type=Path, help="host/out/<timestamp> containing raw evidence and ELF")
    args = p.parse_args()
    try:
        asyncio.run(main(args))
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        p.exit(1, f"Error: {exc}\n")
