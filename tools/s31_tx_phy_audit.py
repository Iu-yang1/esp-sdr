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


def parse_nm_output(output):
    """Portable nm -A output: archive:object:address TYPE symbol."""
    symbols = set()
    for line in output.splitlines():
        match = NM_LINE.match(line.strip())
        if match and match["kind"].upper() not in {"U", "N"}:
            name = match["name"]
            if WORDS.search(name):
                symbols.add(name)
    return sorted(symbols)


def sdk_revision(sdk):
    p = subprocess.run(["git", "-C", str(sdk), "rev-parse", "HEAD"],
                       text=True, capture_output=True, check=False)
    return p.stdout.strip() if p.returncode == 0 else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sdk", type=Path, required=True, help="Pinned ESP-IDF checkout")
    ap.add_argument("--profile", choices=("esp32s31", "esp32s31-stream"),
                    default="esp32s31-stream")
    ap.add_argument("--nm", help="Path to nm from the ESP-IDF toolchain")
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
    errors = []
    for archive in archives:
        cmd = [tool, "-A", "--defined-only", str(archive)]
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if p.returncode:
            errors.append({"file": str(archive.relative_to(args.sdk)),
                           "error": p.stderr[-800:]})
            continue
        found.update(parse_nm_output(p.stdout))
    symbols = sorted(found)
    report = {
        "profile": args.profile,
        "expected_idf_commit": expected,
        "actual_idf_commit": actual,
        "sdk_matches_profile": actual == expected,
        "examined_archives": [str(p.relative_to(args.sdk)) for p in archives],
        "candidate_symbols": [s for s in symbols if STRONG.search(s)],
        "other_relevant_symbols": [s for s in symbols if not STRONG.search(s)],
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
