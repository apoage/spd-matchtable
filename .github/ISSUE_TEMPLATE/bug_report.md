---
name: Bug report
about: A computed value looks wrong, the tool crashed, or something else broke
title: ""
labels: bug
assignees: ""
---

## What happened

<!-- What did the tool do, and what did you expect instead? -->

## The most useful attachment: raw data, not a description

If this is about a **computed value** (a timing, a capacity, a warning
that did or didn't fire), the fastest way to get it fixed is the actual
bytes, not a description of the symptom:

```
python3 spd_matchtable.py --json > report.json
```

If possible, also attach a raw dump of the specific module in question:

```
cat /sys/bus/i2c/drivers/ee1004/X-00YY/eeprom > stick.bin
```

(Replace `X-00YY` with the actual i2c address shown in the tool's own
output.) Both files contain nothing about your identity — just SPD
timing/organization data — but feel free to redact the serial number
field if you'd rather not share it.

- [ ] I've attached `--json` output and/or a raw `.bin` dump
- [ ] I ran `python3 spd_matchtable.py --selftest` and it still passes (if it
      doesn't, please paste that output instead — that's a different kind
      of bug)

## Environment

- OS / distro:
- Python version (`python3 --version`):
- Tool version (`python3 spd_matchtable.py --version`):
- Live system or offline `.bin` files?
