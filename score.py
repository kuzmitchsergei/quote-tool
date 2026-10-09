"""
Test: did compare.py find every anomaly planted in the demo data?

Usage (after running extract.py and compare.py on both demo quotes):
  python3 score.py
"""
import pandas as pd

DATA = "demo_data"
key = pd.read_csv(f"{DATA}/answer_key.csv")
findings = {s: pd.read_csv(f"{DATA}/new_quote_OEM_{s}_2026_findings.csv") for s in ("Alpha", "Beta")}

# what each kind of planted anomaly should be flagged as
EXPECTED = {
    "spike": "ABOVE INDEX",
    "small_item_spike": "ABOVE INDEX",
    "labour_above_index": "ABOVE INDEX",
    "spike_with_claim": "ABOVE INDEX",
    "decrease": "DECREASE",
    "part_supersession": "PART REPLACED",
    "new_fee": "NEW CHARGE",
    "new_percentage_surcharge": "NEW CHARGE",
    "blanket_increase_above_index": "BLANKET INCREASE",
}

found = 0
for _, row in key.iterrows():
    f = findings[row["supplier"]]
    expected = EXPECTED[row["anomaly_type"]]
    if row["part_no"] == "ALL PARTS":
        hit = (f["flag"] == expected).sum() >= 5
    else:
        part = row["part_no"].split("->")[-1].strip()
        hit = (f.loc[f["part_no"] == part, "flag"] == expected).any()
    found += hit
    print(f"{'FOUND ' if hit else 'MISSED'}  {row['supplier']:<6} {row['part_no']:<22} {row['anomaly_type']}")

# false alarms: lines flagged as a problem that were not planted
planted = {p.split("->")[-1].strip() for p in key["part_no"]}
false_alarms = []
for s, f in findings.items():
    problems = f[f["flag"].isin(["ABOVE INDEX", "PART REPLACED", "NEW CHARGE"])]
    false_alarms += [f"{s} {p}" for p in problems["part_no"] if p not in planted]

print(f"\nFound {found} of {len(key)} planted anomalies. False alarms: {len(false_alarms)}")
for fa in false_alarms:
    print("  false alarm:", fa)
