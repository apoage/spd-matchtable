## What does this change and why

<!-- The "why" matters more than the "what" here -- see CONTRIBUTING.md. -->

## Checklist

- [ ] `python3 -m py_compile spd_matchtable.py` passes
- [ ] `python3 spd_matchtable.py --selftest` passes (paste the final line,
      e.g. `49/49 checks passed`)
- [ ] If this adds or changes a byte offset: it was derived programmatically
      (not hand-counted) and cross-checked against a second independent
      source (`decode-dimms` output, a datasheet, another tool's source) --
      see `CONTRIBUTING.md`
- [ ] If this adds a new test fixture: the bytes were captured mechanically
      (e.g. `xxd -p` on a real dump), not hand-transcribed
- [ ] If this changes output formatting: still strict-ASCII, still fits
      80 columns (`python3 spd_matchtable.py | awk '{print length}' | sort -rn | head`)
- [ ] `CHANGES`/`RELEASE_NOTES.md` updated if this is more than a typo fix

## Anything a reviewer should specifically double-check

<!-- e.g. "please re-verify this offset independently, I only checked it
one way" -- flagging your own uncertainty here is welcome, not penalized. -->
