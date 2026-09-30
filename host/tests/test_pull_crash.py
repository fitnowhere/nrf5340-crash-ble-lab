"""Offline parser/error-path tests; hardware transcripts are in LAB.md."""

import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pull_crash as lab


def synthetic_dump(pc=0x1234, lr=0x5679):
    header = struct.pack("<2sHHBBI", b"ZE", 2, 3, 5, 0, 4)
    regs = struct.pack("<9I", 0, 0, 0, 0, 0, lr, pc, 0, 0x20000100)
    return header + struct.pack("<cHH", b"A", 3, len(regs)) + regs


class DumpTests(unittest.TestCase):
    def test_cortex_m_registers(self):
        info = lab.parse_dump(synthetic_dump())
        self.assertEqual((info["pc"], info["lr"], info["sp"], info["reason"]),
                         (0x1234, 0x5679, 0x20000100, 4))

    def test_wrong_header(self):
        with self.assertRaisesRegex(ValueError, "ZE header"):
            lab.parse_dump(b"not a Zephyr coredump")

    def test_truncated_registers(self):
        with self.assertRaisesRegex(ValueError, "Invalid Cortex-M"):
            lab.parse_dump(synthetic_dump()[:-3])

    def test_wrong_target(self):
        data = bytearray(synthetic_dump())
        data[4:6] = struct.pack("<H", 1)
        with self.assertRaisesRegex(ValueError, "Unsupported dump"):
            lab.parse_dump(data)

    def test_wrong_elf_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            elf = Path(temp) / "empty.elf"
            elf.write_bytes(b"not an elf")
            with self.assertRaises(Exception):
                lab.validate_elf(elf, {"fingerprint": "01234567890123456789"})

    def test_truncated_dump_rejected_before_symbolication(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "crash.bin").write_bytes(synthetic_dump())
            (folder / "meta.txt").write_text(json.dumps({"dump_size": 123456}))
            with patch.object(lab, "validate_elf", return_value="test-hash"):
                with self.assertRaisesRegex(ValueError, "Truncated or mismatched"):
                    lab.symbolicate(folder / "crash.bin", folder / "missing.elf", "addr2line")

    def test_diagnostics_resolve_matching_call_site_only(self):
        from elftools.elf import elffile

        class TextSection:
            name = ".text"

            def __getitem__(self, key):
                return {"sh_addr": 0x1000, "sh_size": 0x100, "sh_flags": 4}[key]

        class FakeELF:
            def __init__(self, stream):
                pass

            def iter_sections(self):
                return [TextSection()]

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "zephyr.elf").write_bytes(b"fake")
            events = [{"fingerprint": "a" * 20, "category": "ble", "code": 11,
                       "value": 19, "boot": 2, "uptime_ms": 99, "pc": "0x00001009"},
                      {"fingerprint": "b" * 20, "category": "boot", "code": 1,
                       "value": 2, "boot": 1, "uptime_ms": 0, "pc": "0x00001009"}]
            (folder / "events.ndjson").write_text("\n".join(map(json.dumps, events)))
            with patch.object(lab, "validate_elf"), patch.object(elffile, "ELFFile", FakeELF), \
                  patch.object(lab, "addr2line_many",
                               return_value={0x1008: "disconnected\nsrc/dummy_ble.c:164"}) as resolve:
                result = lab.decode_events(folder, folder / "zephyr.elf", "tool", "a" * 20)
            resolve.assert_called_once_with(folder / "zephyr.elf", {0x1008}, "tool")
            self.assertEqual(result[0]["section"], ".text")
            self.assertIn("different build", result[1]["location"])

    def test_corrupted_event_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "zephyr.elf").write_bytes(b"fake")
            (folder / "events.ndjson").write_text("{broken\n")
            with patch.object(lab, "validate_elf"), \
                 patch("elftools.elf.elffile.ELFFile") as image:
                image.return_value.iter_sections.return_value = []
                with self.assertRaisesRegex(ValueError, "Corrupt diagnostic event line 1"):
                    lab.decode_events(folder, folder / "zephyr.elf", "tool", "a" * 20)

    def test_host_transfer_failure_keeps_partial_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "events.ndjson").write_text("partial")
            lab.record_host_failure(folder, SimpleNamespace(address="device-id"),
                                    {"dump_pending": True}, TimeoutError("BLE link lost"))
            saved = json.loads((folder / "host_failure.json").read_text())
            self.assertEqual(saved["error_type"], "TimeoutError")
            self.assertEqual(saved["error"], "BLE link lost")
            self.assertEqual((folder / "events.ndjson").read_text(), "partial")


if __name__ == "__main__":
    unittest.main()
