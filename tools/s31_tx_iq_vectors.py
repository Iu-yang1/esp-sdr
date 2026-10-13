#!/usr/bin/env python3
"""Generate host-side CS16 complex test vectors; no radio or TX commands.

CS16 (interleaved little-endian signed I,Q) is a transport-neutral proposal.
It is NOT S31's unverified on-chip DAC word format.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct


def make_tone(rate_hz, offset_hz, count, amplitude):
    if not 0 < rate_hz <= 80_000_000:
        raise ValueError("invalid sample rate")
    if not -rate_hz / 2 < offset_hz < rate_hz / 2:
        raise ValueError("tone must be strictly inside Nyquist interval")
    if not 1 <= count <= 262144:
        raise ValueError("sample count must be between 1 and 262144")
    if not 0 < amplitude <= 1 or not math.isfinite(amplitude):
        raise ValueError("amplitude must be in (0,1]")
    data = bytearray()
    phase_step = 2 * math.pi * offset_hz / rate_hz
    for n in range(count):
        phase = math.remainder(n * phase_step, 2 * math.pi)
        i = round(32767 * amplitude * math.cos(phase))
        q = round(32767 * amplitude * math.sin(phase))
        data.extend(struct.pack("<hh", i, q))
    return bytes(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("output", type=Path, help="Output .cs16le file")
    ap.add_argument("--rate", type=int, default=250000)
    ap.add_argument("--offset", type=int, default=10000)
    ap.add_argument("--samples", type=int, default=4096)
    ap.add_argument("--amplitude", type=float, default=0.2)
    args = ap.parse_args()
    data = make_tone(args.rate, args.offset, args.samples, args.amplitude)
    args.output.write_bytes(data)
    metadata = {"sample_format": "CS16LE", "iq_order": "IQ",
                "rate_hz": args.rate, "tone_offset_hz": args.offset,
                "samples": args.samples, "amplitude": args.amplitude,
                "sha256": hashlib.sha256(data).hexdigest(),
                "tx_hardware_validated": False}
    args.output.with_suffix(args.output.suffix + ".json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print("Wrote %d complex samples (%d bytes)" % (args.samples, len(data)))


if __name__ == "__main__":
    main()
