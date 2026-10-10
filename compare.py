"""
Step 2 of the quotation analysis tool: compare a new quote with the history.

Everything here is plain Python arithmetic - no AI - so every flag can be traced
back to a number in the history, the new quote or the benchmark index.

What it checks, line by line
  - change vs. the last price paid for the same part
  - change vs. the benchmark index (parts index, or labour index for service hours)
  - part numbers that replace an old part ("replaces A-xxxx") -> compared with the old part
  - lines that never appeared before (new fees / surcharges)
And for the quote as a whole
  - the typical (median) increase vs. the index
  - any percentage the supplier claims in the cover letter vs. the index

Usage (inside the quote_tool folder, after running extract.py on the same PDF):
  python3 compare.py demo_data/new_quote_OEM_Beta_2026.pdf

Output: <name>_findings.csv next to the PDF, plus a summary on screen.
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "demo_data"
TOLERANCE_PTS = 3.0   # flag when a price rises more than 3 %-points above the index


def load(pdf_path):
    base = pdf_path.with_suffix("")
    new = pd.read_csv(f"{base}_lines.csv")
    meta = json.loads(Path(f"{base}_meta.json").read_text())
    history = pd.read_csv(DATA / "quote_history.csv")
    index = pd.read_csv(DATA / "benchmark_index.csv").set_index("year")
    return new, meta, history, index


def find_supplier(new, history):
    """Pick the supplier by part numbers, not by the AI's reading of the name."""
    overlap = history[history["part_no"].isin(new["part_no"])]
    return overlap["supplier"].value_counts().idxmax()


def index_change_pct(index, part_no, year_from, year_to):
    column = "labour_cost_index" if part_no.startswith("SRV") else "machinery_parts_index"
    return (index.loc[year_to, column] / index.loc[year_from, column] - 1) * 100


def compare(new, meta, history, index):
    supplier = find_supplier(new, history)
    hist = history[history["supplier"] == supplier].copy()
    hist["year"] = hist["quote_date"].str[:4].astype(int)
    new_year = int(str(meta.get("quote_date", ""))[:4] or hist["year"].max() + 1)

    # last known price per part
    last = hist.sort_values("year").groupby("part_no").last()

    findings = []
    for _, line in new.iterrows():
        pn = line["part_no"]
        old_pn = pn
        note = ""

        if pn not in last.index:
            replaced = re.search(r"replaces\s+([A-Z0-9-]+)", line["description"])
            if replaced and replaced.group(1) in last.index:
                old_pn = replaced.group(1)
                note = f"New part number, replaces {old_pn}"
            else:
                findings.append({
                    "part_no": pn, "description": line["description"],
                    "old_price": None, "new_price": line["unit_price_eur"],
                    "change_pct": None, "index_pct": None, "above_index_pts": None,
                    "extra_cost_eur": round(line["total_eur"], 2),
                    "flag": "NEW CHARGE",
                    "note": "Not in any previous quote",
                })
                continue

        old_price = last.loc[old_pn, "unit_price_eur"]
        old_year = int(last.loc[old_pn, "year"])
        change = (line["unit_price_eur"] / old_price - 1) * 100
        idx = index_change_pct(index, old_pn, old_year, new_year)
        above = change - idx
        extra = (line["unit_price_eur"] - old_price * (1 + idx / 100)) * line["qty"]

        if note:
            flag = "PART REPLACED"
        elif change < 0:
            flag = "DECREASE"
        elif above > TOLERANCE_PTS:
            flag = "ABOVE INDEX"
        else:
            flag = "OK"

        findings.append({
            "part_no": pn, "description": line["description"],
            "old_price": round(old_price, 2), "new_price": line["unit_price_eur"],
            "change_pct": round(change, 1), "index_pct": round(idx, 1),
            "above_index_pts": round(above, 1),
            "extra_cost_eur": round(max(extra, 0), 2) if flag != "DECREASE" else 0.0,
            "flag": flag, "note": note,
        })

    df = pd.DataFrame(findings)
    return supplier, new_year, mark_blanket_increase(df)


def mark_blanket_increase(df):
    """If most parts rose by the same %, call it one uniform increase instead of
    many separate flags - so the real outliers stand out."""
    parts = df[df["part_no"].str.match(r"^[A-Z]-\d") & df["change_pct"].notna()]
    if parts.empty:
        return df
    median = parts["change_pct"].median()
    same = parts[(parts["change_pct"] - median).abs() <= 0.3]
    if len(same) >= len(parts) / 2 and (same["above_index_pts"] > TOLERANCE_PTS).all():
        df.loc[same.index, "flag"] = "UNIFORM INCREASE"
        df.loc[same.index, "note"] = f"Same +{median:.1f}% as most other parts"
    return df


def quote_level_checks(df, meta, index, new_year):
    """Checks on the quote as a whole."""
    parts = df[df["part_no"].str.match(r"^[A-Z]-\d") & df["change_pct"].notna()]
    median = parts["change_pct"].median()
    parts_idx = (index.loc[new_year, "machinery_parts_index"]
                 / index.loc[new_year - 1, "machinery_parts_index"] - 1) * 100
    lines = [f"Typical (median) change on spare parts: {median:+.1f}% "
             f"vs. parts index {parts_idx:+.1f}%"]
    if median - parts_idx > TOLERANCE_PTS:
        lines.append("  -> FLAG: broad increase across most parts, well above the index")

    for r in meta.get("stated_reasons", []):
        pct = r.get("claimed_increase_pct")
        if pct is not None and pct - parts_idx > TOLERANCE_PTS:
            lines.append(f"  -> FLAG: supplier claims +{pct:.1f}% ('{r['reason']}'), "
                         f"index shows only {parts_idx:+.1f}%")
    return lines


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 compare.py <quote.pdf>  (run extract.py on it first)")
    pdf_path = Path(sys.argv[1])

    new, meta, history, index = load(pdf_path)
    supplier, year, df = compare(new, meta, history, index)

    pd.set_option("display.width", 200)
    print(f"Supplier: {supplier}   Quote year: {year}\n")
    blanket = df[df["flag"] == "UNIFORM INCREASE"]
    if not blanket.empty:
        print(f"UNIFORM INCREASE: {len(blanket)} parts all up {blanket['change_pct'].median():+.1f}% "
              f"(index {blanket['index_pct'].median():+.1f}%), "
              f"extra cost EUR {blanket['extra_cost_eur'].sum():,.2f}\n")
    flagged = df[~df["flag"].isin(["OK", "UNIFORM INCREASE"])]
    print(flagged[["part_no", "old_price", "new_price", "change_pct", "index_pct",
                   "extra_cost_eur", "flag", "note"]].to_string(index=False))
    print()
    for line in quote_level_checks(df, meta, index, year):
        print(line)
    extra = df.loc[~df["flag"].isin(["OK", "DECREASE"]), "extra_cost_eur"].sum()
    print(f"\nCost above index (unit prices + new charges): EUR {extra:,.2f}")

    out = f"{pdf_path.with_suffix('')}_findings.csv"
    df.to_csv(out, index=False)
    print(f"Saved {Path(out).name}")
