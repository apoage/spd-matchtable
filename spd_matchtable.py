#!/usr/bin/env python3
"""Byte-accurate DDR4 SPD decoder and cross-module timing match table.

Reads raw SPD EEPROM data straight from the ee1004 kernel driver's sysfs
binary attribute, or from offline dumps passed as file arguments (no
dmidecode/decode-dimms dependency either way), decodes the base JEDEC
timing block per installed module, and computes the worst-case (governing)
timing every installed module needs satisfied simultaneously at a set of
candidate frequencies -- i.e. a safe manual BIOS starting point for a
mixed-kit system, derived independently of any single module's embedded
XMP profile. Also flags module-type (RDIMM/UDIMM) and ECC mismatches, which
are the most common reasons a mixed kit fails to POST at all.

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
import os
import re
import sys
import tempfile
import textwrap

__version__ = "1.1.0"

MTB = 0.125  # ns per medium-timebase tick
FTB = 0.001  # ns per fine-timebase tick

DENSITY_MAP = ["256Mb", "512Mb", "1Gb", "2Gb", "4Gb", "8Gb", "16Gb", "32Gb", "12Gb", "24Gb"]
DENSITY_GB = [0.25, 0.5, 1, 2, 4, 8, 16, 32, 12, 24]  # numeric Gb, parallel to DENSITY_MAP
DEV_WIDTH = [4, 8, 16, 32, None, None, None, None]
PKG_RANKS = [1, 2, 3, 4, 5, 6, 7, 8]

MODULE_TYPE_NAME = {
    0: "Extended", 1: "RDIMM", 2: "UDIMM", 3: "SO-DIMM", 4: "LRDIMM",
    5: "Mini-RDIMM", 6: "Mini-UDIMM", 8: "72b-SO-RDIMM", 9: "72b-SO-UDIMM",
    10: "16b-SO-DIMM", 11: "32b-SO-DIMM",
}
MODULE_TYPE_FAMILY = {
    1: "registered", 4: "registered", 5: "registered", 8: "registered",
    2: "unbuffered", 3: "unbuffered", 6: "unbuffered", 9: "unbuffered",
    10: "unbuffered", 11: "unbuffered",
}

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


DDR4_TYPE = 0x0C
STANDARD_TIMEBASE = 0x00  # byte 0x11: only combination JEDEC defines (MTB=125ps, FTB=1ps)
TCKMIN_SANE_RANGE = (0.3, 3.0)  # ns; covers ~666-6666 MT/s, comfortably beyond real DDR4 bins
FREQ_SANE_RANGE = (800, 8000)  # MT/s; a --freqs value outside this is almost certainly a typo

# JEDEC publishes tCKmin to 3 decimal places (e.g. DDR4-2400's is "0.833 ns"),
# which is itself already a rounding of the true repeating fraction (5/6 ns).
# Re-deriving frequency as 2000/tCKmin from that rounded value lands on
# 2400.96, not 2400 -- a display artifact, not a different real speed bin.
# Snapping to the nearest standard bin (only within a tight tolerance, so an
# actually non-standard tCKmin still shows its own plain value) keeps the
# ceiling line, the match table's own frequency columns, and the cheat-sheet
# math all consistent with each other instead of off by a fractional cycle.
JEDEC_SPEED_BINS = [1600, 1866, 2133, 2400, 2666, 2933, 3200, 3466, 3733, 4000, 4266, 4400]


def snap_to_jedec_bin(freq, tolerance=15):
    nearest = min(JEDEC_SPEED_BINS, key=lambda b: abs(b - freq))
    return nearest if abs(nearest - freq) <= tolerance else round(freq)


def _nonneg(name, ns):
    if ns < 0:
        raise SPDError(f"{name} computed as negative ({ns} ns) -- SPD data looks corrupt/crafted")
    return ns


def parse_base(d):
    if d[0x02] != DDR4_TYPE:
        raise SPDError(f"not a DDR4 SPD (memory type byte = 0x{d[0x02]:02X}, expected 0x{DDR4_TYPE:02X})")
    if d[0x11] != STANDARD_TIMEBASE:
        raise SPDError(f"unsupported SPD timebase encoding (byte 0x11 = 0x{d[0x11]:02X}); "
                        f"this decoder assumes the standard MTB=125ps/FTB=1ps JEDEC combination")

    banks = d[0x04]
    org = d[0x0C]
    tCKmin = d[0x12] * MTB + s8(d[0x7D]) * FTB
    tCKmax = d[0x13] * MTB + s8(d[0x7C]) * FTB

    lo, hi = TCKMIN_SANE_RANGE
    if not (lo <= tCKmin <= hi):
        raise SPDError(f"tCKmin={tCKmin}ns is outside the sane range {TCKMIN_SANE_RANGE} -- "
                        f"SPD data looks blank/corrupt")

    rasRC = d[0x1B]
    ras = ((rasRC & 0x0F) << 8) | d[0x1C]
    rc = ((rasRC & 0xF0) << 4) | d[0x1D]
    rfc1 = (d[0x1F] << 8) | d[0x1E]
    rfc2 = (d[0x21] << 8) | d[0x20]
    rfc4 = (d[0x23] << 8) | d[0x22]
    faw = ((d[0x24] & 0x0F) << 8) | d[0x25]

    density_idx = banks & 0x0F
    density = DENSITY_MAP[density_idx] if density_idx < len(DENSITY_MAP) else "?"
    density_gb = DENSITY_GB[density_idx] if density_idx < len(DENSITY_GB) else None

    width_idx = org & 0x7
    ranks_idx = (org >> 3) & 0x7
    width = DEV_WIDTH[width_idx]
    ranks = PKG_RANKS[ranks_idx] if ranks_idx < len(PKG_RANKS) else None

    capacity_gb = None
    if density_gb is not None and width and ranks:
        # Standard non-ECC monolithic-DIMM capacity formula, verified against
        # this project's own 3 known reference capacities (16GB/16GB/8GB)
        # before being trusted: density_Gb * (64/width) * ranks / 8.
        capacity_gb = density_gb * (64 / width) * ranks / 8

    module_type_code = d[0x03] & 0x0F
    ecc = bool((d[0x0D] >> 3) & 0x3)

    part = sanitize_text(d[0x149:0x15D].decode("ascii", "replace").replace("\x00", ""))
    mfg_year, mfg_week = d[0x143], d[0x144]
    serial = d[0x145:0x149].hex().upper()

    return {
        "part": part or "(unreadable)",
        "mfgDate": f"20{mfg_year:02x}-W{mfg_week:02x}",
        "serial": serial,
        "density": density,
        "capacityGB": capacity_gb,
        "ranks": ranks,
        "width": width,
        "moduleTypeCode": module_type_code,
        "moduleType": MODULE_TYPE_NAME.get(module_type_code, f"unknown(0x{module_type_code:02X})"),
        "moduleFamily": MODULE_TYPE_FAMILY.get(module_type_code, "other"),
        "ecc": ecc,
        "tCKmin": round(tCKmin, 4),
        "tCKmax": round(tCKmax, 4),
        "freq_max": snap_to_jedec_bin(2000 / tCKmin),
        "CL": round(_nonneg("CL", d[0x18] * MTB + s8(d[0x7B]) * FTB), 4),
        "tRCD": round(_nonneg("tRCD", d[0x19] * MTB + s8(d[0x7A]) * FTB), 4),
        "tRP": round(_nonneg("tRP", d[0x1A] * MTB + s8(d[0x79]) * FTB), 4),
        "tRAS": round(_nonneg("tRAS", ras * MTB), 4),
        "tRC": round(_nonneg("tRC", rc * MTB + s8(d[0x78]) * FTB), 4),
        "tRFC1": round(_nonneg("tRFC1", rfc1 * MTB), 4),
        "tRFC2": round(_nonneg("tRFC2", rfc2 * MTB), 4),
        "tRFC4": round(_nonneg("tRFC4", rfc4 * MTB), 4),
        "tFAW": round(_nonneg("tFAW", faw * MTB), 4),
        "tRRDS": round(_nonneg("tRRDS", d[0x26] * MTB + s8(d[0x77]) * FTB), 4),
        "tRRDL": round(_nonneg("tRRDL", d[0x27] * MTB + s8(d[0x76]) * FTB), 4),
        "tCCDL": round(_nonneg("tCCDL", d[0x28] * MTB + s8(d[0x75]) * FTB), 4),
        "tWR": round(_nonneg("tWR", d[0x2A] * MTB), 4),
        "tWTRS": round(_nonneg("tWTRS", d[0x2C] * MTB), 4),
        "tWTRL": round(_nonneg("tWTRL", d[0x2D] * MTB), 4),
    }


def parse_xmp_profile(p):
    voltage_raw = p[0]
    voltage = ((voltage_raw & 0x80) >> 7) * 100 + (voltage_raw & 0x7F)
    tCK = p[3] * MTB + s8(p[38]) * FTB
    lo, hi = TCKMIN_SANE_RANGE
    if not (lo <= tCK <= hi):
        return None  # a bogus/corrupt profile is treated as absent, not fatal to the module

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

    values_ns = [cl, rcd, rp, ras * MTB, rc * MTB, rfc1 * MTB, faw * MTB, rrds, rrdl]
    if any(v < 0 for v in values_ns):
        return None  # same corrupt-data guard as the base block, just non-fatal here

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


def natural_sort_key(slot):
    """'1-0050' before '2-0050' before '10-0050': split into digit/non-digit
    runs so numeric parts compare numerically, not lexically."""
    return [int(part) if part.isdigit() else part
            for part in re.split(r"(\d+)", slot)]


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
    modules.sort(key=lambda m: natural_sort_key(m["slot"]))
    return modules


def load_modules_from_files(paths, warn=print):
    """Offline mode: decode raw SPD dumps taken earlier (e.g. one DIMM at a
    time in another machine, or `cat .../eeprom > stick.bin` on this one) --
    the scenario the README is written for, where the live system may only
    boot with a subset of sticks installed."""
    modules = []
    for path in paths:
        try:
            with open(path, "rb") as f:
                d = f.read()
        except OSError as e:
            warn(f"warning: could not read {path}: {e}")
            continue
        slot = os.path.basename(path)
        try:
            modules.append(parse_module(d, slot))
        except SPDError as e:
            warn(f"warning: {slot}: {e} -- skipping this file")
    modules.sort(key=lambda m: natural_sort_key(m["slot"]))
    return modules


def compatibility_warnings(modules):
    """Loud, cheap, high-value checks: these are the #1/#2 reasons a mixed
    kit refuses to POST at all, not just fails to hit a target speed."""
    warnings = []

    families = {m["slot"]: m["base"]["moduleFamily"] for m in modules}
    distinct = set(families.values()) - {"other"}
    if len(distinct) > 1:
        detail = ", ".join(f"{slot}={fam}" for slot, fam in families.items())
        warnings.append(
            "MODULE TYPE MISMATCH: mixing registered (RDIMM/LRDIMM) and "
            f"unbuffered (UDIMM) modules will not POST -- {detail}")

    ecc = {m["slot"]: m["base"]["ecc"] for m in modules}
    if len(set(ecc.values())) > 1:
        detail = ", ".join(f"{slot}={'ECC' if v else 'non-ECC'}" for slot, v in ecc.items())
        warnings.append(
            "ECC MISMATCH: most boards require all-ECC or all-non-ECC "
            f"populated -- {detail}")

    return warnings


def worst_case(modules):
    """Independent per-parameter maxima, EXCEPT tRC: same-bank ACT->ACT must
    cover the full ACT-[tRAS]->PRE-[tRP]->ACT chain, so tRC is a protocol
    floor of tRAS+tRP, not just a value some module happens to declare.
    A module's own SPD can (and in practice does) list tRC < its own
    tRAS+tRP -- real vendor data, not a decoding bug -- so this floor has
    to be enforced explicitly rather than trusted to fall out of the
    per-module numbers. Taking system-wide worst-tRAS + worst-tRP as the
    floor is a safe upper bound: since each is independently >= any single
    module's own value, their sum is >= that module's own tRAS+tRP too."""
    worst, source = {}, {}
    for p in BASE_PARAMS:
        best_v, best_s = -1, None
        for m in modules:
            v = m["base"][p]
            if v > best_v:
                best_v, best_s = v, m["slot"]
        worst[p], source[p] = best_v, best_s

    floor = worst["tRAS"] + worst["tRP"]
    if floor > worst["tRC"]:
        worst["tRC"] = floor
        if source["tRAS"] == source["tRP"]:
            source["tRC"] = f"{source['tRAS']} (tRAS+tRP floor)"
        else:
            source["tRC"] = f"{source['tRAS']}+{source['tRP']} (tRAS+tRP floor)"

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


def _column_widths(headers, rows):
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    return widths


def _paginate_columns(headers, rows, max_width):
    """Split columns into left-to-right pages that each fit max_width,
    always repeating column 0 (the row label) on every page. Enforces the
    80-column claim unconditionally instead of only holding for the
    default --freqs list."""
    widths = _column_widths(headers, rows)

    def page_width(idxs):
        return sum(widths[i] + 3 for i in idxs) + 1

    pages, i, n = [], 1, len(headers)
    if n <= 1:
        return [[0]]
    while i < n:
        page = [0]
        while i < n:
            if page_width(page + [i]) > max_width and len(page) > 1:
                break
            page.append(i)
            i += 1
        pages.append(page)
    return pages


def print_table(headers, rows, max_width=None):
    max_width = MAX_TERM_WIDTH if max_width is None else max_width
    pages = _paginate_columns(headers, rows, max_width)
    for n, idxs in enumerate(pages):
        if len(pages) > 1:
            print(f"-- columns {n + 1}/{len(pages)} --")
        page_headers = [headers[i] for i in idxs]
        page_rows = [[row[i] for i in idxs] for row in rows]
        for line in render_table(page_headers, page_rows):
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
        cap = f"{b['capacityGB']:.0f}GB" if b["capacityGB"] is not None else "?"
        mod_rows.append([m["slot"], b["part"], cap, b["ranks"], f"x{b['width']}",
                          b["density"], b["moduleType"], "Y" if b["ecc"] else "N",
                          f"{b['freq_max']:.0f}", crc_str, b["serial"], b["mfgDate"]])
        if crc["base_ok"] or include_invalid:
            usable.append(m)
        else:
            warn(f"excluding {m['slot']} from match table (CRC mismatch); "
                 f"pass --include-invalid to force its inclusion")
    print_table(["Slot", "Part Number", "Capacity", "Rank", "Width", "Density",
                 "Type", "ECC", "MaxMTs", "CRC", "Serial", "MfgDate"], mod_rows)

    for w in compatibility_warnings(modules):
        print(f"!! {w}")

    if not usable:
        warn("No module passed CRC validation -- refusing to compute a match table "
             "from data that may be corrupt. Re-run with --include-invalid to override "
             "(not recommended).")
        sys.exit(3)

    worst, source = worst_case(usable)
    guaranteed = min(m["base"]["freq_max"] for m in usable)
    limiters = [m["slot"] for m in usable if m["base"]["freq_max"] == guaranteed]

    print()
    for line in textwrap.wrap(f"Base SPD ceiling: {guaranteed:.0f} MT/s "
                               f"(limited by {', '.join(limiters)})", MAX_TERM_WIDTH):
        print(line)
    print("Note: this is what every module's own spec independently allows, not a")
    print("guarantee this board's memory controller trains reliably at that speed.")

    print("\n=== Worst-case (governing) requirement per timing ===")
    print_table(["Param", "ns", "Source"],
                [[p, f"{worst[p]:.3f}", source[p]] for p in BASE_PARAMS])

    lo_sane, hi_sane = FREQ_SANE_RANGE
    min_tckmax = min(m["base"]["tCKmax"] for m in usable)
    for f in freqs:
        if not (lo_sane <= f <= hi_sane):
            warn(f"'{f}' MT/s is outside the realistic DDR4 range {FREQ_SANE_RANGE} "
                 f"-- check for a typo")
        if 2000.0 / f > min_tckmax:
            warn(f"{f} MT/s is below at least one module's minimum supported speed "
                 f"(tCKmax exceeded) -- unusually slow, verify this is intentional")

    print("\n=== Match table: cycles needed to satisfy ALL modules, per candidate MT/s ===")
    print_table(["Param"] + [str(c) for c in freqs],
                [[p] + [math.ceil(round(worst[p] / (2000.0 / c), 6)) for c in freqs]
                 for p in BASE_PARAMS])
    invalid_included = [m["slot"] for m in usable if not m["crc"]["base_ok"]]
    if invalid_included:
        print(f"WARNING: table above includes CRC-failed module(s): "
              f"{', '.join(invalid_included)} -- numbers may be corrupt.")

    print("\n=== OC required beyond each module's own base JEDEC spec ===")
    for f in freqs:
        oc = [m["slot"] for m in usable if f > m["base"]["freq_max"]]
        text = f"{f} MT/s: " + (", ".join(oc) if oc else "none (native for all modules)")
        for line in textwrap.wrap(text, MAX_TERM_WIDTH - 2):
            print(f"  {line}")
    print("(a module with an embedded XMP profile above near this speed suggests")
    print(" its own OC voltage; a module with none has no vendor-declared OC target)")

    print("\n=== Suggested starting point ===")
    tck = 2000.0 / guaranteed

    def cyc(p):
        return math.ceil(round(worst[p] / tck, 6))

    spd_line = (f"@ {guaranteed:.0f} MT/s (from SPD, zero OC required): "
                f"CL{cyc('CL')}-{cyc('tRCD')}-{cyc('tRP')}-{cyc('tRAS')} "
                f"tRC{cyc('tRC')} tRFC1={cyc('tRFC1')} tFAW={cyc('tFAW')} "
                f"tRRD_S/L={cyc('tRRDS')}/{cyc('tRRDL')} tCCD_L={cyc('tCCDL')} "
                f"tWR={cyc('tWR')} tWTR_S/L={cyc('tWTRS')}/{cyc('tWTRL')} @ 1.20V")
    for line in textwrap.wrap(spd_line, MAX_TERM_WIDTH):
        print(line)

    two_dpc_or_multirank = len(usable) > 2 or any((m["base"]["ranks"] or 0) > 1 for m in usable)
    inferred_line = (
        f"Inferred, NOT from SPD -- generic starting points, verify in BIOS: "
        f"tCWL={cyc('CL') - 1} tRTP~={math.ceil(round(7.5 / tck, 6))} tCCD_S=4 "
        f"Command Rate={'2T' if two_dpc_or_multirank else '1T (Auto)'}")
    for line in textwrap.wrap(inferred_line, MAX_TERM_WIDTH):
        print(line)

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
                                  pr["tRFC1"], pr["tFAW"], pr["tRRDS"], pr["tRRDL"],
                                  pr["voltage"]])
    if xmp_rows:
        print_table(["Slot", "Prof", "MT/s", "CL", "RCD", "RP", "RAS", "RC",
                      "RFC1", "FAW", "RRDS", "RRDL", "Volt"], xmp_rows)
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
    check("freq_max snaps to JEDEC bin (2400, not the raw 2400.96 artifact)",
          b["freq_max"], 2400)
    check("capacity_gb formula", b["capacityGB"], 16.0)
    check("module type decoded as UDIMM", b["moduleType"], "UDIMM")
    check("ecc decoded false for consumer UDIMM", b["ecc"], False)
    check("serial matches decode-dimms cross-check", b["serial"], "03E966E3")
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

    # --- Failure-path checks --------------------------------------------
    # The happy path above proves the decoder is accurate; these prove it
    # actually refuses bad input instead of confidently decoding garbage.
    # Mutations are CRC-repaired so each test isolates ONE guard at a time
    # -- e.g. a real-world "wrong EEPROM wired to ee1004" has a perfectly
    # valid CRC over its own (wrong-format) data.

    def repaired(offset, value):
        b = bytearray(d)
        b[offset] = value
        crc1 = crc16(bytes(b[0:0x7E]))
        b[0x7E], b[0x7F] = crc1 & 0xFF, (crc1 >> 8) & 0xFF
        return bytes(b)

    def expect_rejected(name, data, needle):
        try:
            parse_module(data, "selftest-mutant")
            check(name, "no exception raised", f"SPDError containing {needle!r}")
        except SPDError as e:
            check(name, needle in str(e), True)

    expect_rejected("rejects non-DDR4 memory type byte",
                     repaired(0x02, 0x0B), "not a DDR4 SPD")
    expect_rejected("rejects non-standard timebase byte",
                     repaired(0x11, 0x01), "timebase encoding")

    try:
        _nonneg("test", -1.0)
        check("_nonneg rejects negative ns", "no exception raised", "SPDError")
    except SPDError:
        check("_nonneg rejects negative ns", True, True)
    check("_nonneg passes through non-negative", _nonneg("test", 0.0), 0.0)

    # Synthetic two-module case matching the real bug this was written to
    # catch: module A has the highest declared tRC but NOT the highest
    # tRAS/tRP; module B has the highest tRAS+tRP but a lower (real vendor
    # data) declared tRC. A naive independent-max would under-report tRC.
    fake_modules = [
        {"slot": "A", "base": {p: 20.0 for p in BASE_PARAMS}},
        {"slot": "B", "base": {p: 20.0 for p in BASE_PARAMS}},
    ]
    fake_modules[0]["base"].update(tRAS=32.0, tRP=13.75, tRC=45.75)
    fake_modules[1]["base"].update(tRAS=32.375, tRP=14.0, tRC=44.5)
    w, src = worst_case(fake_modules)
    check("tRC floor applied when a module's own tRC < its tRAS+tRP",
          w["tRC"], round(32.375 + 14.0, 4))
    check("tRC floor source is annotated, not silently substituted",
          "floor" in src["tRC"], True)

    check("natural_sort_key orders 2-0050 before 10-0050",
          sorted(["10-0050", "2-0050", "1-0050"], key=natural_sort_key),
          ["1-0050", "2-0050", "10-0050"])

    rdimm_udimm = [
        {"slot": "A", "base": {"moduleFamily": "unbuffered", "ecc": False}},
        {"slot": "B", "base": {"moduleFamily": "registered", "ecc": False}},
    ]
    warnings = compatibility_warnings(rdimm_udimm)
    check("detects RDIMM/UDIMM mismatch", any("MODULE TYPE MISMATCH" in w for w in warnings), True)

    ecc_mismatch = [
        {"slot": "A", "base": {"moduleFamily": "unbuffered", "ecc": True}},
        {"slot": "B", "base": {"moduleFamily": "unbuffered", "ecc": False}},
    ]
    warnings = compatibility_warnings(ecc_mismatch)
    check("detects ECC/non-ECC mismatch", any("ECC MISMATCH" in w for w in warnings), True)

    matched_kit = [
        {"slot": "A", "base": {"moduleFamily": "unbuffered", "ecc": False}},
        {"slot": "B", "base": {"moduleFamily": "unbuffered", "ecc": False}},
    ]
    check("no false-positive warning on a matched kit",
          compatibility_warnings(matched_kit), [])

    with tempfile.NamedTemporaryFile(suffix=".bin") as tf:
        tf.write(d)
        tf.flush()
        file_modules = load_modules_from_files([tf.name])
        check("offline file input decodes identically to live parse_module",
              file_modules[0]["base"]["part"] if file_modules else None,
              "KF3200C16D4/16GX")

    for bad, why in [("abc", "not a valid integer"), ("0", "must be positive"), ("", "no frequencies")]:
        try:
            parse_freqs(bad)
            check(f"parse_freqs rejects {bad!r}", "no exception raised", why)
        except SPDError as e:
            check(f"parse_freqs rejects {bad!r}", why in str(e), True)
    check("parse_freqs dedupes and sorts", parse_freqs("3200,2400,3200"), [2400, 3200])

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
    ap.add_argument("spd_files", nargs="*", metavar="FILE",
                     help="decode raw SPD dump(s) instead of live sysfs -- each must be "
                          "exactly 512 bytes, e.g. produced with "
                          "`cat /sys/bus/i2c/drivers/ee1004/1-0050/eeprom > stick.bin`. "
                          "Lets you combine dumps taken one module at a time, or analyze "
                          "dumps from another machine.")
    ap.add_argument("--freqs", default="2400,2666,2933,3000,3200",
                     help="comma-separated candidate MT/s values (default: %(default)s)")
    ap.add_argument("--json", action="store_true",
                     help="dump decoded data as JSON instead of the text report, including "
                          "the same computed worst-case timings and match table the text "
                          "report shows (not just the raw per-module decode)")
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

    warn = lambda m: print(m, file=sys.stderr)  # noqa: E731

    try:
        if args.spd_files:
            modules = load_modules_from_files(args.spd_files, warn=warn)
        else:
            modules = discover_modules(warn=warn)

        freqs = parse_freqs(args.freqs)

        if args.json:
            usable = [m for m in modules if m["crc"]["base_ok"] or args.include_invalid]
            worst, source = worst_case(usable) if usable else ({}, {})
            match_table = {
                str(c): {p: math.ceil(round(worst[p] / (2000.0 / c), 6)) for p in BASE_PARAMS}
                for c in freqs
            } if usable else {}
            json.dump({
                "modules": [{"slot": m["slot"], "base": m["base"], "xmp": m["xmp"],
                             "crc": m["crc"]} for m in modules],
                "worstCaseNs": worst,
                "worstCaseSource": source,
                "matchTableCycles": match_table,
                "compatibilityWarnings": compatibility_warnings(modules),
            }, sys.stdout, indent=2)
            print()
            return

        print_report(modules, freqs, args.include_invalid, warn=warn)
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
