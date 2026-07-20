# Release notes

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
