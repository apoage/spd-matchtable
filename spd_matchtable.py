#!/usr/bin/env python3
"""Byte-accurate DDR4 SPD decoder and cross-module timing match table.

Reads raw SPD EEPROM data straight from the ee1004 kernel driver's sysfs
binary attribute (no dmidecode/decode-dimms dependency), decodes the base
JEDEC timing block per installed module, and computes the worst-case
(governing) timing every installed module needs satisfied simultaneously at
a set of candidate frequencies -- i.e. a safe manual BIOS starting point for
a mixed-kit system, derived independently of any single module's embedded
XMP profile.

SAFETY NOTES
  - Read-only. This tool never writes to an SPD EEPROM and never touches
    BIOS/UEFI settings; it only reads sysfs files and prints numbers.
  - No root/sudo required in the normal case: the ee1004 sysfs "eeprom"
    attribute is world-readable on stock kernels.
  - Every module's base SPD block is CRC16-validated (the same check JEDEC
    defines and decode-dimms performs) before its numbers are trusted. A
    module that fails CRC is excluded from the match table by default and
    clearly flagged, because computing a "safe" table from corrupted data
    would be worse than not computing one at all.
  - This is a read-only diagnostic aid, not a substitute for a real
    stability test. Always validate any resulting BIOS profile with a full
    Memtest86 pass before trusting it with real data.

Struct offsets and CRC algorithm cross-checked against two independent
sources: the JEDEC-compliant `decode-dimms` (i2c-tools) output, and the
open-source integralfx/DDR4XMPEditor project (DDR4SPD/SPD.cs, XMP.cs),
which reverse-engineered the DDR4 XMP 2.0 layout that decode-dimms does not
parse at all.
"""
import argparse
import glob
import json
import math
import sys

__version__ = "1.0.0"

MTB = 0.125  # ns per medium-timebase tick
FTB = 0.001  # ns per fine-timebase tick

DENSITY_MAP = ["256Mb", "512Mb", "1Gb", "2Gb", "4Gb", "8Gb", "16Gb", "32Gb", "12Gb", "24Gb"]
DEV_WIDTH = [4, 8, 16, 32, None, None, None, None]
PKG_RANKS = [1, 2, 3, 4, 5, 6, 7, 8]

BASE_PARAMS = ["CL", "tRCD", "tRP", "tRAS", "tRC", "tRFC1", "tRFC2", "tRFC4",
               "tFAW", "tRRDS", "tRRDL", "tCCDL", "tWR", "tWTRS", "tWTRL"]

SPD_SIZE = 512
EEPROM_GLOB = "/sys/bus/i2c/drivers/ee1004/*/eeprom"


class SPDError(Exception):
    """Raised for malformed or unreadable SPD data; always caught and
    reported cleanly, never left to surface as a raw traceback."""


def s8(b):
    return b - 256 if b >= 128 else b


def sanitize_text(raw):
    """Keep only printable ASCII. SPD string fields are attacker-influenceable
    (a crafted/counterfeit EEPROM), so this is a hard filter, not a cosmetic
    strip -- it blocks terminal control/escape sequences from ever reaching
    the user's terminal, not just null bytes."""
    return "".join(c for c in raw if 0x20 <= ord(c) <= 0x7E).strip()


def crc16(data):
    """JEDEC SPD CRC-16 (CCITT, poly 0x1021, init 0, no reflection)."""
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def verify_crc(d):
    crc1_stored = (d[0x7F] << 8) | d[0x7E]
    crc1_calc = crc16(d[0:0x7E])
    crc2_stored = (d[0xFF] << 8) | d[0xFE]
    crc2_calc = crc16(d[0x80:0xFE])
    return {
        "base_ok": crc1_calc == crc1_stored,
        "base_calc": crc1_calc, "base_stored": crc1_stored,
        "module_ok": crc2_calc == crc2_stored,
        "module_calc": crc2_calc, "module_stored": crc2_stored,
    }


def parse_base(d):
    banks = d[0x04]
    org = d[0x0C]
    tCKmin = d[0x12] * MTB + s8(d[0x7D]) * FTB
    tCKmax = d[0x13] * MTB + s8(d[0x7C]) * FTB

    if tCKmin <= 0:
        raise SPDError("tCKmin is zero or negative -- SPD data looks blank/corrupt")

    rasRC = d[0x1B]
    ras = ((rasRC & 0x0F) << 8) | d[0x1C]
    rc = ((rasRC & 0xF0) << 4) | d[0x1D]
    rfc1 = (d[0x1F] << 8) | d[0x1E]
    rfc2 = (d[0x21] << 8) | d[0x20]
    rfc4 = (d[0x23] << 8) | d[0x22]
    faw = ((d[0x24] & 0x0F) << 8) | d[0x25]

    density_idx = banks & 0x0F
    density = DENSITY_MAP[density_idx] if density_idx < len(DENSITY_MAP) else "?"

    width_idx = org & 0x7
    ranks_idx = (org >> 3) & 0x7
    width = DEV_WIDTH[width_idx]
    ranks = PKG_RANKS[ranks_idx] if ranks_idx < len(PKG_RANKS) else None

    part = sanitize_text(d[0x149:0x15D].decode("ascii", "replace").replace("\x00", ""))
    mfg_year, mfg_week = d[0x143], d[0x144]

    return {
        "part": part or "(unreadable)",
        "mfgDate": f"20{mfg_year:02x}-W{mfg_week:02x}",
        "density": density,
        "ranks": ranks,
        "width": width,
        "tCKmin": round(tCKmin, 4),
        "tCKmax": round(tCKmax, 4),
        "freq_max": round(2000 / tCKmin, 1),
        "CL": round(d[0x18] * MTB + s8(d[0x7B]) * FTB, 4),
        "tRCD": round(d[0x19] * MTB + s8(d[0x7A]) * FTB, 4),
        "tRP": round(d[0x1A] * MTB + s8(d[0x79]) * FTB, 4),
        "tRAS": round(ras * MTB, 4),
        "tRC": round(rc * MTB + s8(d[0x78]) * FTB, 4),
        "tRFC1": round(rfc1 * MTB, 4),
        "tRFC2": round(rfc2 * MTB, 4),
        "tRFC4": round(rfc4 * MTB, 4),
        "tFAW": round(faw * MTB, 4),
        "tRRDS": round(d[0x26] * MTB + s8(d[0x77]) * FTB, 4),
        "tRRDL": round(d[0x27] * MTB + s8(d[0x76]) * FTB, 4),
        "tCCDL": round(d[0x28] * MTB + s8(d[0x75]) * FTB, 4),
        "tWR": round(d[0x2A] * MTB, 4),
        "tWTRS": round(d[0x2C] * MTB, 4),
        "tWTRL": round(d[0x2D] * MTB, 4),
    }


def parse_xmp_profile(p):
    voltage_raw = p[0]
    voltage = ((voltage_raw & 0x80) >> 7) * 100 + (voltage_raw & 0x7F)
    tCK = p[3] * MTB + s8(p[38]) * FTB
    if tCK <= 0:
        return None

    ras = ((p[11] & 0x0F) << 8) | p[12]
    rc = ((p[11] & 0xF0) << 4) | p[13]
    rfc1 = (p[15] << 8) | p[14]
    faw = ((p[20] & 0x0F) << 8) | p[21]

    def cyc(ns):
        return math.ceil(round(ns / tCK, 6))

    cl = p[8] * MTB + s8(p[37]) * FTB
    rcd = p[9] * MTB + s8(p[36]) * FTB
    rp = p[10] * MTB + s8(p[35]) * FTB
    rrds = p[22] * MTB + s8(p[33]) * FTB
    rrdl = p[23] * MTB + s8(p[32]) * FTB

    return {
        "voltage": voltage / 100,
        "freq": round(2000 / tCK, 1),
        "CL": cyc(cl), "tRCD": cyc(rcd), "tRP": cyc(rp),
        "tRAS": cyc(ras * MTB), "tRC": cyc(rc * MTB),
        "tRFC1": cyc(rfc1 * MTB),
        "tFAW": cyc(faw * MTB),
        "tRRDS": cyc(rrds), "tRRDL": cyc(rrdl),
    }


def parse_xmp(d):
    hdr = d[0x180:0x189]
    if hdr[0] != 0x0C or hdr[1] != 0x4A:
        return None
    p1 = d[0x189:0x189 + 0x2F]
    p2 = d[0x189 + 0x2F:0x189 + 0x2F * 2]
    return {
        "version": f"{hdr[3] >> 4}.{hdr[3] & 0xF}",
        "profile1": parse_xmp_profile(p1) if hdr[2] & 0x1 else None,
        "profile2": parse_xmp_profile(p2) if hdr[2] & 0x2 else None,
    }


def parse_module(d, slot):
    if len(d) != SPD_SIZE:
        raise SPDError(f"expected {SPD_SIZE} bytes, got {len(d)}")
    crc = verify_crc(d)
    base = parse_base(d)
    xmp = parse_xmp(d)
    return {"slot": slot, "base": base, "xmp": xmp, "crc": crc}


def discover_modules(warn=print):
    modules = []
    paths = sorted(glob.glob(EEPROM_GLOB))
    for path in paths:
        slot = path.rstrip("/").split("/")[-2]
        try:
            with open(path, "rb") as f:
                d = f.read()
        except PermissionError:
            warn(f"warning: permission denied reading {path} (unexpected -- "
                 f"this file is normally world-readable; check module/udev rules)")
            continue
        except OSError as e:
            warn(f"warning: could not read {path}: {e}")
            continue
        try:
            modules.append(parse_module(d, slot))
        except SPDError as e:
            warn(f"warning: {slot}: {e} -- skipping this module")
    return modules


def worst_case(modules):
    worst, source = {}, {}
    for p in BASE_PARAMS:
        best_v, best_s = -1, None
        for m in modules:
            v = m["base"][p]
            if v > best_v:
                best_v, best_s = v, m["slot"]
        worst[p], source[p] = best_v, best_s
    return worst, source


MAX_TERM_WIDTH = 80  # plain 80-column console: no X11, no UTF-8 box-drawing assumed


def render_table(headers, rows):
    """Strict-ASCII bordered table (+, -, | only) so it renders correctly on
    any TTY -- serial console, VGA text mode, recovery shell -- with no
    reliance on UTF-8 glyph support. Column widths are computed from actual
    content, never hand-picked, so a rendering check is a real check."""
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def sep():
        return "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def fmt_row(cells):
        return "|" + "|".join(f" {str(c):<{w}} " for c, w in zip(cells, widths)) + "|"

    lines = [sep(), fmt_row(headers), sep()]
    lines.extend(fmt_row(row) for row in rows)
    lines.append(sep())
    return lines


def print_table(headers, rows):
    for line in render_table(headers, rows):
        print(line)


def print_report(modules, freqs, include_invalid, warn=print):
    if not modules:
        warn(f"No ee1004 SPD EEPROMs found under {EEPROM_GLOB}.")
        warn("Make sure the ee1004 kernel module is loaded (modprobe ee1004); "
             "DDR5 uses a different driver (spd5118) and is not supported here.")
        sys.exit(1)

    print("=== Installed modules ===")
    usable = []
    mod_rows = []
    for m in modules:
        b, crc = m["base"], m["crc"]
        crc_str = "OK" if crc["base_ok"] else "FAIL"
        mod_rows.append([m["slot"], b["part"], b["ranks"], f"x{b['width']}",
                          b["density"], f"{b['freq_max']:.0f}", crc_str])
        if crc["base_ok"] or include_invalid:
            usable.append(m)
        else:
            warn(f"excluding {m['slot']} from match table (CRC mismatch); "
                 f"pass --include-invalid to force its inclusion")
    print_table(["Slot", "Part Number", "Rank", "Width", "Density", "MaxMTs", "CRC"], mod_rows)

    if not usable:
        warn("No module passed CRC validation -- refusing to compute a match table "
             "from data that may be corrupt. Re-run with --include-invalid to override "
             "(not recommended).")
        sys.exit(3)

    worst, source = worst_case(usable)
    guaranteed = min(m["base"]["freq_max"] for m in usable)

    print(f"\nGuaranteed-for-all ceiling (no OC on any module): {guaranteed:.0f} MT/s")

    print("\n=== Worst-case (governing) requirement per timing ===")
    print_table(["Param", "ns", "Source"],
                [[p, f"{worst[p]:.3f}", source[p]] for p in BASE_PARAMS])

    print("\n=== Match table: cycles needed to satisfy ALL modules, per candidate MT/s ===")
    print_table(["Param"] + [str(c) for c in freqs],
                [[p] + [math.ceil(round(worst[p] / (2000.0 / c), 6)) for c in freqs]
                 for p in BASE_PARAMS])

    print("\n=== Embedded XMP profiles (informational only, not used in match table) ===")
    xmp_rows = []
    no_xmp = []
    for m in modules:
        x = m["xmp"]
        unverified = "" if m["crc"]["base_ok"] else "*"
        if not x:
            no_xmp.append(m["slot"] + unverified)
            continue
        for name, label in (("profile1", "P1"), ("profile2", "P2")):
            pr = x[name]
            if pr:
                xmp_rows.append([m["slot"] + unverified, label, f"{pr['freq']:.0f}",
                                  pr["CL"], pr["tRCD"], pr["tRP"], pr["tRAS"], pr["tRC"],
                                  pr["tRFC1"], pr["voltage"]])
    if xmp_rows:
        print_table(["Slot", "Prof", "MT/s", "CL", "RCD", "RP", "RAS", "RC",
                      "RFC1", "Volt"], xmp_rows)
        print("(FAW/RRD_S/RRD_L omitted for width -- see --json for the full set)")
    else:
        print("(none found on any module)")
    if no_xmp:
        print(f"No embedded XMP profile: {', '.join(no_xmp)}")
    if any(s.endswith("*") for s in no_xmp) or any(r[0].endswith("*") for r in xmp_rows):
        print("(* = base CRC failed on this module; XMP data shown is unverified)")

    print("\nReminder: this is a read-only calculation, not a stability guarantee.")
    print("Validate the resulting profile with a full Memtest86 pass before trusting it.")


# --- Selftest -----------------------------------------------------------
# Fixture captured mechanically (xxd -p) from a real Kingston KF3200C16D4/16GX
# module's eeprom sysfs file, not hand-transcribed, to avoid exactly the kind
# of copy error a manual hex trace is prone to. Expected values below were
# independently cross-checked against `decode-dimms` output and the module's
# published Kingston spec (DDR4-3200 CL16-18-18-36 @ 1.35V) before being
# hard-coded here, so this is a genuine correctness check, not a tautology.
_FIXTURE_HEX = (
    "23110c028521000800600003090300000000070df80f00006e6e6e11006ef00a"
    "2008000500a81b2828007800143c0000000000000000000000000000152b1636"
    "0b2b0c36000036152b0c2c162b0c000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000009cb500000000e7d66cf5"
    "1111410100000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000abc6"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "019804225103e966e34b463332303043313644342f313647582020202000830b"
    "4600000000000000000000004600008807393730353331370000000100000000"
    "0c4a17200000000000a3000005fc0f0000505a5a10b46ef00a2008000500a823"
    "2d0000000000000000000000000000000000000000000000a3000006fc0f0000"
    "505b5b10bf6ef00a2008000500a826260000000000000000a8a800cbcbf6ac00"
    "0000000000000000000000000000000000000000000000000000000000000000"
)


def _fixture_bytes():
    hexstr = _FIXTURE_HEX.replace("\n", "")
    data = bytes.fromhex(hexstr)
    if len(data) != SPD_SIZE:
        raise SPDError(f"selftest fixture is {len(data)} bytes, expected {SPD_SIZE} "
                        f"(this indicates a bug in the fixture itself, not your hardware)")
    return data


def selftest():
    checks = []

    def check(name, actual, expected):
        ok = actual == expected
        checks.append((name, ok, actual, expected))

    d = _fixture_bytes()
    m = parse_module(d, "selftest-fixture")

    check("CRC base block valid", m["crc"]["base_ok"], True)
    check("CRC module block valid", m["crc"]["module_ok"], True)
    check("CRC base computed", f"{m['crc']['base_calc']:04X}", "F56C")
    check("CRC module computed", f"{m['crc']['module_calc']:04X}", "C6AB")

    b = m["base"]
    check("part number", b["part"], "KF3200C16D4/16GX")
    check("ranks", b["ranks"], 2)
    check("width", b["width"], 8)
    check("density", b["density"], "8Gb")
    check("tCKmin", b["tCKmin"], 0.833)
    check("CL (ns)", b["CL"], 13.75)
    check("tRCD (ns)", b["tRCD"], 13.75)
    check("tRP (ns)", b["tRP"], 13.75)
    check("tRAS (ns)", b["tRAS"], 32.0)
    check("tRC (ns)", b["tRC"], 45.75)
    check("tRFC1 (ns)", b["tRFC1"], 350.0)
    check("tRFC2 (ns)", b["tRFC2"], 260.0)
    check("tRFC4 (ns)", b["tRFC4"], 160.0)
    check("tFAW (ns)", b["tFAW"], 21.0)
    check("tRRDS (ns)", b["tRRDS"], 3.3)
    check("tRRDL (ns)", b["tRRDL"], 4.9)

    x = m["xmp"]
    check("xmp present", x is not None, True)
    if x:
        p1, p2 = x["profile1"], x["profile2"]
        check("xmp version", x["version"], "2.0")
        check("profile1 freq", p1["freq"], 3200.0)
        check("profile1 CL-RCD-RP-RAS", (p1["CL"], p1["tRCD"], p1["tRP"], p1["tRAS"]), (16, 18, 18, 36))
        check("profile1 voltage", p1["voltage"], 1.35)
        check("profile1 tRFC1", p1["tRFC1"], 560)
        check("profile2 freq", p2["freq"], 3003.0)
        check("profile2 CL-RCD-RP-RAS", (p2["CL"], p2["tRCD"], p2["tRP"], p2["tRAS"]), (15, 17, 17, 36))
        check("profile2 voltage", p2["voltage"], 1.35)

    failed = 0
    for name, ok, actual, expected in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"  [{status}] {name}" + ("" if ok else f"  (got {actual!r}, expected {expected!r})"))

    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return failed == 0


def parse_freqs(raw):
    out = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            v = int(tok)
        except ValueError:
            raise SPDError(f"'{tok}' is not a valid integer MT/s value")
        if v <= 0:
            raise SPDError(f"frequency must be positive, got {v}")
        out.append(v)
    if not out:
        raise SPDError("no frequencies given")
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--freqs", default="2400,2666,2933,3000,3200",
                     help="comma-separated candidate MT/s values (default: %(default)s)")
    ap.add_argument("--json", action="store_true",
                     help="dump raw decoded data as JSON instead of the text report")
    ap.add_argument("--include-invalid", action="store_true",
                     help="include CRC-failed modules in the match table anyway (not recommended)")
    ap.add_argument("--selftest", action="store_true",
                     help="run the built-in correctness check against a known-good fixture and exit")
    ap.add_argument("--debug", action="store_true",
                     help="show full Python tracebacks instead of short error messages")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)

    try:
        modules = discover_modules(warn=lambda m: print(m, file=sys.stderr))

        if args.json:
            json.dump([{"slot": m["slot"], "base": m["base"], "xmp": m["xmp"],
                        "crc": m["crc"]} for m in modules], sys.stdout, indent=2)
            print()
            return

        freqs = parse_freqs(args.freqs)
        print_report(modules, freqs, args.include_invalid,
                     warn=lambda m: print(m, file=sys.stderr))
    except SPDError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception:
        if args.debug:
            raise
        print("error: unexpected failure -- rerun with --debug for the full traceback",
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
