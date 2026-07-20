# spd-matchtable

**A Linux CLI tool that reads the raw SPD of every installed DDR4 module and
computes the safest common memory timing profile for a mixed/mismatched RAM
kit — a DDR4 mixed-kit compatibility calculator, decode-dimms with XMP
support, and a free alternative to Thaiphoon Burner for this one specific
job.**

If you searched for anything like *"mixed RAM kit timings calculator"*,
*"DIMM compatibility calculator"*, *"safe RAM overclock mismatched modules"*,
*"XMP mixed kit"*, *"decode-dimms XMP"*, *"JEDEC SPD worst-case calculator"*,
or *"ee1004 python"* — this is built for exactly that question.

## What it's for

You've got a DDR4 system with a **mixed kit** — modules bought at different
times, from different revisions, maybe even different manufacturers — and
it either won't train at a decent speed, or it "accidentally" booted at some
auto setting and you want to know the best manual BIOS profile you can
safely run, without pulling sticks and testing them one at a time in
another machine.

It reads the raw SPD EEPROM of every installed DDR4 module directly from
the kernel, decodes the real JEDEC timing values (not just the printed box
speed), and computes the **worst-case (governing) requirement per timing
across all installed modules** at a set of candidate clock speeds — i.e.
the tightest setting every stick can actually agree to at once.

## Requirements

- Linux with the `ee1004` kernel module bound (standard on any distro with a
  recent kernel; DDR4 SPD EEPROMs use this driver). If nothing shows up,
  try `sudo modprobe ee1004`.
- **Python 3.6+, standard library only** — no `pip install` of anything.
- **No root required** in the normal case — the `ee1004` sysfs `eeprom`
  attribute is world-readable on stock kernels.
- DDR4 only. DDR5 uses a different SPD driver (`spd5118`) and isn't
  supported — the tool will simply find nothing and say so.

## Install

```
git clone https://github.com/apoage/spd-matchtable.git
cd spd-matchtable
```

No build step, no dependencies to install.

## Usage

```
./spd_matchtable.py                              # full text report
./spd_matchtable.py --freqs 2666,2933,3200        # only these candidate clocks
./spd_matchtable.py --json                        # raw decoded data, all fields
./spd_matchtable.py --selftest                    # run the built-in correctness check
```

## Example output

4-module mixed kit, 2 dual-rank + 2 single-rank:

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

Base SPD ceiling: 2401 MT/s (limited by 1-0050, 1-0051)

=== Match table: cycles needed to satisfy ALL modules, per candidate MT/s ===
+-------+------+------+------+------+------+
| Param | 2400 | 2666 | 2933 | 3000 | 3200 |
+-------+------+------+------+------+------+
| CL    | 17   | 19   | 21   | 21   | 23   |
| tRCD  | 17   | 19   | 21   | 21   | 23   |
| tRC   | 56   | 62   | 69   | 70   | 75   |
...
+-------+------+------+------+------+------+
```

`tRC` is enforced as at least `tRAS + tRP` (the physical
ACT→[tRAS]→PRE→[tRP]→ACT chain), not just the largest value any one module
happens to declare — some vendors' own SPD data lists a `tRC` smaller than
their own `tRAS+tRP`, so this floor is computed explicitly rather than
trusted to fall out of a per-parameter maximum.

A table wider than 80 columns (e.g. a long `--freqs` list) is automatically
split into multiple side-by-side pages rather than overflowing the line.

Read the `Param` rows as: "if you want to run at this MT/s, every one of
these values needs to be at least this many cycles, because at least one
installed module genuinely requires that much time in nanoseconds." Lower
target frequencies need looser (higher) numbers relative to their own
clock, but the same physical nanosecond requirement.

Output is plain 80-column, strict-ASCII (`+`, `-`, `|` borders only, no
Unicode box-drawing) on purpose — this is meant to also work cleanly on a
bare recovery console (serial console, VGA text mode, no X11), which is
where a mixed-kit system that barely booted is most likely to be diagnosed
from.

## What to expect (and what it deliberately does *not* do)

- **Read-only, always.** No EEPROM writes, no BIOS/UEFI interaction of any
  kind — it only reads sysfs files and prints numbers.
- **Doesn't guess channel topology.** Whether two sticks share a channel is
  a motherboard trace-layout question SPD can't answer, so the tool doesn't
  pretend to know it.
- **Doesn't treat embedded XMP profiles as the answer for a mixed kit.**
  They're printed for reference only — a profile only applies if *every*
  installed module supports it, and in a mixed kit usually only some do.
  The match table is computed independently from each module's base JEDEC
  data, which is the part guaranteed present on every DDR4 module.
- **Isn't a substitute for a real stability test.** It tells you the
  theoretical safe common ground per the modules' own declared specs.
  Actual stability still depends on your board's memory controller, trace
  layout, and silicon lottery — always run a full Memtest86 pass on
  anything this suggests before trusting it with real data (the tool
  reprints this reminder at the end of every run).
- A module that fails its own SPD CRC check, isn't DDR4, uses a
  non-standard timebase encoding, or decodes to a negative/nonsensical
  timing is rejected with a clear error rather than silently trusted.

See [Releases](https://github.com/apoage/spd-matchtable/releases) for
what's changed release to release, including audit/hardening notes for
anyone vetting a specific version before trusting it.

## Attribution

DDR4 XMP 2.0 struct offsets (the part `decode-dimms` doesn't parse at all)
were taken from [integralfx/DDR4XMPEditor](https://github.com/integralfx/DDR4XMPEditor)
(`DDR4SPD/SPD.cs`, `DDR4SPD/XMP.cs`), an open-source DDR4 SPD/XMP editor.
Base JEDEC field offsets were cross-checked against that project's `RawSPD`
struct and against `decode-dimms` (i2c-tools) output.

## License

MIT — see `LICENSE`.
