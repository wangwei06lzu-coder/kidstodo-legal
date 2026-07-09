#!/usr/bin/env python3
"""Convert the operator's native maintenance sheet → the engine's baseline CSV.

────────────────────────────────────────────────────────────────────────────────
NATIVE format (what the operator maintains, e.g. `baseline_schedule - コピー.csv`)
  • 6 ID columns, BY POSITION:  次車 · 編成 · 番号 · set_id · cars · subtype
  • a fiscal-year BANNER row     (…, 2017年度, …, 2018年度, …)
  • a month-NUMBER row           (…, 4,5,6,7,8,9,10,11,12,1,2,3, 4,5,…)  ← Japanese FY, Apr→Mar
  • each month cell holds a running MILEAGE number (ignored) and/or an EVENT marker:
        全 → 全般検査 (GENERAL)   台 → 台車検査 (BOGIE)   新 → New   廃 → Retired
    Full words (全般検査 / 台車検査 / New / Retired) and notes like "廃車（…）" are also read,
    and a combined 全般 + 廃 in one cell becomes the engine's "全般検査/Retired" token.

ENGINE baseline format (what src/mso/ingest/baseline_parser.py reads)
  row 0   fiscal-year banner            (ignored by the parser)
  row 1   系列,編成,番号,set_id,cars,subtype, <ISO labels YYYY-MM …>
  row 2+  data — each month cell is a full-word token (全般検査/台車検査/New/Retired) or empty

WHAT THIS FIXES FOR YOU
  • Writes CORRECT consecutive ISO labels, so the "Mar-93 before Apr-94" mislabel disappears.
  • Drops the running mileage numbers; keeps only real events.
  • Reconstructs calendar months as a strict monthly sequence — never trusts FY label text.

USAGE
  python3 scripts/convert_native_to_baseline.py NATIVE.csv -o baseline_out.csv
  (add --verbose to see the detected structure, event counts and any unmapped cells)

Assumptions (override with the flags below if your file differs):
  • 6 metadata columns before the month grid            (--meta-cols)
  • one 年度 banner row and one month-number row         (auto-detected)
  • data rows have a set_id AND an integer car count     (used to find where data starts)
  • exactly 12 consecutive months per fiscal year, Apr→Mar, no gaps
Anything it cannot classify is left blank and reported, so nothing is silently invented.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys

META_COLS = 6          # 次車, 編成, 番号, set_id, cars, subtype
_STRIP = "0123456789.,%-  \t　"   # running number + separators (incl. full-width space)


def classify(cell: str, unmapped: dict) -> str:
    """One native cell → one engine token ('' = no event). Mileage-only cells return ''."""
    core = cell.strip().strip(_STRIP)
    if not core:                       # empty, or a pure running number
        return ""
    low = core.lower()
    general = ("全般" in core) or (core == "全")
    bogie   = ("台車" in core) or (core == "台")
    new     = ("新製" in core) or (core == "新") or (low == "new")
    retire  = ("廃車" in core) or (core == "廃") or (low == "retired")
    if general and retire:
        return "全般検査/Retired"       # combined token the parser knows
    if general:
        return "全般検査"
    if bogie:
        return "台車検査"
    if new:
        return "New"
    if retire:
        return "Retired"
    unmapped[core] = unmapped.get(core, 0) + 1   # residue we could not map → reported
    return ""


def _is_month_row(row: list, meta_cols: int) -> bool:
    vals = [c.strip() for c in row[meta_cols:] if c.strip()]
    return len(vals) >= 6 and all(v.isdigit() and 1 <= int(v) <= 12 for v in vals[:12])


def _is_data_row(row: list, meta_cols: int) -> bool:
    # data rows carry a set_id (col 3) and an integer car count (col 4)
    return len(row) > meta_cols and row[3].strip() != "" and row[4].strip().isdigit()


def gen_labels(fy0: int, month0: int, n: int) -> list:
    """n consecutive ISO 'YYYY-MM' labels starting at (fiscal year fy0, month month0)."""
    year = fy0 if month0 >= 4 else fy0 + 1      # Japanese FY: Jan–Mar belong to FY-1's calendar+1
    m = month0
    out = []
    for _ in range(n):
        out.append(f"{year:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            year += 1
    return out


def convert(path: str, meta_cols: int = META_COLS):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise SystemExit("empty file")

    # locate the fiscal-year banner and the month-number row
    banner_idx = next((i for i, r in enumerate(rows)
                       if any(re.search(r"\d{4}\s*年度", c) for c in r)), None)
    month_idx = next((i for i, r in enumerate(rows) if _is_month_row(r, meta_cols)), None)
    if banner_idx is None:
        raise SystemExit("could not find a 'YYYY年度' banner row — check --meta-cols / file layout")
    if month_idx is None:
        raise SystemExit("could not find the month-number row (…4,5,6,…) — check --meta-cols")

    fy0 = int(re.search(r"(\d{4})\s*年度", " ".join(rows[banner_idx])).group(1))
    month_cells = [c.strip() for c in rows[month_idx][meta_cols:] if c.strip()]
    month0 = int(month_cells[0])

    data_rows = [r for r in rows if _is_data_row(r, meta_cols)]
    if not data_rows:
        raise SystemExit("no data rows found (need a set_id in col 4 and an integer car count in col 5)")
    n_month = max(len(r) for r in rows) - meta_cols        # widest row governs the grid
    labels = gen_labels(fy0, month0, n_month)

    # build output
    header = ["系列", "編成", "番号", "set_id", "cars", "subtype"] + labels
    banner = [""] * meta_cols + [f"{int(l[:4]) if int(l[5:7]) >= 4 else int(l[:4]) - 1}年度"
                                 if l.endswith("-04") else "" for l in labels]
    out_rows = [banner, header]

    counts = {"全般検査": 0, "台車検査": 0, "New": 0, "Retired": 0, "全般検査/Retired": 0}
    unmapped: dict = {}
    for r in data_rows:
        meta = (list(r[:meta_cols]) + [""] * meta_cols)[:meta_cols]
        cells = list(r[meta_cols:meta_cols + n_month]) + [""] * (n_month - (len(r) - meta_cols))
        toks = [classify(c, unmapped) for c in cells[:n_month]]
        for t in toks:
            if t:
                counts[t] = counts.get(t, 0) + 1
        out_rows.append(meta + toks)

    summary = {
        "sets": len(data_rows), "month_cols": n_month,
        "first_month": labels[0], "last_month": labels[-1],
        "fy0": fy0, "month0": month0, "events": counts,
        "unmapped": unmapped, "banner_row": banner_idx, "month_row": month_idx,
    }
    return out_rows, summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert native maintenance sheet → engine baseline CSV")
    ap.add_argument("input", help="native CSV (the operator's wide 年度/month sheet)")
    ap.add_argument("-o", "--output", required=True, help="baseline CSV to write")
    ap.add_argument("--meta-cols", type=int, default=META_COLS, help="ID columns before the month grid (default 6)")
    ap.add_argument("--verbose", action="store_true", help="print detected structure and warnings")
    a = ap.parse_args(argv)

    out_rows, s = convert(a.input, a.meta_cols)
    with open(a.output, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(out_rows)

    print(f"✓ wrote {a.output}: {s['sets']} sets × {s['month_cols']} months "
          f"({s['first_month']} … {s['last_month']})")
    print(f"  events: " + ", ".join(f"{k}={v}" for k, v in s["events"].items() if v))
    if a.verbose:
        print(f"  detected: banner row #{s['banner_row']}, month row #{s['month_row']}, "
              f"start FY={s['fy0']} month={s['month0']}")
    if s["unmapped"]:
        top = sorted(s["unmapped"].items(), key=lambda kv: -kv[1])[:12]
        print(f"  ⚠ {len(s['unmapped'])} unmapped cell value(s) left blank — please review:", file=sys.stderr)
        for val, c in top:
            print(f"      {c:>5}×  {val!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
