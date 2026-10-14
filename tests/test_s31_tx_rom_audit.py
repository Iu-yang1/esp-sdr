"""ROM evidence must identify S31 code and keep device reads inside mask ROM."""
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rom_audit", ROOT / "tools/s31_tx_rom_audit.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def fixture():
    ident = b"\x7fELF\x01\x01\x01" + bytes(9)
    header = struct.pack("<16sHHIIIIIHHHHHH", ident, 2, 243, 1,
                         audit.ROM_LOW, 0, 52, 0, 52, 0, 0, 40, 4, 0)
    text = bytearray(256)
    text[16:20] = b"\x82\x80\x82\x80"
    symbols = bytes(16) + struct.pack("<IIIBBH", 1, audit.ROM_LOW + 16, 4, 0x12, 0, 1)
    strings = b"\0testfunc\0"
    sections = [bytes(40),
                struct.pack("<10I", 0, 1, 6, audit.ROM_LOW, 212, 256, 0, 0, 4, 0),
                struct.pack("<10I", 0, 2, 0, 0, 468, 32, 3, 1, 4, 16),
                struct.pack("<10I", 0, 3, 0, 0, 500, len(strings), 0, 0, 1, 0)]
    return header + b"".join(sections) + text + symbols + strings


class RomEvidence(unittest.TestCase):
    def test_elf_code_and_function_symbol(self):
        elf = audit.RomElf(fixture())
        self.assertEqual(elf.symbols["testfunc"], (audit.ROM_LOW + 16, 4))
        self.assertEqual(elf.read(audit.ROM_LOW + 16, 4), b"\x82\x80\x82\x80")
        for address, size in [(audit.ROM_LOW - 4, 4), (audit.ROM_HIGH - 2, 4),
                              (audit.ROM_LOW, 0), (audit.ROM_LOW + 255, 2)]:
            with self.assertRaises(ValueError):
                elf.read(address, size)

    def test_reject_wrong_architecture_and_truncated_elf(self):
        data = bytearray(fixture())
        struct.pack_into("<H", data, 18, 94)  # Xtensa cannot serve as S31 evidence
        for invalid in [data, fixture()[:60], fixture()[:-2], b"not an ELF"]:
            with self.assertRaises(ValueError):
                audit.RomElf(invalid)

    def test_decode_actual_s31_trampoline_and_negative_jump(self):
        self.assertEqual(audit.jump_target(0x2F801310, struct.pack("<I", 0x40C2906F)),
                         0x2F82A71C)
        self.assertEqual(audit.jump_target(0x2F801310, struct.pack("<I", 0xFFDFF06F)),
                         0x2F80130C)
        with self.assertRaises(ValueError):
            audit.jump_target(0x2F801310, struct.pack("<I", 0x000000EF))

    def test_readback_page_plan_is_bounded_and_deduplicated(self):
        self.assertEqual(audit.readback_pages([(audit.ROM_LOW + 2, 4),
                                               (audit.ROM_LOW + 254, 4)]),
                         [audit.ROM_LOW, audit.ROM_LOW + 256])
        with self.assertRaises(ValueError):
            audit.readback_pages([(audit.ROM_LOW, 16385)])
        with self.assertRaises(ValueError):
            audit.readback_pages([(0x20100448, 4)])

    def test_comparison_rejects_truncation_and_bit_changes(self):
        data = bytes(range(256))
        self.assertTrue(audit.compare_page(data, data)["matches"])
        self.assertFalse(audit.compare_page(data, data[:-1])["matches"])
        self.assertFalse(audit.compare_page(data, bytes(256))["matches"])

    def test_linker_definitions_exclude_comments(self):
        self.assertEqual(audit.linker_entries(
            "//fake = 0x2f800000;\nphy_dac_rate_set = 0x2f801310;\n"),
            {"phy_dac_rate_set": 0x2F801310})
