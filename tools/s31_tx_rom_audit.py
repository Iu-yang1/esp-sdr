#!/usr/bin/env python3
"""Audit S31 TX control in the SDK ROM ELF; optionally compare mask ROM bytes.

The default is offline. --readback-port resets a board into the ROM loader
and reads bounded mask ROM pages only. It never calls PHY functions, writes
MMIO, fills SRAM, flashes firmware, or enables an RF transmitter.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

ROM_LOW, ROM_HIGH = 0x2F800000, 0x2F850000
FOCUS_SYMBOLS = (
    "phy_dac_rate_set", "phy_set_txclk_en", "phy_dac_scale_set",
    "phy_bbtx_outfilter", "phy_start_tx_tone_step", "phy_stop_tx_tone",
    "phy_pbus_xpd_tx_off", "phy_pbus_xpd_tx_on", "phy_force_txrx_off",
    "phy_open_fe_bb_clk",
)


def rom_range(address, size):
    if size <= 0 or address < ROM_LOW or address + size > ROM_HIGH:
        raise ValueError("Read is outside the S31 mask ROM window")


class RomElf:
    """Minimal ELF32 reader for file-backed ROM bytes and function symbols."""
    def __init__(self, data):
        self.data = data
        if len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01":
            raise ValueError("Expected a little-endian ELF32 file")
        header = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
        if header[2] != 243:
            raise ValueError("Expected a RISC-V ROM ELF")
        offset, entry_size, count = header[6], header[11], header[12]
        if entry_size != 40 or count == 0 or offset + count * 40 > len(data):
            raise ValueError("Invalid ELF section table")
        self.sections = [struct.unpack_from("<10I", data, offset + i * 40)
                         for i in range(count)]
        self.symbols = {}
        for section in self.sections:
            if section[1] != 2:  # SHT_SYMTAB
                continue
            if section[9] != 16 or section[5] % 16 or section[6] >= count:
                raise ValueError("Invalid ELF symbol table")
            strings = self.section_bytes(self.sections[section[6]])
            symbols = self.section_bytes(section)
            for at in range(0, len(symbols), 16):
                name, address, size, info, _, index = struct.unpack_from("<IIIBBH", symbols, at)
                if info & 15 != 2 or not index:  # Defined STT_FUNC only
                    continue
                if name >= len(strings) or b"\0" not in strings[name:]:
                    raise ValueError("Invalid ELF symbol name")
                name = strings[name:strings.index(b"\0", name)].decode("utf-8")
                self.symbols[name] = (address, size)

    def section_bytes(self, section):
        offset, size = section[4:6]
        if offset + size > len(self.data):
            raise ValueError("Truncated ELF section")
        return self.data[offset:offset + size]

    def read(self, address, size):
        rom_range(address, size)
        for section in self.sections:
            base, length = section[3], section[5]
            if section[1] == 1 and base <= address and address + size <= base + length:
                offset = address - base
                return self.section_bytes(section)[offset:offset + size]
        raise ValueError("ROM range has no file-backed ELF section")


def jump_target(address, data):
    word, = struct.unpack("<I", data)
    if word & 0xFFF != 0x06F:  # JAL x0, signed 21-bit displacement
        raise ValueError("ROM entry is not a JAL x0 trampoline")
    displacement = ((word >> 31) << 20 | ((word >> 21) & 0x3FF) << 1 |
                    ((word >> 20) & 1) << 11 | ((word >> 12) & 0xFF) << 12)
    if displacement & 0x100000:
        displacement -= 0x200000
    return address + displacement


def linker_entries(text):
    return {name: int(address, 16) for name, address in re.findall(
        r"^\s*(\w+)\s*=\s*(0x[0-9a-fA-F]+)\s*;", text, re.M)}


def readback_pages(regions):
    pages = set()
    for address, size in regions:
        rom_range(address, size)
        pages.update(range(address & ~255, (address + size + 255) & ~255, 256))
    if len(pages) * 256 > 16384:
        raise ValueError("ROM readback exceeds the 16 KiB research limit")
    return sorted(pages)


def compare_page(expected, observed):
    return {"expected_sha256": hashlib.sha256(expected).hexdigest(),
            "observed_sha256": hashlib.sha256(observed).hexdigest(),
            "expected_bytes": len(expected), "observed_bytes": len(observed),
            "matches": expected == observed}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sdk", type=Path, required=True)
    ap.add_argument("--profile", choices=("esp32s31", "esp32s31-stream"), required=True)
    ap.add_argument("--rom-elf", type=Path, required=True)
    ap.add_argument("--objdump", default=shutil.which("riscv32-esp-elf-objdump"))
    ap.add_argument("--strict-sdk", action="store_true")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--readback-port", help="Opt in to read-only board ROM comparison")
    ap.add_argument("--esptool-python", default=sys.executable)
    args = ap.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    expected = next(p["idf_ref"] for p in json.loads(
        (root / "firmware-targets.json").read_text())["profiles"] if p["id"] == args.profile)
    actual = subprocess.check_output(["git", "-C", str(args.sdk), "rev-parse", "HEAD"],
                                     text=True).strip()
    if args.strict_sdk and actual != expected:
        ap.error("ESP-IDF revision does not match the selected profile")
    if not args.objdump:
        ap.error("Pass --objdump from the S31 RISC-V toolchain")
    data = args.rom_elf.read_bytes()
    elf = RomElf(data)
    linker_path = Path("components/esp_rom/esp32s31/ld/esp32s31.rom.phy.ld")
    entries = linker_entries((args.sdk / linker_path).read_text())
    tools = json.loads((args.sdk / "tools/tools.json").read_text())["tools"]
    package = next(v for t in tools if t["name"] == "esp-rom-elfs"
                   for v in t["versions"] if v["status"] == "recommended")
    functions, errors, regions = {}, [], []
    for name in FOCUS_SYMBOLS:
        if name not in elf.symbols:
            errors.append("ROM ELF function missing: " + name)
            continue
        address, size = elf.symbols[name]
        if not 0 < size <= 4096:
            errors.append("Invalid function size: " + name)
            continue
        code = elf.read(address, size)
        regions.append((address, size))
        proc = subprocess.run([args.objdump, "-dw", "--disassemble=" + name,
                               "--start-address=" + hex(address),
                               "--stop-address=" + hex(address + size), str(args.rom_elf)],
                              text=True, capture_output=True, check=False)
        disassembly = "\n".join(line for line in proc.stdout.splitlines()
                                if re.match(r"^\s*[0-9a-fA-F]+(?::| <)", line))
        if proc.returncode or not disassembly:
            errors.append("Disassembly failed: " + name + " " + proc.stderr[-200:])
        entry = entries.get(name)
        target = jump_target(entry, elf.read(entry, 4)) if entry is not None else None
        if entry is not None:
            regions.append((entry, 4))
            if target != address:
                errors.append("Linker trampoline mismatch: " + name)
        functions[name] = {"entry_address": entry, "body_address": address,
                           "body_size": size, "body_sha256": hashlib.sha256(code).hexdigest(),
                           "entry_target": target, "disassembly": disassembly}
    report = {"profile": args.profile, "expected_idf_commit": expected,
              "actual_idf_commit": actual, "sdk_matches_profile": actual == expected,
              "rom_package_version": package["name"],
              "rom_package_expected_sha256": package["any"]["sha256"],
              "rom_elf_sha256": hashlib.sha256(data).hexdigest(),
              "rom_linker_source": linker_path.as_posix(), "functions": functions,
              "readback": [], "errors": errors, "tx_dac_verified": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.readback_port and not errors:
        directory = args.output.parent / (args.output.stem + "-readback")
        directory.mkdir(parents=True, exist_ok=True)
        for address in readback_pages(regions):
            path = directory / ("%08x.bin" % address)
            expected_page = elf.read(address, 256)
            command = [args.esptool_python, "-m", "esptool", "--chip", "esp32s31",
                       "--no-stub", "--port", args.readback_port, "--baud", "115200",
                       "dump-mem", hex(address), "256", str(path)]
            try:
                proc = subprocess.run(command, text=True, capture_output=True,
                                      check=False, timeout=45)
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append("ROM readback failed at %08x: %s" % (address, exc))
                break
            if proc.returncode:
                errors.append("ROM readback failed at %08x: %s" % (address, proc.stderr[-500:]))
                break
            page = compare_page(expected_page, path.read_bytes())
            report["readback"].append(dict(address=address, **page))
            if not page["matches"]:
                errors.append("Device/ELF mismatch at %08x" % address)
                break
        report["device_rom_matches"] = not errors and bool(report["readback"])
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("ROM audit: %d functions, %d device pages, %d errors" %
          (len(functions), len(report["readback"]), len(errors)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
