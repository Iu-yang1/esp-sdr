#!/usr/bin/env python3
"""Offline symbol inventory for the pinned ESP32-S31 vendor PHY archives.

This does not program MMIO, execute firmware or infer a working DAC register
from an unrelated ESP32 family member. It inventories candidate symbols only.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

WORDS = re.compile(r"(?:dac|dactrig|tx|tone|iq|dump|pbus|pll|rf)", re.I)
STRONG = re.compile(r"(?:dactrig|dac.*(?:dump|play|test)|(?:tx|fedump).*(?:mem|iq)|(?:tx|iq).*dump)", re.I)
NM_LINE = re.compile(r"^(?P<prefix>.+?)\s+(?P<kind>[A-Za-z])\s+(?P<name>[A-Za-z_$][\w.$@]*)$")
TOOLS = ("riscv32-esp-elf-nm", "xtensa-esp32s31-elf-nm",
         "xtensa-esp-elf-nm", "llvm-nm", "nm")
OBJDUMP_TOOLS = ("riscv32-esp-elf-objdump", "xtensa-esp32s31-elf-objdump",
                 "xtensa-esp-elf-objdump", "llvm-objdump", "objdump")
FOCUS_SYMBOLS = (
    "dactrig", "fedump_rd_txmem", "fedump_wr_txmem", "phy_tx_tone",
    "phy_start_tx_tone_step_new", "phy_stop_tx_tone_new",
    "phy_set_tx_gain_mem_new", "phy_set_tx_cfr_mem", "rftest_open_clk",
    "tx_contin_en", "esp_tx_func", "set_txdc_dig",
)


def parse_nm_records(output):
    """Return symbol + archive/object provenance (not an RF/DAC claim)."""
    records = []
    for line in output.splitlines():
        match = NM_LINE.match(line.strip())
        if match and match["kind"].upper() not in {"U", "N"}:
            name = match["name"]
            if WORDS.search(name):
                prefix = match["prefix"]
                # GNU nm -A: path/libphy.a:object.o:address TYPE symbol
                source = prefix.rsplit(":", 1)[0] if ":" in prefix else prefix
                records.append((name, source))
    return records


def parse_nm_output(output):
    """Portable nm -A output: archive:object:address TYPE symbol."""
    return sorted({name for name, _ in parse_nm_records(output)})


def sdk_revision(sdk):
    p = subprocess.run(["git", "-C", str(sdk), "rev-parse", "HEAD"],
                       text=True, capture_output=True, check=False)
    return p.stdout.strip() if p.returncode == 0 else None


def relative_to_sdk(sdk, path):
    """Normalize nm provenance whether SDK paths are absolute or relative."""
    try:
        return Path(path).relative_to(sdk).as_posix()
    except ValueError:
        return str(path)


def normalize_symbol_sources(sdk, sources):
    return {symbol: {relative_to_sdk(sdk, source) for source in paths}
            for symbol, paths in sources.items()}


def extract_function_disassembly(output, symbol):
    """Keep only one function body from GNU objdump's archive output."""
    lines = output.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if re.match(r"^[0-9a-fA-F]+ <" + re.escape(symbol) + r">:$", line)), None)
    if start is None:
        return None
    body = [lines[start]]
    for line in lines[start + 1:]:
        if line.startswith("Disassembly of section ") or re.match(r"^\S+\.o:\s+file format ", line):
            break
        body.append(line)
    return "\n".join(body).rstrip()


def parse_objdump_references(output, symbols):
    """Map archive relocation references to their object and source section."""
    references = {symbol: [] for symbol in symbols}
    obj = "unknown.o"
    section = None
    for line in output.splitlines():
        match = re.match(r"^(?P<obj>[^\s:]+\.o):\s+file format ", line)
        if match:
            obj = match["obj"]
            section = None
            continue
        match = re.match(r"^RELOCATION RECORDS FOR \[(?P<section>[^]]+)\]:$", line)
        if match:
            section = match["section"]
            continue
        match = re.match(r"^\s*[0-9a-fA-F]+\s+\S+\s+(\S+)", line)
        if match and section:
            target = match[1].split("+")[0]
            if target in references:
                references[target].append(obj + section)
    return {symbol: sorted(set(callers)) for symbol, callers in references.items()}


def focus_disassembly(sdk, archives, symbols, sources, objdump):
    """Disassemble a bounded set of TX-related symbols and retain provenance."""
    result = {}
    available = set(symbols)
    for symbol in FOCUS_SYMBOLS:
        if symbol not in available:
            continue
        archive_name = next((source.rsplit(":", 1)[0]
                             for source in sources.get(symbol, [])), None)
        archive = sdk / archive_name if archive_name else None
        if archive not in archives:
            archive = None
        if archive is None:
            result[symbol] = {"error": "symbol source archive not found"}
            continue
        p = subprocess.run([objdump, "-drwC", "--disassemble=" + symbol, str(archive)],
                           capture_output=True, text=True, check=False)
        body = extract_function_disassembly(p.stdout, symbol)
        result[symbol] = {
            "archive": str(archive.relative_to(sdk)),
            "disassembly": body,
            "error": p.stderr[-800:] if p.returncode else None,
        }
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sdk", type=Path, required=True, help="Pinned ESP-IDF checkout")
    ap.add_argument("--profile", choices=("esp32s31", "esp32s31-stream"),
                    default="esp32s31-stream")
    ap.add_argument("--nm", help="Path to nm from the ESP-IDF toolchain")
    ap.add_argument("--objdump", help="Path to target objdump; defaults to one on PATH")
    ap.add_argument("--output", type=Path, help="Write JSON report here")
    ap.add_argument("--strict-sdk", action="store_true",
                    help="Reject a revision other than firmware-targets.json")
    args = ap.parse_args(argv)
    catalog = json.loads((Path(__file__).resolve().parents[1] /
                          "firmware-targets.json").read_text(encoding="utf-8"))
    expected = next(p["idf_ref"] for p in catalog["profiles"]
                    if p["id"] == args.profile)
    actual = sdk_revision(args.sdk)
    if args.strict_sdk and actual != expected:
        ap.error("ESP-IDF commit mismatch: expected %s, got %s" % (expected, actual))
    if actual != expected:
        print("WARNING: ESP-IDF revision differs from pinned profile", file=sys.stderr)
    archives = sorted(p for p in args.sdk.rglob("*.a")
                      if "esp_phy" in p.parts and "esp32s31" in str(p).lower())
    if not archives:
        ap.error("No ESP32-S31 PHY archives found; install/build the pinned SDK first")
    tool = args.nm or next((p for p in TOOLS if shutil.which(p)), None)
    if not tool:
        ap.error("No nm tool available; pass --nm PATH")
    found = set()
    sources = {}
    errors = []
    for archive in archives:
        cmd = [tool, "-A", "--defined-only", str(archive)]
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if p.returncode:
            errors.append({"file": str(archive.relative_to(args.sdk)),
                           "error": p.stderr[-800:]})
            continue
        for name, source in parse_nm_records(p.stdout):
            found.add(name)
            sources.setdefault(name, set()).add(source)
    symbols = sorted(found)
    sources = normalize_symbol_sources(args.sdk, sources)
    objdump = args.objdump or next((p for p in OBJDUMP_TOOLS if shutil.which(p)), None)
    disassembly = focus_disassembly(args.sdk, archives, symbols, sources, objdump) if objdump else {}
    references = {}
    if objdump:
        for archive in archives:
            p = subprocess.run([objdump, "-r", str(archive)], capture_output=True,
                               text=True, check=False)
            if p.returncode == 0:
                for symbol, callers in parse_objdump_references(p.stdout, FOCUS_SYMBOLS).items():
                    archive_name = str(archive.relative_to(args.sdk))
                    references.setdefault(symbol, []).extend(
                        archive_name + ":" + caller for caller in callers)
        references = {symbol: sorted(set(callers)) for symbol, callers in references.items()}
    report = {
        "profile": args.profile,
        "expected_idf_commit": expected,
        "actual_idf_commit": actual,
        "sdk_matches_profile": actual == expected,
        "examined_archives": [str(p.relative_to(args.sdk)) for p in archives],
        "candidate_symbols": [s for s in symbols if STRONG.search(s)],
        "symbol_locations": {s: sorted(sources[s]) for s in symbols},
        "other_relevant_symbols": [s for s in symbols if not STRONG.search(s)],
        "objdump_tool": objdump,
        "focused_disassembly": disassembly,
        "focused_references": references,
        "errors": errors,
        "tx_dac_verified": False,
        "note": "Symbol presence is not evidence of playable DAC or transmitted RF."
    }
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    if errors or not symbols:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
