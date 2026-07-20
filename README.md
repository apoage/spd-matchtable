# spd_matchtable

A small, dependency-free Linux tool for one specific situation: you've got a
DDR4 system with a **mixed kit** — modules bought at different times, from
different revisions, maybe even different manufacturers — and it either
won't train at a decent speed, or it "accidentally" booted at some auto
setting and you want to know the best manual BIOS profile you can safely
run without pulling sticks and testing them one at a time.

It reads the raw SPD EEPROM of every installed DDR4 module directly from
the kernel, decodes the real JEDEC timing values (not just the printed
box speed), and computes the **worst-case (governing) requirement per
timing across all installed modules** at a set of candidate clock speeds —
i.e. the tightest setting every stick can actually agree to at once.

## Why this and not [existing tool]?

Before writing this we checked. The landscape:

| Tool | Platform | Reads XMP | Computes a cross-module worst-case table |
|---|---|---|---|
| `decode-dimms` (i2c-tools) | Linux CLI | No | No |
| hardinfo | Linux GUI | Partial | No |
| Thaiphoon Burner | Windows, GUI | Yes, excellent | No — manual comparison only |
| RAMMon | Windows | Basic | No |
| Ryzen DRAM Calculator / ZenTimings | Windows | N/A | No — tunes for one known IC, not multiple installed modules |

Nothing found reads every populated slot on a live system and automatically
computes a governing value across all of them into a ready BIOS table,
without a GUI, without Windows, without proprietary software. That's the
specific gap this fills. If you know of prior art that already does this,
please open an issue — happy to be pointed at it and defer.

## Requirements

- Linux with the `ee1004` kernel module bound (standard on any distro with a
  recent kernel; DDR4 SPD EEPROMs use this driver). If nothing shows up,
  try `sudo modprobe ee1004`.
- **Python 3.6+, standard library only.** No `pip install` of anything.
- **No root required** in the normal case — the `ee1004` sysfs `eeprom`
  attribute is world-readable on stock kernels. If your system restricts it,
  you'll get a clear permission-denied warning naming the file, not a
  crash.
- DDR4 only. DDR5 uses a different SPD driver (`spd5118`) with a different
  layout and is out of scope here — the tool will simply find no `ee1004`
  devices and say so.

## Usage

```
./spd_matchtable.py                              # full text report
./spd_matchtable.py --freqs 2666,2933,3200        # only these candidate clocks
./spd_matchtable.py --json                        # raw decoded data, all fields
./spd_matchtable.py --selftest                     # run the built-in correctness check
```

Example output (4-module mixed kit, 2 dual-rank + 2 single-rank):

```
=== Installed modules ===
+--------+------------------+------+-------+---------+--------+-----+
| Slot   | Part Number      | Rank | Width | Density | MaxMTs | CRC |
+--------+------------------+------+-------+---------+--------+-----+
| 1-0050 | KF3200C16D4/16GX | 2    | x8    | 8Gb     | 2401   | OK  |
| 1-0051 | KF3200C16D4/16GX | 2    | x8    | 8Gb     | 2401   | OK  |
| 1-0052 | KF432C16BB3/16   | 1    | x8    | 16Gb    | 3200   | OK  |
| 1-0053 | KF432C16BB/8     | 1    | x16   | 16Gb    | 3200   | OK  |
+--------+------------------+------+-------+---------+--------+-----+

Guaranteed-for-all ceiling (no OC on any module): 2401 MT/s

=== Match table: cycles needed to satisfy ALL modules, per candidate MT/s ===
+-------+------+------+------+------+------+
| Param | 2400 | 2666 | 2933 | 3000 | 3200 |
+-------+------+------+------+------+------+
| CL    | 17   | 19   | 21   | 21   | 23   |
| tRCD  | 17   | 19   | 21   | 21   | 23   |
...
+-------+------+------+------+------+------+
```

Read the `Param` rows as: "if you want to run at this MT/s, every one of
these values needs to be at least this many cycles, because at least one
installed module genuinely requires that much time in nanoseconds." Lower
target frequencies need looser (higher) numbers relative to their own
clock, but the same physical nanosecond requirement.

Output is plain 80-column, strict-ASCII (`+`, `-`, `|` borders only, no
Unicode box-drawing) on purpose — this is meant to also work cleanly on a
bare recovery console (serial console, VGA text mode, no X11) where a
mixed-kit system that barely booted is most likely to be diagnosed from.

## What it deliberately does *not* do

- **Does not write anything.** No EEPROM writes, no BIOS/UEFI interaction of
  any kind. It opens SPD files `"rb"` only. If you're auditing this, that's
  the one invariant worth checking first.
- **Does not guess channel topology.** Whether two sticks share a channel is
  a motherboard trace layout question SPD can't answer, so the tool doesn't
  pretend to know it.
- **Does not treat embedded XMP profiles as the answer for a mixed kit.**
  They're printed for reference (a profile only applies if *every* installed
  module supports it — and in a mixed kit, usually only some do). The match
  table is computed independently, from each module's base JEDEC data, which
  is the part that's actually guaranteed present on every DDR4 module.
- **Does not replace a real stability test.** It tells you what's
  *theoretically* the safe common ground per the modules' own declared
  specs. Actual stability still depends on your specific board's memory
  controller, trace layout, and silicon lottery. Always run a full
  Memtest86 pass on anything this suggests before trusting it with real
  data — the tool prints this reminder itself at the end of every report.

## Safety / audit notes

This was written expecting to be read by someone other than its author
before being trusted, so a few things worth pointing out directly:

- **CRC validated.** Every module's base SPD block is checked against its
  own stored CRC-16 (the same check `decode-dimms` performs, values
  cross-verified against it: `0xF56C` / `0xC6AB` in the shipped test
  fixture). A module that fails CRC is excluded from the match table by
  default and clearly flagged — the tool refuses to quietly compute a
  "safe" table from data that might already be corrupt. Override with
  `--include-invalid` if you understand the risk.
- **String fields are sanitized**, not just null-stripped. SPD data is, in
  principle, attacker-influenceable (a crafted or counterfeit EEPROM), so
  the part-number field is hard-filtered to printable ASCII before it ever
  reaches your terminal — this specifically blocks any terminal
  escape-sequence injection via a malicious or corrupted SPD dump.
- **No unhandled tracebacks in normal operation.** Missing kernel module,
  permission errors, malformed/short reads, division-by-zero on a
  blank/corrupt EEPROM, bad `--freqs` input — all produce a short message
  and a non-zero exit code. Pass `--debug` to get the real traceback back
  if you're troubleshooting the tool itself.
- **`--selftest` is a real correctness check, not a smoke test.** The
  fixture bytes were captured mechanically (`xxd -p`) from a real module's
  live EEPROM, not hand-transcribed — a manual hex trace is exactly how a
  transcription bug slips in (it happened once during this tool's own
  development; see the offset confusion around SPD byte 17 that a byte-by-byte
  re-check caught). The expected values it asserts against were independently
  cross-checked against `decode-dimms` output and the module's published
  vendor spec (Kingston DDR4-3200 CL16-18-18-36 @ 1.35V) before being
  hard-coded, so a bug in the parser has an actual chance of being caught,
  rather than the fixture and the code agreeing by construction.
- **Only reads what it's told to read.** File discovery is
  `glob.glob("/sys/bus/i2c/drivers/ee1004/*/eeprom")` — a fixed, non-user-
  supplied kernel path, no shell involved, no path ever built from
  untrusted input.

## Why Python and not a shell script

This does bit-level struct unpacking (nibble-packed 12-bit fields,
signed fine-timebase corrections, 16-bit little-endian pairs) and
floating-point nanosecond arithmetic with careful rounding, then a CRC-16
bit-loop. Bash's integer arithmetic can do the bitwise part, but has no
native floating point (routing every ns computation through `awk`/`bc`
subprocesses instead), and the combination is considerably harder to
review for correctness than the direct Python — which matters more here
than shaving an interpreter-startup cost, given the whole point of
publishing this is that someone can audit it and trust the numbers.

Python 3 itself isn't an extra dependency in practice: this uses only the
standard library (`argparse`, `glob`, `json`, `math`, `sys`), and `python3`
ships by default on essentially every desktop Linux distribution. If
you're specifically on a minimal/rescue environment without it, that's the
one real gap — `apt install python3` / `pacman -S python` / equivalent is
the fix, not a rewrite.

## Attribution

DDR4 XMP 2.0 struct offsets (the part `decode-dimms` doesn't parse at all)
were taken from [integralfx/DDR4XMPEditor](https://github.com/integralfx/DDR4XMPEditor)
(`DDR4SPD/SPD.cs`, `DDR4SPD/XMP.cs`), an open-source DDR4 SPD/XMP editor.
Base JEDEC field offsets were cross-checked against that project's `RawSPD`
struct and against `decode-dimms` (i2c-tools) output.

## License

MIT — see `LICENSE`.
