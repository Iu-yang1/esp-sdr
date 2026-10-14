# ESP32-S31 native I/Q TX research (fork-only)

**Status (2026-10-14): Stage 1 software implemented; S31 RF DAC TX unverified.**

This work is experimental and does not add TX to standard ESP-SDR or SoapyESPSDR.
Do not infer TX capability from successful RX streaming.

## Reference evidence

- [ESPARGOS/esp-sdr](https://github.com/ESPARGOS/esp-sdr):
  S31 RX dump, TCM SRAM, PHY tuning, PARLIO/GDMA and USB/Ethernet streaming.
- [jochenhammes/esp32-sdr-trx S3 IQ research](https://github.com/jochenhammes/esp32-sdr-trx/tree/research/iq-tx):
  S3 SRAM DAC playback at 40/80 MSa/s, and failure of single-bank in-place refilling.
- [cyber-murmel/esp-sdr-zephyr](https://github.com/cyber-murmel/esp-sdr-zephyr):
  S3 TX DAC, alternative dual-bank streaming, and SoapySDR TX.

None proves a working ESP32-S31 DAC TX address, word format, or RF mux. S3
register 0x60033d64 and SRAM 0x3fcd0000 **must not be reused on S31**.
The known S31 RX dump registers (e.g. 0x20109004) do not establish the
existence or register map of the TX DAC.

## Stage 1: non-radiating firmware probe — implemented

The ordinary S31 burst firmware has two new commands:

| Serial command | Effect |
| --- | --- |
| TXCAPS? | Reports DAC unverified, burst/stream/Soapy TX disabled |
| TXPROBE? | Passively reads existing RX DUMP_CTRL, DUMP_MODE, DUMP_MAC, RX_GAIN |
| Other TX-prefixed commands | ERR tx_unverified |

The snapshot is **not a DAC test**. It only records known RX PHY state without
mode changes, retuning or register writes. The CAPS list advertises TXPROBE,
not TX. No new RF transmitter is enabled.

### Offline PHY archive audit

Use the ESP-IDF commit pinned in firmware-targets.json for the profile;
the S31 burst and S31 streaming profiles currently pin different preview SDK
revisions. Install the matching toolchain and obtain its PHY archive first.

    python3 tools/s31_tx_phy_audit.py --sdk /path/to/esp-idf \
      --profile esp32s31-stream --strict-sdk \
      --output reports/s31-tx-symbols.json

Optionally pass --nm /path/to/target-nm and --objdump
/path/to/target-objdump. The tool finds defined symbols containing DAC, TX,
I/Q, dump, PBUS, tone or PLL terms. When objdump is available, it also records
focused function disassemblies and archive relocation references. This is
**candidate discovery only**, never a verified register map. If there is no
PHY archive or nm fails, treat that as a tool/environment error, not a failed
DAC test.

For each candidate, separately review the correctly pinned disassembly and
record archive/object/symbol, callers, possible MMIO, bit-width/endianness,
and independently corroborated evidence. The S31 source may differ from S3.

GitHub Actions now also runs a **non-blocking PHY symbol and disassembly audit**
after each successful S31 firmware build, using the matched preview SDK version, and
uploads separate research-s31-phy-esp32s31 and
research-s31-phy-esp32s31-stream JSON artifacts. The report records symbol
locations, focused disassembly, and references in the vendor archives and
explicitly sets tx_dac_verified=false. Because this is experimental analysis,
missing symbols or audit errors do not invalidate an otherwise working RX
firmware build. Check the audit step and its upload result; the build alone
does not prove the report exists.

### P0 evidence from pinned libraries (2026-10-14)

The two Actions reports for commit `b482766` matched their pinned ESP-IDF
revisions and each listed 286 relevant defined symbols with no `nm` errors:

| Profile | ESP-IDF commit | Main candidate locations |
| --- | --- | --- |
| S31 burst | `26dd2948925d44f41e3bb614fb044b7a0839a242` | `dactrig` in `librftest.a:mac_common.o`; `fedump_rd_txmem` / `fedump_wr_txmem` in `librftest.a:phy_test.o`; TX tone/gain functions in `libphy.a` |
| S31 streaming | `c712a0dde385d659a1470a136251980d31a70bc1` | Same candidate symbol locations |

The matched archives contain ELF32 RISC-V objects. Direct disassembly of both
library revisions establishes these limited facts:

- `fedump_rd_txmem` and `fedump_wr_txmem` are each two-byte functions whose
  only instruction is `ret`. They are stubs in these pinned archives and do
  not read or write TX memory.
- `dactrig` forms address `0x2f060000`, loops `0x20000` times and issues one
  32-bit `sw` at `base + loop_index` per iteration. The stores therefore
  overlap at byte increments. It then calls `ets_delay_us(10000)` and
  `phy_printf`. The relocation scan found no direct caller for `dactrig` in
  the four inspected PHY archives. This is evidence of CPU writes encoded in
  an otherwise unreferenced test helper; the address is not identified here
  as an S31 DAC SRAM bank. It does not demonstrate a DAC playback trigger or
  arbitrary I/Q format. Do not write there from firmware on this evidence.
- `phy_tx_tone` calls `phy_force_txon_mode` and
  `phy_start_tx_tone_step_new`; `phy_stop_tx_tone_new` clears related control
  bits. This is a tone-test path, not proof of user-supplied complex-sample
  playback.
- `phy_set_tx_gain_mem_new` and `phy_set_tx_cfr_mem` manipulate PHY gain/CFR
  state. Their names and MMIO accesses do not identify an arbitrary-I/Q data
  input or settle the DAC rate, SRAM ownership, or RF mux.

These are **binary-level findings**, not public register definitions or
hardware observations. No TX DAC source mux, playback-rate divider, bank
ownership/switch protocol, or safe TX/RX restoration sequence has yet been
identified from corroborated S31 source and silicon evidence. The report keeps
`tx_dac_verified=false`; no RF TX implementation is authorized by these
findings.

### Offline I/Q vectors

    python3 tools/s31_tx_iq_vectors.py /tmp/iq-tone.cs16le \
      --rate 250000 --offset 10000 --samples 4096 --amplitude 0.2

Writes transport-neutral CS16LE interleaved signed I,Q pairs and JSON
metadata, with no board access and no RF. It is deliberately **not** a claim
about S31's on-chip DAC format. Use a hardware-proven packing converter in
Stage 2.

    python3 -m unittest discover -s tests

## Stage 2: native single I/Q burst — not implemented

1. Discover S31-specific DAC SRAM source, ownership gate, clock, baseband
   word packing, trigger, TX mux and PHY gain through pinned SDK research.
2. Add an **opt-in separate research firmware profile**. Leave existing
   RX artifacts and SRAM linker guards unchanged.
3. Validate waveform length/checksum while RF is off. Copy into a proven
   idle TX bank. Then allow a bounded-duration playback in a shielded or
   conducted test setup, with watchdog, stop and RX restoration.
4. Verify positive and negative complex frequency offsets at RF, true
   playback duration/rate, LO feedthrough, IQ image, spur spectrum, power,
   harmonic content and two-tone IMD.
5. Preserve a fail-closed state after invalid commands, interruption or reset.

**Gate:** independent RF capture proves arbitrary S31 SRAM I/Q determines RF
output. A functioning RX dump or PLL-tuned FM/FSK is not equivalent.

## Stage 3: continuous dual-bank I/Q — blocked on Stage 2

DAC consumes bank A only while CPU/DMA fills bank B. Switch ownership at a
measured, deterministic boundary; never modify a bank that the DAC owns.
The S3 experiment showed that CPU/GDMA in-place writing to an active bank
can corrupt samples. If S31 lacks a validated second playback bank, retain
bounded burst TX instead of claiming gapless playback.

- PSRAM may hold the host queue; the hardware playback banks must be proven
  accessible and correctly reserved.
- Interpolate input I/Q to the measured DAC rate and anti-image filter it.
  Host I/Q rate and internal DAC rate are different quantities.
- Record epochs, absolute sample position, queue occupancy, overruns,
  underruns and bank handoff timestamps.
- On starvation, transmit zeros **only if that behavior is characterized**;
  otherwise cleanly stop TX. Never repeat stale samples unintentionally.
- Make RX/TX explicitly half-duplex until separate RF clocks and isolation
  are demonstrated.

Initial target: gapless 250 kSa/s CS16LE for at least 60 seconds; then
attempt 1 MSa/s and higher. RX USB 20 MSa/s and Ethernet 40 MSa/s
measurements do not validate the reverse TX direction.

**Gate:** independently captured signal continuity, no underruns, no
bank corruption, repeatable TX stop, and RF spectral characterization.

## Stage 4: SoapySDR TX — blocked on Stage 3

Extending [SoapyESPSDR](https://github.com/ESPARGOS/SoapyESPSDR) requires a
new negotiated opt-in protocol and host-to-device path:

- USB: separate bulk OUT IQ data endpoint, not RX 0x82.
- Ethernet: owner-bound input datagrams with stream epoch, sample position,
  allowed peer and bounded buffers; reject stale/non-owner packets.
- Soapy TX channel exists **only** for firmware that reports validated TX.
  Implement writeStream, activate/deactivate, close, format/rate/frequency/
  gain discovery and timeout/underflow errors; remain half-duplex.
- Regression tests: RX-only capability detection, split USB transfers,
  stalled host, stream restarts, reset/unplug, buffer overflow and recovery.

## Safety and acceptance

First RF tests require conducted RF or a shielded box, a suitably attenuated
measurement receiver and matched load, not an attached antenna. Verify local
authorization and emissions restrictions before radiating. Arbitrary waveform
modulation has no automatic compliance exemption, including inside ISM bands.

| Milestone | Software | S31 hardware |
| --- | --- | --- |
| Existing S31 RX | Source preserved apart from read-only probe | Regression pending |
| Passive TXCAPS?/TXPROBE? | Implemented; host test included | Snapshot pending |
| Pinned PHY symbol audit | Script implemented | Inventory pending |
| Offline CS16LE test vectors | Implemented; host test included | Not applicable |
| RF DAC I/Q burst | Not implemented | Unverified |
| Gapless dual-bank TX | Not implemented | Unverified |
| SoapySDR TX | Not implemented | Unverified |

Do not interpret a passing host test or an unexamined PHY symbol as an RF
validation result.
