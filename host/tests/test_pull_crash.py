"""Offline parser/error-path tests; hardware transcripts are in LAB.md."""

import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
