# spd-matchtable

[![CI](https://github.com/apoage/spd-matchtable/actions/workflows/ci.yml/badge.svg)](https://github.com/apoage/spd-matchtable/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.6+](https://img.shields.io/badge/python-3.6%2B-blue.svg)](https://www.python.org/)
[![Wiki](https://img.shields.io/badge/docs-wiki-informational.svg)](https://github.com/apoage/spd-matchtable/wiki)

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
./spd_matchtable.py                              # full text report, live system
./spd_matchtable.py --freqs 2666,2933,3200        # only these candidate clocks
./spd_matchtable.py --json                        # decoded data + computed tables, all fields
./spd_matchtable.py --selftest                    # run the built-in correctness check
./spd_matchtable.py stick1.bin stick2.bin ...      # decode offline dumps instead of live sysfs
```

The offline mode is for the exact situation this tool is written for: a
system that only boots with some sticks installed, or SPDs you'd rather
dump and analyze one module at a time. Produce a dump with:

```
cat /sys/bus/i2c/drivers/ee1004/1-0050/eeprom > stick1.bin
```

then pass however many `.bin` files you've collected (from this machine,
another machine, or a forum post) as arguments in place of live discovery.

## Example output

4-module mixed kit, 2 dual-rank + 2 single-rank:

```
=== Installed modules ===
-- columns 1/2 --
+--------+------------------+----------+------+-------+---------+-------+
| Slot   | Part Number      | Capacity | Rank | Width | Density | Type  |
+--------+------------------+----------+------+-------+---------+-------+
| 1-0050 | KF3200C16D4/16GX | 16GB     | 2    | x8    | 8Gb     | UDIMM |
| 1-0051 | KF3200C16D4/16GX | 16GB     | 2    | x8    | 8Gb     | UDIMM |
| 1-0052 | KF432C16BB3/16   | 16GB     | 1    | x8    | 16Gb    | UDIMM |
| 1-0053 | KF432C16BB/8     | 8GB      | 1    | x16   | 16Gb    | UDIMM |
+--------+------------------+----------+------+-------+---------+-------+
-- columns 2/2 --
+--------+-----+--------+-----+----------+----------+
| Slot   | ECC | MaxMTs | CRC | Serial   | MfgDate  |
+--------+-----+--------+-----+----------+----------+
...
+--------+-----+--------+-----+----------+----------+

Base SPD ceiling: 2400 MT/s (limited by 1-0050, 1-0051)

=== Match table: cycles needed to satisfy ALL modules, per candidate MT/s ===
+-------+------+------+------+------+------+
| Param | 2400 | 2666 | 2933 | 3000 | 3200 |
+-------+------+------+------+------+------+
| CL    | 17   | 19   | 21   | 21   | 23   |
| tRCD  | 17   | 19   | 21   | 21   | 23   |
| tRC   | 56   | 62   | 69   | 70   | 75   |
...
+-------+------+------+------+------+------+

=== OC required beyond each module's own base JEDEC spec ===
  2400 MT/s: none (native for all modules)
  2666 MT/s: 1-0050, 1-0051
  ...

=== Suggested starting point ===
@ 2400 MT/s (from SPD, zero OC required): CL17-17-17-39 tRC56 tRFC1=660
tFAW=36 tRRD_S/L=7/8 tCCD_L=6 tWR=18 tWTR_S/L=3/9 @ 1.20V
Inferred, NOT from SPD -- generic starting points, verify in BIOS: tCWL=16
tRTP~=9 tCCD_S=4 Command Rate=2T
```

The two lines under "Suggested starting point" are deliberately kept
separate: the first line is entirely derived from the modules' own SPD
data; the second is generic OC-guide heuristics (`tCWL = CL-1`, a ~7.5ns
`tRTP` estimate, `tCCD_S=4`, and a rank/DIMM-count-based command-rate
guess) that DDR4 SPD does not encode at all — labeled as such rather than
presented with the same authority as the measured values.

If any installed modules are a mix of RDIMM/UDIMM or ECC/non-ECC — the two
most common reasons a mixed kit refuses to POST at all rather than just
underperforming — a `!!` warning prints right after the modules table.

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
- **Does check for the two most common "won't POST at all" mismatches** —
  mixing registered (RDIMM/LRDIMM) with unbuffered (UDIMM) modules, or
  mixing ECC with non-ECC — and prints a loud warning if either is present,
  since these are worse failure modes than just missing a target speed.
- **The "OC required" and "Suggested starting point" sections make
  inferences, and say so.** Which modules need overclocking beyond their
  own SPD at a given candidate speed, and the generic secondary-timing
  guesses (`tCWL`, `tRTP`, `tCCD_S`, command rate) that DDR4 SPD doesn't
  encode at all, are both clearly separated from the SPD-measured numbers
  rather than presented with the same certainty.

See [Releases](https://github.com/apoage/spd-matchtable/releases) for
what's changed release to release, including audit/hardening notes for
anyone vetting a specific version before trusting it.

## What each value actually means

The **[wiki](https://github.com/apoage/spd-matchtable/wiki)** explains,
in real technical depth, what every timing this tool reads or computes
physically gates in a DRAM chip — not just the abbreviation, but why it
exists in the protocol and what breaks if it's wrong: primary timings
(CL/tRCD/tRP/tRAS/tRC), refresh (tRFC), bank-group/activation timings
(tFAW/tRRD/tCCD), write/turnaround timings (tWR/tWTR/tRTP), the inferred
values (tCWL, command rate), voltage, the SPD byte structure itself, the
capacity formula, module-type/ECC compatibility, XMP profiles, the
worst-case computation methodology (including a real bug it found and
fixed), and a design-decisions FAQ answering the "why not just..."
questions this tool's approach invites.

## No warranty

This software is provided **as is, with absolutely no warranty of any
kind** — see `LICENSE` (MIT) for the exact legal text. Nothing here
verifies or guarantees that a suggested profile is stable on your specific
board, CPU, or silicon; it computes what the modules' own SPD data
implies, nothing more.

Applying memory timings and voltages in your BIOS/UEFI is something you
should understand before you do it — a wrong voltage or an unstable
timing can corrupt data or make a system fail to boot. If you don't
already know what CL/tRCD/tRP/tRAS or a DRAM voltage rail is, or how to
recover a system that won't POST (clearing CMOS, restoring a BIOS default
profile), read up on that first. Use the output here as an input to your
own judgment, not as an instruction to follow blindly, and validate
anything it suggests with a full Memtest86 pass before trusting it with
real data.

## Attribution

DDR4 XMP 2.0 struct offsets (the part `decode-dimms` doesn't parse at all)
were taken from [integralfx/DDR4XMPEditor](https://github.com/integralfx/DDR4XMPEditor)
(`DDR4SPD/SPD.cs`, `DDR4SPD/XMP.cs`), an open-source DDR4 SPD/XMP editor.
Base JEDEC field offsets were cross-checked against that project's `RawSPD`
struct and against `decode-dimms` (i2c-tools) output.

## Built with AI assistance

Worth stating plainly rather than leaving implicit: this project — the
decoder itself, the CLI, the wiki, and the repo scaffolding — was built
in collaboration with **Claude** (Anthropic). The v1.0.1 correctness and
safety fixes were prompted by an independent code review from **Kimi**
(Moonshot AI), which caught a real bug (the `tRC` understatement
documented in [[Worst Case Methodology]](https://github.com/apoage/spd-matchtable/wiki/Worst-Case-Methodology))
— but that same review also cited a wrong byte offset for one of its own
suggested fixes, which had to be independently re-derived and corrected
before being applied (see `RELEASE_NOTES.md`, v1.0.1). Nothing an AI
suggested — model or reviewer — went in without being checked against
real reference hardware/data first; that discipline is the actual point
of this project's development history, not an afterthought applied to
otherwise-blind AI output.

## Contributing

See `CONTRIBUTING.md` — in particular the section on verifying byte
offsets programmatically rather than by hand, which this project's own
history shows is exactly where mistakes creep in (twice, so far — once
in this tool's own early code, once in an external review). Security-
relevant reports (a case where crafted/corrupt SPD data produces
something worse than a clean labeled error) are covered in `SECURITY.md`.
Participation is governed by `CODE_OF_CONDUCT.md`.

## License

MIT — see `LICENSE`.
