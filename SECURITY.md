# Security

## Scope

This tool reads SPD EEPROM data (from live sysfs or from a file you
provide) and prints computed timing values. It never writes to an
EEPROM, never touches BIOS/UEFI settings, and never requires elevated
privileges in the normal case. The realistic security-relevant surface
is therefore narrow:

- **Malformed or adversarial SPD data** (a corrupted, counterfeit, or
  deliberately crafted EEPROM dump) causing the tool to crash, hang, or
  print misleading output. This is taken seriously and actively tested
  against — see `--selftest`'s failure-path checks and
  `RELEASE_NOTES.md` for the specific guards this has already prompted
  (CRC validation, non-DDR4/non-standard-timebase rejection,
  negative/nonsensical-timing rejection, terminal-escape-sequence
  filtering on string fields).
- **A wrong computed value being trusted for a real BIOS change.** This
  is the actual stakes of a bug here — not data exfiltration or remote
  code execution, but someone applying a timing or voltage this tool
  suggested and getting instability or, in a worse case, memory
  corruption. Treat any report of a wrong computed value with the same
  urgency as a conventional security bug.

## What's explicitly out of scope

- Privilege escalation: the tool never requests or requires elevated
  privileges, so there's no privileged code path to escalate through.
- Supply-chain concerns around the `ee1004` kernel driver or `i2c-tools`
  itself — those are upstream kernel/distro concerns, not this project's.

## Reporting

If you find a case where crafted or corrupted SPD input causes anything
other than a clean, labeled error — a crash, a hang, or (most
importantly) a plausible-looking but wrong number that isn't caught by
existing validation — please open an issue with:

- The exact input (a raw SPD file, or the `--json` output if it's a live
  system)
- What you expected vs. what happened

There is no bug bounty; this is a small hobby-scale tool. Reports are
still genuinely welcome and will be fixed with the same rigor documented
in `RELEASE_NOTES.md`'s existing entries — every previous fix here was
independently verified against real data before being applied, not just
patched reactively.
