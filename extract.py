"""
Step 1 of the quotation analysis tool: read a supplier quote PDF.

What it does
  1. Python (pdfplumber) reads the price table: part no., description, qty, prices.
     -> no AI here, so every number is exactly what is printed in the PDF.
  2. Python checks that the line totals add up to the quote total.
  3. The LOCAL AI model (Ollama) reads the cover letter and pulls out:
     supplier, quote number, date, and every reason the supplier gives for price changes.
     -> nothing leaves your computer.

Usage (in Terminal, inside the quote-tool folder):
  python3 extract.py data/new_quote_OEM_Alpha_2026.pdf

Output (written next to the PDF):
  <name>_lines.csv   the price table
  <name>_meta.json   supplier info + the claimed reasons
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pdfplumber
from ollama import chat
from pydantic import BaseModel

MODEL = "gemma4:e2b"   # the local model you downloaded with "ollama pull"


# ------------------------------------------------------------------ 1. table
def to_number(text):
    """Turn '5,173.76' into 5173.76."""
    return float(text.replace(",", "").strip())


def read_table(pdf_path):
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    if not row or row[0] in (None, "", "Pos"):
                        continue  # skip header and empty rows
                    pos, part_no, desc, unit, qty, unit_price, total = row[:7]
                    rows.append({
                        "pos": int(pos),
                        "part_no": part_no.strip(),
                        "description": " ".join(desc.split()),  # remove line breaks
                        "unit": unit,
                        "qty": to_number(qty),
                        "unit_price_eur": to_number(unit_price),
                        "total_eur": to_number(total),
                    })
    return pd.DataFrame(rows), full_text


# ----------------------------------------------------------- 2. sanity check
def check_total(lines, full_text):
    match = re.search(r"Total net\s+([\d,]+\.\d{2})", full_text)
    if not match:
        return "WARNING: no 'Total net' found in the PDF - check the quote manually."
    printed = to_number(match.group(1))
    calculated = round(lines["total_eur"].sum(), 2)
    if abs(printed - calculated) > 0.05:
        return f"WARNING: lines add up to {calculated:,.2f} but the PDF says {printed:,.2f}."
    return f"OK: {len(lines)} lines, total EUR {calculated:,.2f} matches the PDF."


# ------------------------------------------------------- 3. AI on cover letter
class Reason(BaseModel):
    reason: str               # e.g. "energy and raw material costs"
    applies_to: str           # e.g. "all spare parts" or "B-5563"
    claimed_increase_pct: float | None   # only if the supplier states a number


class QuoteInfo(BaseModel):
    supplier: str
    quote_number: str
    quote_date: str
    stated_reasons: list[Reason]
    new_charges_mentioned: list[str]   # fees/surcharges the letter says are new


PROMPT = """You read supplier quotations for a procurement department.
From the quotation text below, extract:
- supplier name, quote number, quote date
- every reason the supplier gives for price changes, what it applies to
  (a part number or 'all spare parts'), and the percentage only if it is written in the text
- any fees or surcharges the text says are new
Use only information written in the text. Do not guess. If something is not stated, leave it empty.

QUOTATION TEXT:
"""


def read_cover_letter(full_text):
    letter = full_text.split("Pos Part no.")[0]   # the part above the price table
    response = chat(
        model=MODEL,
        messages=[{"role": "user", "content": PROMPT + letter}],
        format=QuoteInfo.model_json_schema(),
        options={"temperature": 0},   # same input -> same answer
    )
    return QuoteInfo.model_validate_json(response.message.content)


# --------------------------------------------------------------------- main
if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 extract.py <quote.pdf>")
    pdf_path = Path(sys.argv[1])

    lines, text = read_table(pdf_path)
    print(lines.to_string(index=False))
    print("\n" + check_total(lines, text))

    print("\nAsking the local AI to read the cover letter...")
    info = read_cover_letter(text)
    print(json.dumps(info.model_dump(), indent=2))

    out = pdf_path.with_suffix("")
    lines.to_csv(f"{out}_lines.csv", index=False)
    Path(f"{out}_meta.json").write_text(json.dumps(info.model_dump(), indent=2))
    print(f"\nSaved {out.name}_lines.csv and {out.name}_meta.json")
