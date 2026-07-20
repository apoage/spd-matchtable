# Contributing

This is a small, single-file tool where correctness matters more than
almost anything else — it's read by people deciding what to type into
their BIOS. The bar for any change is: **verify it against real data
before it goes in, not just against your own reasoning about what the
spec says.**

## Before you open a PR

Run the built-in checks:

```
python3 -m py_compile spd_matchtable.py
python3 spd_matchtable.py --selftest
```

CI runs both across Python 3.8–3.13 on every push and PR — see
`.github/workflows/ci.yml`.

## If you're adding or changing a byte offset

This project has already caught two offset mistakes during its own
development — one in this tool's own early code, one in an external
review that suggested a fix — both because someone counted bytes by hand
and got it wrong. Don't repeat that. Instead:

1. **Derive the offset programmatically**, not by counting hex bytes by
   eye. If you're working from a reference struct (e.g. a C#/C
   definition from another project), replicate its field list and sizes
   in a small Python script and let it compute cumulative offsets —
   see the git history around the `snap_to_jedec_bin` / timebase-byte fix
   for a worked example of exactly this process.
2. **Cross-check against a second independent source** before trusting a
   new offset — `decode-dimms` output, a datasheet, or (ideally) both.
   A value that only agrees with one source is not yet verified.
3. **Capture any new test fixture mechanically** (`xxd -p` on a real
   EEPROM dump), never by hand-transcribing hex from a terminal
   transcript or a forum post. A manual transcription error is exactly
   how this project's own selftest fixture briefly broke during
   development (see `RELEASE_NOTES.md`).
4. **Add expected values to `--selftest` that come from an independent
   source** (a datasheet, `decode-dimms`, a vendor spec sheet) — not
   values computed by the same code path you're testing, which would
   make the test pass even if the parser were wrong.

## If you're changing the worst-case computation

Read [`Worst Case Methodology`](https://github.com/apoage/spd-matchtable/wiki/Worst-Case-Methodology)
on the wiki first — it documents a real bug (`tRC` needing a
`tRAS+tRP` floor) that a naive per-parameter maximum missed, and exactly
why the fix is a safe over-approximation rather than an exact
reconstruction. Any change here should come with a synthetic
`--selftest` case reproducing the scenario it fixes or protects against,
the same way that one does.

## Style

- No comments explaining *what* code does — names should do that. Comments
  are for *why*, when it's genuinely non-obvious (a hidden constraint, a
  workaround, a subtlety a reader would otherwise miss).
- Stdlib only. No dependencies, no exceptions — this tool's ability to
  just run anywhere python3 exists is a real property worth keeping,
  not an implementation detail.
- Keep output strict-ASCII and within 80 columns (`MAX_TERM_WIDTH`) — see
  the wiki's [`FAQ and Design Decisions`](https://github.com/apoage/spd-matchtable/wiki/FAQ-and-Design-Decisions)
  page for why.

## Reporting a bug in the decoder itself

If you believe a timing value this tool computes is wrong for a real
module, the most useful thing you can attach to an issue is:

```
python3 spd_matchtable.py --json > report.json
```

...plus, if you're able to get it, a raw dump of the specific module's
SPD (`cat /sys/bus/i2c/drivers/ee1004/X-00YY/eeprom > stick.bin`) so the
exact bytes can be checked rather than a second-hand description of the
symptom.
