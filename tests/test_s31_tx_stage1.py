"""Stage-one S31 TX is a read-only probe, not an RF transmitter."""
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def import_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class S31TxProbe(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "Host C compiler unavailable")
    def test_no_tx_mmio_writes_or_mode_changes(self):
        header = ROOT / "main/targets/esp32s31/burst/s31_tx_probe.h"
        text = header.read_text()
        self.assertNotIn("REG_WRITE(", text)
        self.assertNotIn("REG_SET_BIT(", text)
        self.assertNotIn("REG_CLR_BIT(", text)
        self.assertNotIn("phy_pbus_xpd_tx_on(", text)
        c_code = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#define DUMP_CTRL 0x20109004u
#define DUMP_MODE 0x20109008u
#define DUMP_MAC 0x2010900cu
#define RX_GAIN 0x2010702cu
static unsigned reads, writes;
static char message[256];
static uint32_t fake_read(unsigned addr) { reads++; return addr; }
#define REG_READ(addr) fake_read(addr)
#define REG_WRITE(addr, value) (writes++)
static void reply(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(message, sizeof message, fmt, ap);
    va_end(ap);
}
#include "s31_tx_probe.h"
int main(void) {
    assert(!s31_tx_probe_command("CAPS"));
    assert(reads == 0 && writes == 0);
    assert(s31_tx_probe_command("TXCAPS?"));
    assert(strstr(message, "burst=0") && strstr(message, "rf=disabled"));
    assert(reads == 0);
    assert(s31_tx_probe_command("TXPROBE?"));
    assert(reads == 4);
    assert(strstr(message, "rx_dump_ctrl=20109004"));
    assert(strstr(message, "rx_gain=2010702c"));
    assert(s31_tx_probe_command("TXPATH?"));
    assert(reads == 11 && writes == 0);
    assert(strstr(message, "dac_rate=20100448"));
    assert(strstr(message, "tx_clock=20100890"));
    assert(strstr(message, "tone_ctrl=2010040c"));
    assert(strstr(message, "dac_scale=20100c04"));
    assert(strstr(message, "tx_filter=20107440"));
    assert(s31_tx_probe_command("TXSTART"));
    assert(strstr(message, "ERR tx_unverified"));
    assert(writes == 0 && reads == 11);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "test.c"
            source.write_text(c_code)
            binary = Path(temp) / "test"
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(header.parent), str(source), "-o", str(binary)],
                           check=True)
            subprocess.run([str(binary)], check=True)

    def test_probe_is_reachable_but_not_advertised_as_tx(self):
        source = (ROOT / "main/targets/esp32s31/burst/receiver.c").read_text()
        self.assertIn('#include "s31_tx_probe.h"', source)
        self.assertIn("s31_tx_probe_command(line)", source)
        parser = source[source.index("static void command("):source.index("void app_main(")]
        self.assertNotIn("s31_tx_probe_command(", parser)
        app = source[source.index("void app_main("):]
        self.assertIn("!s31_tx_probe_command(line)", app)
        caps = next(line for line in source.splitlines()
                    if 'else if (!strcmp(line, "CAPS"))' in line)
        self.assertIn("TXPROBE", caps)
        self.assertNotIn(" TX ", caps)


class S31OfflineTools(unittest.TestCase):
    def test_nm_candidate_inventory(self):
        mod = import_file("phy_audit", ROOT / "tools/s31_tx_phy_audit.py")
        output = ("archive.a:foo.o:00000000 T dactrig\n"
                  "archive.a:foo.o:00000100 T phy_pbus_xpd_tx_on\n"
                  "archive.a:foo.o:         U tx_missing\n"
                  "archive.a:foo.o:00000200 T receive_only\n")
        self.assertEqual(mod.parse_nm_output(output),
                         ["dactrig", "phy_pbus_xpd_tx_on"])
        self.assertTrue(mod.STRONG.search("dactrig"))
        self.assertFalse(mod.STRONG.search("phy_set_txclk_en"))

    def test_extract_focused_objdump_body(self):
        mod = import_file("phy_audit", ROOT / "tools/s31_tx_phy_audit.py")
        self.assertEqual(mod.relative_to_sdk(Path(".esp-idf"),
                                             ".esp-idf/components/esp_phy/lib.a"),
                         "components/esp_phy/lib.a")
        self.assertEqual(mod.relative_to_sdk(Path(".esp-idf"),
                                             "components/esp_phy/lib.a"),
                         "components/esp_phy/lib.a")
        self.assertEqual(mod.normalize_symbol_sources(
            Path(".esp-idf"),
            {"dactrig": {".esp-idf/components/esp_phy/lib.a:mac_common.o"}}),
            {"dactrig": {"components/esp_phy/lib.a:mac_common.o"}})
        output = ("archive.a:foo.o: file format elf32-littleriscv\n"
                  "Disassembly of section .text.dactrig:\n"
                  "00000000 <dactrig>:\n"
                  "   0: 2f0608b7 lui a7,0x2f060\n"
                  "   4: 00a82023 sw a0,0(a6)\n"
                  "Disassembly of section .text.other:\n"
                  "00000000 <other>:\n")
        body = mod.extract_function_disassembly(output, "dactrig")
        self.assertIn("<dactrig>", body)
        self.assertIn("lui a7,0x2f060", body)
        self.assertNotIn("<other>", body)
        self.assertIsNone(mod.extract_function_disassembly(output, "missing"))

    def test_parse_archive_references(self):
        mod = import_file("phy_audit", ROOT / "tools/s31_tx_phy_audit.py")
        output = ("mac_common.o: file format elf32-littleriscv\n"
                  "RELOCATION RECORDS FOR [.text.caller]:\n"
                  "00000004 R_RISCV_CALL dactrig\n"
                  "phy_test.o: file format elf32-littleriscv\n"
                  "RELOCATION RECORDS FOR [.text.other]:\n"
                  "00000008 R_RISCV_CALL unrelated\n")
        self.assertEqual(mod.parse_objdump_references(output, ["dactrig"]),
                         {"dactrig": ["mac_common.o.text.caller"]})

    def test_cs16_vectors(self):
        mod = import_file("tx_vectors", ROOT / "tools/s31_tx_iq_vectors.py")
        data = mod.make_tone(1000, 250, 4, 0.5)
        points = list(struct.iter_unpack("<hh", data))
        self.assertEqual(len(data), 16)
        self.assertEqual(points[0], (16384, 0))
        self.assertEqual(points[1], (0, 16384))
        self.assertEqual(points[2], (-16384, 0))
        self.assertEqual(points[3], (0, -16384))
        negative = mod.make_tone(1000, -250, 4, 0.5)
        self.assertLess(struct.unpack_from("<h", negative, 6)[0], 0)
        for bad in [(-1, 1, 4, 0.2), (1000, 500, 4, 0.2),
                    (1000, 1, 0, 0.2), (1000, 1, 4, 1.1)]:
            with self.assertRaises(ValueError):
                mod.make_tone(*bad)
