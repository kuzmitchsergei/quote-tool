"""
Generate a fully FICTIONAL demo dataset for the quotation analysis prototype.

Nothing here is based on real supplier prices, part numbers or customers.
Suppliers: "OEM Alpha Druckmaschinen GmbH" (Germany, EUR) and
           "OEM Beta Precision AG" (Switzerland, quotes in EUR).
Customer:  "Demo State Printing Works".

Outputs (in ./data):
  quote_history.csv        - 2023-2025 annual spare-parts/service quotes (the "ERP history")
  benchmark_index.csv      - SYNTHETIC cost indices (placeholders, replace with real data later)
  new_quote_OEM_Alpha_2026.pdf
  new_quote_OEM_Beta_2026.pdf
  answer_key.csv           - the anomalies deliberately planted in the 2026 quotes
"""
import csv
import random
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

random.seed(42)
OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True)

# ---------------------------------------------------------------- catalogue
ALPHA_ITEMS = [
    ("A-10021", "Gripper bar assembly, feeder", "pc", 4850),
    ("A-10045", "Impression cylinder bearing set", "set", 3120),
    ("A-10078", "Ink fountain blade, intaglio", "pc", 685),
    ("A-10102", "Wiping roller sleeve", "pc", 2240),
    ("A-10133", "Wiping solution pump", "pc", 1960),
    ("A-10150", "Doctor blade holder", "pc", 890),
    ("A-10188", "Sheet transfer gripper pad (set of 20)", "set", 410),
    ("A-10204", "Vacuum belt, delivery", "pc", 1275),
    ("A-10219", "Servo motor, register control", "pc", 6340),
    ("A-10236", "Encoder, main drive", "pc", 1580),
    ("A-10251", "Safety light curtain", "pc", 2790),
    ("A-10270", "Pneumatic valve block", "pc", 1145),
    ("A-10288", "Chain link set, delivery", "set", 3460),
    ("A-10305", "Filter cartridge, wiping system", "pc", 96),
    ("A-10319", "Infeed sensor, sheet detection", "pc", 465),
    ("A-10333", "Control board, ink zone", "pc", 3890),
    ("A-10347", "Plate clamp kit", "kit", 720),
    ("A-10362", "Drive belt, numbering unit", "pc", 310),
    ("A-10378", "Numbering box, 7-digit", "pc", 1840),
    ("A-10391", "Lubrication pump, central", "pc", 2310),
    ("SRV-ALPHA-HR", "Service technician, per hour", "h", 165),
    ("SRV-ALPHA-REM", "Remote support, per hour", "h", 120),
]
BETA_ITEMS = [
    ("B-5501", "Blanket cylinder bearing", "pc", 2650),
    ("B-5514", "Dampening roller", "pc", 1190),
    ("B-5522", "Ink roller, rubber covered", "pc", 980),
    ("B-5538", "Feeder sucker head (set of 4)", "set", 540),
    ("B-5546", "Register cam assembly", "pc", 3720),
    ("B-5559", "Side lay sensor", "pc", 610),
    ("B-5563", "Main drive gear", "pc", 7480),
    ("B-5577", "Air pump, feeder", "pc", 2120),
    ("B-5584", "Inspection camera lens", "pc", 4290),
    ("B-5590", "LED light bar, inspection", "pc", 1730),
    ("B-5602", "Delivery pile plate", "pc", 1460),
    ("B-5615", "Gripper spring set", "set", 260),
    ("B-5629", "Touch panel, operator console", "pc", 5180),
    ("B-5633", "Coolant hose kit", "kit", 340),
    ("B-5648", "Sheet guide plate", "pc", 690),
    ("B-5651", "Cutting knife, inline", "pc", 1350),
    ("SRV-BETA-HR", "Service engineer, per hour", "h", 175),
    ("SRV-BETA-REM", "Remote diagnostics, per hour", "h", 130),
]

# typical quantities per year for a mid-size printing works
def qty_for(unit):
    return random.choice([1, 1, 2, 2, 4]) if unit != "h" else random.choice([40, 60, 80])


# --------------------------------------------------- synthetic benchmark index
# SYNTHETIC placeholder values (2023 = 100). To be replaced with real published
# producer price / labour cost indices before any real use.
BENCH = [
    # year, machinery_parts_index, labour_cost_index
    (2023, 100.0, 100.0),
    (2024, 102.6, 104.1),
    (2025, 104.9, 107.6),
    (2026, 107.1, 110.8),
]
with open(OUT / "benchmark_index.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["year", "machinery_parts_index", "labour_cost_index", "note"])
    for row in BENCH:
        w.writerow(list(row) + ["SYNTHETIC demo values - not real statistics"])

idx = {y: {"PARTS": parts, "LAB": lab} for y, parts, lab in BENCH}


# -------------------------------------------------------------- history 2023-25
history = []
prices = {}  # (oem, part) -> price per year

def yearly_step(oem, pn, year):
    """Normal price evolution: roughly follows the index, +/- small noise."""
    if pn.startswith("SRV"):
        base = idx[year]["LAB"] / idx[year - 1]["LAB"]
    else:
        base = idx[year]["PARTS"] / idx[year - 1]["PARTS"]
    return base * random.uniform(0.995, 1.012)

for oem, items, supplier in [("Alpha", ALPHA_ITEMS, "OEM Alpha Druckmaschinen GmbH"),
                             ("Beta", BETA_ITEMS, "OEM Beta Precision AG")]:
    for pn, desc, unit, p2023 in items:
        p = float(p2023)
        for year in (2023, 2024, 2025):
            if year > 2023:
                p *= yearly_step(oem, pn, year)
            prices[(oem, pn, year)] = round(p, 2)
            history.append({
                "supplier": supplier,
                "quote_no": f"Q-{oem[:2].upper()}-{year}-0{random.randint(10, 99)}",
                "quote_date": f"{year}-0{random.randint(2, 4)}-1{random.randint(0, 9)}",
                "part_no": pn,
                "description": desc,
                "unit": unit,
                "qty": qty_for(unit),
                "unit_price_eur": round(p, 2),
                "currency": "EUR",
            })

with open(OUT / "quote_history.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(history[0].keys()))
    w.writeheader()
    w.writerows(history)


# ------------------------------------------------- 2026 quotes with planted traps
answer_key = []

def normal_2026(oem, pn):
    return round(prices[(oem, pn, 2025)] * yearly_step(oem, pn, 2026), 2)

def key(oem, pn, kind, old, new, expl):
    answer_key.append({
        "supplier": oem, "part_no": pn, "anomaly_type": kind,
        "price_2025": old, "price_2026": new,
        "change_pct": "" if not old else round((new / old - 1) * 100, 1),
        "what_the_tool_should_find": expl,
    })

# ---- OEM Alpha
alpha_lines = []
alpha_special = {
    "A-10219": ("spike", 1.37, "Unexplained +37% on servo motor, far above index (~+2%)."),
    "A-10305": ("small_item_spike", 1.62, "Low-value consumable +62%. Easy to miss in total value, big % jump."),
    "SRV-ALPHA-HR": ("labour_above_index", 1.095, "Technician rate +9.5% vs labour index ~+3%."),
    "A-10391": ("decrease", 0.96, "Price went DOWN 4%. Tool must not flag as a problem (good news)."),
}
for pn, desc, unit, _ in ALPHA_ITEMS:
    old = prices[("Alpha", pn, 2025)]
    if pn == "A-10133":
        # supersession: old part replaced by a new part number at higher price
        new = round(old * 1.28, 2)
        alpha_lines.append(("A-10133-B", desc + " (replaces A-10133)", unit, 2, new))
        key("Alpha", "A-10133 -> A-10133-B", "part_supersession", old, new,
            "Old part number replaced by new one at +28%. Exact part-number matching misses it; "
            "tool must link via description / 'replaces' note and ask for technical justification.")
        continue
    if pn in alpha_special:
        kind, factor, expl = alpha_special[pn]
        new = round(old * factor, 2)
        key("Alpha", pn, kind, old, new, expl)
    else:
        new = normal_2026("Alpha", pn)
    alpha_lines.append((pn, desc, unit, qty_for(unit), new))

# new fees that did not exist before
alpha_fees = [
    ("FEE-TRAVEL", "Travel flat fee per service visit (new)", "visit", 3, 890.00),
    ("FEE-ENERGY", "Energy & logistics surcharge, 3.5% of parts value (new)", "lot", 1, None),
]
key("Alpha", "FEE-TRAVEL", "new_fee", 0, 890.00,
    "New line not present in any previous quote. Tool must flag new charges separately.")
key("Alpha", "FEE-ENERGY", "new_percentage_surcharge", 0, 0,
    "New 3.5% surcharge on total parts value. Effective increase hidden outside unit prices.")

# ---- OEM Beta
beta_lines = []
beta_special = {
    "B-5563": ("spike_with_claim", 1.15,
               "+15% on main drive gear. Supplier cover letter claims steel costs; "
               "machinery parts index only ~+2%. Justification weak."),
    "B-5584": ("spike", 1.22, "+22% on camera lens, no explanation given."),
}
for pn, desc, unit, _ in BETA_ITEMS:
    old = prices[("Beta", pn, 2025)]
    if pn in beta_special:
        kind, factor, expl = beta_special[pn]
        new = round(old * factor, 2)
        key("Beta", pn, kind, old, new, expl)
    elif pn.startswith("SRV"):
        new = normal_2026("Beta", pn)
    else:
        # blanket increase claimed in cover letter: +6% across the board
        new = round(old * 1.06, 2)
    beta_lines.append((pn, desc, unit, qty_for(unit), new))
key("Beta", "ALL PARTS", "blanket_increase_above_index", "", "",
    "Cover letter claims a general +6% on all parts 'due to energy and raw material costs'. "
    "Machinery parts index rose only ~+2.1% (104.9 -> 107.1), so ~4 points are not explained by the data.")

# ------------------------------------------------------------------ PDF writer
styles = getSampleStyleSheet()

def build_pdf(path, supplier, address, quote_no, date, cover_text, lines, fees=None):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    s = []
    s.append(Paragraph(f"<b>{supplier}</b>", styles["Title"]))
    s.append(Paragraph(address, styles["Normal"]))
    s.append(Spacer(1, 8))
    s.append(Paragraph("<b>QUOTATION</b> &nbsp; " + quote_no + " &nbsp; | &nbsp; Date: " + date, styles["Normal"]))
    s.append(Paragraph("Customer: Demo State Printing Works, Procurement Department", styles["Normal"]))
    s.append(Paragraph("Subject: Annual spare parts and service quotation 2026", styles["Normal"]))
    s.append(Spacer(1, 8))
    s.append(Paragraph(cover_text, styles["Normal"]))
    s.append(Spacer(1, 10))

    data = [["Pos", "Part no.", "Description", "Unit", "Qty", "Unit price EUR", "Total EUR"]]
    parts_total = 0.0
    for i, (pn, desc, unit, qty, price) in enumerate(lines, start=1):
        total = qty * price
        if not pn.startswith("SRV"):
            parts_total += total
        data.append([str(i * 10), pn, Paragraph(desc, styles["BodyText"]), unit, str(qty),
                     f"{price:,.2f}", f"{total:,.2f}"])
    grand = sum(q * p for _, _, _, q, p in lines)
    pos = len(lines)
    for pn, desc, unit, qty, price in (fees or []):
        pos += 1
        if price is None:  # percentage surcharge
            price = round(parts_total * 0.035, 2)
        total = qty * price
        grand += total
        data.append([str(pos * 10), pn, Paragraph(desc, styles["BodyText"]), unit, str(qty),
                     f"{price:,.2f}", f"{total:,.2f}"])
    data.append(["", "", "", "", "", "Total net", f"{grand:,.2f}"])

    t = Table(data, colWidths=[12 * mm, 26 * mm, 62 * mm, 12 * mm, 10 * mm, 26 * mm, 26 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b3a4a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -2), 0.25, colors.grey),
        ("ALIGN", (4, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    s.append(t)
    s.append(Spacer(1, 10))
    s.append(Paragraph("Prices ex works, excluding VAT. Validity: 90 days. Payment: 30 days net.",
                       styles["Normal"]))
    s.append(Paragraph("<i>FICTIONAL DEMO DOCUMENT - all companies, part numbers and prices are invented.</i>",
                       styles["Normal"]))
    doc.build(s)
    return grand, round(parts_total * 0.035, 2)

alpha_total, alpha_surcharge = build_pdf(
    OUT / "new_quote_OEM_Alpha_2026.pdf",
    "OEM Alpha Druckmaschinen GmbH", "Industriestrasse 12, 00000 Musterstadt, Germany",
    "Q-AL-2026-041", "2026-03-12",
    "Dear Sir or Madam, please find below our quotation for spare parts and service for 2026. "
    "Due to the general development of costs, some adjustments were necessary. "
    "We have additionally introduced a travel flat fee and an energy &amp; logistics surcharge "
    "to reflect current market conditions.",
    alpha_lines, alpha_fees)

beta_total, _ = build_pdf(
    OUT / "new_quote_OEM_Beta_2026.pdf",
    "OEM Beta Precision AG", "Musterweg 5, 0000 Beispielstadt, Switzerland",
    "Q-BE-2026-017", "2026-03-20",
    "Dear valued customer, thank you for your continued trust. Please note that due to "
    "increased energy and raw material costs, a general price adjustment of 6% on spare parts was "
    "unavoidable. In addition, the price of the main drive gear (B-5563) reflects the significant "
    "increase in special steel costs.",
    beta_lines)

for row in answer_key:
    if row["part_no"] == "FEE-ENERGY":
        row["price_2026"] = alpha_surcharge
with open(OUT / "answer_key.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(answer_key[0].keys()))
    w.writeheader()
    w.writerows(answer_key)

print(f"history rows: {len(history)}")
print(f"Alpha 2026 total: EUR {alpha_total:,.2f}   Beta 2026 total: EUR {beta_total:,.2f}")
print(f"planted anomalies: {len(answer_key)}")
