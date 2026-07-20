# Release notes

## v1.1.0

The usefulness half of the same v1.0.0 review: v1.0.1 fixed correctness
and safety, this addresses "what's actually missing for the stated use
case" (a mixed kit that barely boots, being diagnosed one module at a
time). Every new byte offset used here (moduleType, busWidth/ECC bit,
serial number) was independently cross-checked against the live reference
kit's `decode-dimms` output before being trusted -- same discipline as
every previous release.

### New features

- **Offline SPD file input.** `spd_matchtable.py stick1.bin stick2.bin ...`
  decodes dumps instead of live sysfs -- the actual gap for a system that
  only boots with some sticks installed, or for combining dumps taken one
  module at a time, from another machine, or shared in a forum post.
  Produce one with `cat /sys/bus/i2c/drivers/ee1004/1-0050/eeprom >
  stick.bin`.
- **Module-type and ECC mismatch warnings.** Mixing RDIMM/LRDIMM with
  UDIMM, or ECC with non-ECC, are the #1 and #2 reasons a mixed kit fails
  to POST at all rather than just underperforming -- previously silent,
  now a loud `!!` warning right after the modules table.
- **Physical identification columns**: capacity (computed from density,
  width, and rank -- verified against this project's own 3 known reference
  capacities before trusting the formula), serial number, and
  manufacture date, so "1-0053 is your problem stick" can actually be
  matched to a physical DIMM without guessing.
- **OC-boundary markers.** A new section lists, per candidate MT/s, which
  installed modules are being pushed beyond their own declared JEDEC base
  spec -- previously the match table alone gave no indication that half
  the "candidates" were an overclock for some fraction of the kit.
- **"Suggested starting point"**: a ready BIOS line at the SPD-guaranteed
  ceiling, plus a clearly separate second line for generic secondary
  timings SPD doesn't encode (`tCWL = CL-1`, a ~7.5ns `tRTP` estimate,
  `tCCD_S=4`, and a rank/DIMM-count-based command-rate guess) -- labeled
  as inference, not measurement.
- `--json` now includes the same computed `worstCaseNs`, `matchTableCycles`,
  and `compatibilityWarnings` the text report shows, not just the raw
  per-module decode -- so scripts can consume the tool's actual product.

### Fixes and polish from the same review

- **Frequency-labeling rounding artifact removed.** `2000/tCKmin` on a
  JEDEC-rounded `tCKmin` (e.g. DDR4-2400's published 0.833ns) doesn't
  invert cleanly and previously showed as "2401 MT/s" everywhere,
  including feeding real cycle math in the new cheat-sheet line. Snapped
  to the nearest standard JEDEC speed bin within a tight tolerance instead
  -- ceiling, match-table columns, and cheat sheet now agree exactly.
- Natural slot/filename sorting (`2-0050` before `10-0050`, not the
  reverse) instead of plain lexical sort.
- Soft sanity warnings for a `--freqs` value outside a realistic DDR4
  range, or below any installed module's minimum supported speed
  (`tCKmax` exceeded) -- previously silent.
- `--selftest` grew from 39 to 49 checks: natural sort ordering, the
  capacity formula, module-type/ECC decoding, the frequency-snapping fix,
  both compatibility-mismatch detectors (plus a no-false-positive check on
  a matched kit), and a round-trip check that offline file input decodes
  identically to live sysfs reads.

## v1.0.1

Fixes from an independent review of v1.0.0, each verified against the live
reference kit before being applied (one of the review's own claims turned
out to have an off-by-one and was corrected rather than applied verbatim
— see below).

### Correctness

- **`tRC` was understated.** The match table computed each timing as an
  independent maximum across modules, which is valid for every parameter
  except `tRC`: the same-bank cycle must cover the full
  `ACT -> [tRAS] -> PRE -> [tRP] -> ACT` chain, so `tRC >= tRAS + tRP` is a
  protocol floor, not just whatever the loosest module happens to declare.
  On the reference 4-module kit this was measurably wrong: worst-case
  `tRAS + tRP` = 46.375 ns vs. the previously-reported worst-case `tRC` of
  45.75 ns — a real, provable 0.625 ns understatement, confirmed before
  fixing. `worst_case()` now enforces this floor and annotates the source
  as e.g. `1-0053 (tRAS+tRP floor)` instead of silently substituting a
  number with no explanation.

### Safety

- **Rejects non-DDR4 and non-standard-timebase SPD data.** Previously
  nothing checked that byte 0x02 actually says DDR4, or that the SPD
  timebase encoding (byte 0x11) matches the MTB=125ps/FTB=1ps assumption
  the whole decoder hardcodes — a DDR3 module (or any SPD with a
  non-standard timebase) wired to `ee1004`, whether by a manual
  `new_device` mistake or an unexpected i2c-mux target, would have been
  decoded and trusted with confident, wrong numbers. Both are now checked
  and rejected with a specific error.
  - Note on the review that flagged this: it identified the right gap but
    cited the wrong byte offset for the timebase check (0x10 instead of
    0x11 — 0x10 is the tail of an unrelated reserved field). Re-derived
    the offset independently by replicating the exact upstream C# struct
    field order before writing the fix, rather than applying the
    suggested line as given.
- **Rejects negative/nonsensical decoded timings and out-of-range
  `tCKmin`.** A crafted or corrupted-but-CRC-valid EEPROM could previously
  decode to e.g. a negative `tRRD_S` and have it flow silently into the
  match table as "0 cycles, no complaint." Every base timing is now
  bounds-checked, and a corrupt XMP profile is treated as absent (not
  fatal to the rest of the module) rather than surfaced as a bogus
  number.
- **`--include-invalid` no longer disappears after the warning line.** A
  visible banner now repeats directly under the match table itself when
  a CRC-failed module was force-included, so the caveat survives even if
  only the table gets copy-pasted or screenshotted elsewhere.

### Output

- **The 80-column claim is now enforced, not just documented.** Tables
  wider than 80 columns (e.g. a long `--freqs` list, or the full XMP
  detail columns) are automatically paginated into side-by-side column
  groups instead of silently overflowing the line — the constant
  governing this was previously defined but never actually referenced by
  the table renderer.
- Restored the full XMP profile columns (`FAW`, `RRD_S`, `RRD_L`) that
  v1.0.0 had dropped to force-fit one specific case under 80 columns;
  pagination makes that trade-off unnecessary now.
- The "ceiling" line now names which module(s) are the limiting factor
  (`Base SPD ceiling: 2401 MT/s (limited by 1-0050, 1-0051)`) instead of
  just asserting a number, and drops the word "guaranteed," which
  overstated what base-SPD-only data can actually promise about real
  training stability.

### Testing

- `--selftest` grew from 29 to 39 checks, adding coverage for exactly the
  failure paths above: a CRC-repaired non-DDR4 mutation, a CRC-repaired
  bad-timebase mutation, direct checks on the negative-timing guard, a
  synthetic two-module case reproducing the `tRC` bug above byte-for-byte,
  and `--freqs` argument-parsing edge cases. The happy-path fixture proves
  the decoder is accurate; these prove it actually refuses bad input
  instead of confidently decoding it.

## v1.0.0

Initial release.

### Features

- Byte-accurate DDR4 SPD decoder, reading directly from the `ee1004`
  kernel driver's sysfs `eeprom` attribute — no `dmidecode`/`decode-dimms`
  dependency.
- Decodes base JEDEC timing block (CL, tRCD, tRP, tRAS, tRC, tRFC1/2/4,
  tFAW, tRRD_S/L, tCCD_L, tWR, tWTR_S/L) per installed module.
- Decodes embedded Intel XMP 2.0 profiles where present — a region
  `decode-dimms` does not parse at all.
- Computes the worst-case (governing) requirement per timing across all
  installed modules, at a configurable set of candidate clock speeds —
  the core "mixed-kit match table" feature.
- `--json` for raw decoded output, `--selftest` for a built-in correctness
  check, `--freqs` to choose candidate clocks, `--include-invalid` /
  `--debug` for troubleshooting.
- Output rendered as strict-ASCII bordered tables, fixed at 80 columns,
  so it renders correctly on a bare recovery console (serial console, VGA
  text mode, no X11) — the environment a barely-booting mixed-kit system
  is most likely to actually be diagnosed from.

### Audit & hardening notes

This tool is meant to be read and trusted by people other than its
author, so the following were deliberately verified rather than assumed:

- **CRC validated.** Every module's base SPD block is checked against its
  own stored CRC-16 (the same check `decode-dimms` performs internally).
  Values were cross-verified against `decode-dimms`'s own reported CRCs
  (`0xF56C` / `0xC6AB` for the shipped test fixture) before being trusted.
  A module that fails CRC is excluded from the match table by default and
  clearly flagged — the tool refuses to quietly compute a "safe" table
  from data that might already be corrupt. `--include-invalid` overrides
  this if you understand the risk.
- **String fields are sanitized, not just null-stripped.** SPD data is, in
  principle, attacker-influenceable (a crafted or counterfeit EEPROM), so
  the part-number field is hard-filtered to printable ASCII before it
  ever reaches a terminal — this specifically blocks terminal
  escape-sequence injection via a malicious or corrupted SPD dump.
- **No unhandled tracebacks in normal operation.** Missing kernel module,
  permission errors, malformed/short reads, division-by-zero on a
  blank/corrupt EEPROM, bad `--freqs` input — all produce a short message
  and a non-zero exit code. `--debug` restores the full traceback for
  troubleshooting the tool itself.
- **`--selftest` is a real correctness check, not a smoke test.** The
  fixture bytes were captured mechanically (`xxd -p`) from a real
  module's live EEPROM rather than hand-transcribed — a manual hex trace
  is exactly how a transcription bug slips in, and one did during this
  release's own development (an offset mix-up around SPD byte 17, caught
  by a byte-by-byte re-check). The expected values the selftest asserts
  against were independently cross-checked against `decode-dimms` output
  and the reference module's published vendor spec (Kingston
  DDR4-3200 CL16-18-18-36 @ 1.35V) before being hard-coded, so a bug in
  the parser has an actual chance of being caught rather than the fixture
  and the code agreeing by construction.
- **Only reads what it's told to read.** File discovery is
  `glob.glob("/sys/bus/i2c/drivers/ee1004/*/eeprom")` — a fixed,
  non-user-supplied kernel path, no shell involved, no path ever built
  from untrusted input.
- **Read-only invariant.** Every EEPROM file is opened `"rb"`. No write
  path exists anywhere in the tool, to an EEPROM or otherwise.

### Design decisions

- **Python, not a shell script.** The decoder does bit-level struct
  unpacking (nibble-packed 12-bit fields, signed fine-timebase
  corrections, 16-bit little-endian pairs) and floating-point nanosecond
  arithmetic with careful rounding, then a CRC-16 bit-loop. Bash's integer
  arithmetic covers the bitwise part, but has no native floating point —
  every ns computation would route through `awk`/`bc` subprocesses
  instead, which is considerably harder to review for correctness than
  direct Python. Since the entire point of publishing this is that
  someone can audit it and trust the numbers, that outweighed shaving an
  interpreter-startup cost. In practice Python 3 isn't an extra
  dependency either: only the standard library is used
  (`argparse`, `glob`, `json`, `math`, `sys`), and `python3` ships by
  default on essentially every desktop Linux distribution — the one real
  gap is a minimal/rescue environment without it, where `apt install
  python3` / `pacman -S python` is the fix, not a rewrite.
