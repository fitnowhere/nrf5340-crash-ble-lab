"""Server-side ELF verification, Zephyr Cortex-M dump and event analysis."""

from io import BytesIO
import hashlib
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess

from elftools.elf.elffile import ELFFile
from elftools.common.exceptions import ELFError

MAX_EVENTS = 4096
MAX_EVENT_LINE = 4096


def verify_elf(data: bytes, fingerprint: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{20}", fingerprint):
        raise ValueError("Invalid 20-character build fingerprint")
    try:
        image = ELFFile(BytesIO(data))
        if image.get_section_by_name(".debug_info") is None:
            raise ValueError("Unstripped application ELF (.debug_info) is required")
        if image["e_machine"] != "EM_ARM":
            raise ValueError("Expected an ARM application ELF, not a net-core/host ELF")
    except (OSError, struct.error, ValueError, ELFError) as exc:
        raise ValueError(f"Invalid application ELF: {exc}") from exc
    if fingerprint.encode("ascii") not in data:
        raise ValueError("ELF does not contain the report's build fingerprint")
    return hashlib.sha256(data).hexdigest()


def elf_sections(data: bytes) -> list[tuple[str, int, int]]:
    image = ELFFile(BytesIO(data))
    return [(section.name, section["sh_addr"], section["sh_addr"] + section["sh_size"])
            for section in image.iter_sections() if section["sh_flags"] & 4]


def parse_dump(data: bytes) -> dict:
    if len(data) < 17 or data[:2] != b"ZE":
        raise ValueError("Not a Zephyr binary coredump")
    version, target, ptr_bits, flags, reason = struct.unpack_from("<HHBBI", data, 2)
    if (version, target, ptr_bits) != (2, 3, 5):
        raise ValueError("Unsupported Zephyr Cortex-M dump version/architecture")
    if data[12:13] != b"A":
        raise ValueError("Missing Cortex-M register block")
    arch_version, arch_len = struct.unpack_from("<HH", data, 13)
    if arch_version != 3 or arch_len < 36 or 17 + arch_len > len(data):
        raise ValueError("Truncated Cortex-M register block")
    regs = struct.unpack_from("<9I", data, 17)
    return {"reason_code": reason, "flags": flags, "pc": regs[6],
            "lr": regs[5], "sp": regs[8]}


def locate_tool(name: str) -> str | None:
    override = os.getenv("DIAG_ADDR2LINE" if "addr2line" in name else "DIAG_GDB")
    if override:
        return shutil.which(override) or (override if Path(override).is_file() else None)
    direct = shutil.which(name)
    if direct:
        return direct
    sdk = os.getenv("ZEPHYR_SDK_INSTALL_DIR")
    candidates = [Path(sdk)] if sdk else []
    candidates += list(Path("/opt/nordic/ncs/toolchains").glob("*/opt/zephyr-sdk"))
    for root in candidates:
        tool = root / "gnu" / "arm-zephyr-eabi" / "bin" / name
        if tool.is_file():
            return str(tool)
    return None


def address_location(elf: Path, pc: int, sections: list[tuple[str, int, int]],
                     *, return_address: bool = False,
                     resolved: dict[int, tuple[str | None, str | None, int | None]] | None = None) -> dict:
    addr = (pc & ~1) - (1 if return_address else 0)
    section = next((name for name, start, end in sections if start <= addr < end), None)
    result = {"address": f"0x{pc:08x}", "section": section,
              "function": None, "file": None, "line": None}
    if section is None:
        return result
    if resolved is not None and addr in resolved:
        result["function"], result["file"], result["line"] = resolved[addr]
        return result
    tool = locate_tool("arm-zephyr-eabi-addr2line")
    if not tool:
        result["error"] = "arm-zephyr-eabi-addr2line unavailable"
        return result
    try:
        process = subprocess.run([tool, "-f", "-C", "-i", "-e", str(elf), f"0x{addr:x}"],
                                 capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        result["error"] = str(exc)
        return result
    lines = process.stdout.splitlines()
    if len(lines) >= 2:
        result["function"] = lines[0] if lines[0] != "??" else None
        location = lines[1]
        if ":" in location and not location.startswith("??"):
            filename, line = location.rsplit(":", 1)
            result["file"] = filename
            match = re.match(r"(\d+)", line)
            result["line"] = int(match.group(1)) if match else None
    return result


def resolve_addresses(elf: Path, addresses: set[int]) -> dict[int, tuple[str | None, str | None, int | None]]:
    """Resolve unique addresses with one bounded addr2line process."""
    ordered = sorted(addresses)
    if not ordered:
        return {}
    tool = locate_tool("arm-zephyr-eabi-addr2line")
    if not tool:
        return {}
    try:
        process = subprocess.run(
            [tool, "-f", "-C", "-e", str(elf), *(f"0x{address:x}" for address in ordered)],
            capture_output=True, text=True, timeout=15, check=True,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {}
    lines = process.stdout.splitlines()
    if len(lines) != len(ordered) * 2:
        return {}
    result = {}
    for index, address in enumerate(ordered):
        function = lines[index * 2]
        location = lines[index * 2 + 1]
        filename = None
        line_number = None
        if ":" in location and not location.startswith("??"):
            filename, line = location.rsplit(":", 1)
            match = re.match(r"(\d+)", line)
            line_number = int(match.group(1)) if match else None
        result[address] = (function if function != "??" else None, filename, line_number)
    return result


def gdb_trace(elf: Path, dump: Path) -> str | None:
    base = os.getenv("ZEPHYR_BASE")
    tool = locate_tool("arm-zephyr-eabi-gdb")
    if not base or not tool:
        return None
    script = Path(base) / "scripts/coredump/coredump_gdbserver.py"
    if not script.is_file():
        return None
    import sys
    pipe = f"target remote | {sys.executable} {script} --pipe {elf.resolve()} {dump.resolve()}"
    try:
        result = subprocess.run([tool, "-q", "-batch", str(elf), "-ex", pipe,
                                 "-ex", "bt 16"], capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 and "#0 " in result.stdout else None


# Zephyr v4.4 ARM Cortex-M enum k_fatal_error_reason_arch (arch/arm/arch.h).
# Never infer a named fault from a requested test trigger in metadata.
REASONS = {0: "CPU exception", 1: "spurious interrupt", 2: "stack check failure",
           3: "kernel oops", 4: "kernel panic", 16: "Memory fault",
           17: "Memory stacking fault", 18: "Memory unstacking fault",
           19: "Memory data-access fault", 20: "Memory instruction-access fault",
           21: "Memory FP lazy-state fault", 22: "BusFault",
           23: "BusFault during stacking", 24: "BusFault during unstacking",
           25: "Precise data BusFault", 26: "Imprecise data BusFault",
           27: "Instruction BusFault", 28: "Bus FP lazy-state fault",
           29: "UsageFault", 30: "Divide-by-zero UsageFault",
           31: "Unaligned-access UsageFault", 32: "Stack-overflow UsageFault",
           33: "Missing coprocessor UsageFault", 34: "Invalid exception return",
           35: "Invalid EPSR", 36: "Undefined-instruction UsageFault"}


def analyze_crash(data: bytes, elf: Path, sections: list[tuple[str, int, int]],
                  dump_path: Path | None = None) -> dict:
    regs = parse_dump(data)
    pc = address_location(elf, regs["pc"], sections)
    lr = address_location(elf, regs["lr"], sections, return_address=True)
    reason = REASONS.get(regs["reason_code"],
                         f"architecture-specific fatal code {regs['reason_code']}")
    if pc["section"] is None:
        explanation = f"PC {pc['address']} outside executable ELF; caller: {lr['function'] or 'unknown'}"
    elif pc["function"] in {"assert_post_action", "z_fatal_error", "k_panic"} and lr["function"]:
        explanation = f"{reason} at {lr['function']} (via {pc['function']})"
    else:
        explanation = f"{reason} at {pc['function'] or pc['address']}"
    trace = gdb_trace(elf, dump_path) if dump_path else None
    issue_func = (lr["function"] if pc["section"] is None or
                  pc["function"] in {"assert_post_action", "z_fatal_error", "k_panic"}
                  else pc["function"]) or pc["address"]
    return {"registers": regs, "reason": reason, "explanation": explanation,
            "pc": pc, "lr_call_site": lr, "gdb_backtrace": trace,
            "issue_function": issue_func}


def analyze_events(data: bytes, elf: Path, sections: list[tuple[str, int, int]],
                    fingerprint: str) -> list[dict]:
    events = []
    matching_addresses = set()
    for number, raw in enumerate(BytesIO(data), 1):
        if number > MAX_EVENTS:
            raise ValueError(f"Diagnostic event count exceeds {MAX_EVENTS}")
        raw = raw.rstrip(b"\r\n")
        if not raw:
            continue
        if len(raw) > MAX_EVENT_LINE:
            raise ValueError(f"Diagnostic event line {number} too long")
        import json
        try:
            event = json.loads(raw)
            if event.get("v") != 1 or not isinstance(event.get("category"), str):
                raise ValueError("Unsupported diagnostic event schema")
            if not isinstance(event.get("code"), int) or not isinstance(event.get("value"), int):
                raise ValueError("Missing numeric event code/value")
            if event.get("fingerprint") == fingerprint:
                pc = int(event["pc"], 16)
                address = (pc & ~1) - 1
                matching_addresses.add(address)
                event["_address"] = address
                event["mismatched_build"] = False
            else:
                event["location"] = None
                event["mismatched_build"] = True
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"Invalid diagnostic event line {number}: {exc}") from exc
        events.append(event)
    resolved = resolve_addresses(elf, matching_addresses)
    for event in events:
        if "_address" in event:
            pc = int(event["pc"], 16)
            event["location"] = address_location(
                elf, pc, sections, return_address=True, resolved=resolved)
            del event["_address"]
    return events
