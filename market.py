"""
Market data checks for supplier claims like
  "copper rose 18% on the world market"   or   "our sub-supplier raised prices 15%".

All arithmetic is plain Python on two local files - nothing is looked up online:
  demo_data/market_prices.csv    monthly commodity prices (SYNTHETIC in the demo; for real use,
                                 import published series, e.g. World Bank monthly commodity prices)
  demo_data/material_shares.csv  the buyer's assumptions: main cost material per part and its share

The three questions asked for every material claim:
  1. Is the claimed figure right?     -> 12-month average change vs. the claim (and the peak month)
  2. Is it the right material?        -> claimed commodity vs. what the part is actually made of
  3. How much does it really justify? -> pass-through: share x material change + rest x general index
"""
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "demo_data"
TOLERANCE_PTS = 3.0
GAP_PTS = 1.0

COMMODITY_LABELS = {"copper": "copper", "aluminium": "aluminium", "steel": "steel",
                    "natural_rubber": "natural rubber", "brent": "crude oil (Brent)",
                    "nickel": "nickel", "iron_ore": "iron ore", "gas_europe": "natural gas (Europe)"}
# words a supplier might use -> commodity series; checked in this order
KEYWORDS = [("natural gas", "gas_europe"), ("nickel", "nickel"), ("iron ore", "iron_ore"),
            ("natural rubber", "natural_rubber"), ("synthetic rubber", "brent"), ("epdm", "brent"),
            ("copper", "copper"), ("alumin", "aluminium"), ("steel", "steel"),
            ("oil", "brent"), ("crude", "brent"), ("petro", "brent"), ("plastic", "brent"),
            ("pvc", "brent"), ("polymer", "brent"), ("rubber", "natural_rubber")]


def pct(x):
    return f"{x:+.1f}%"


def lower_first(text):
    """'Copper windings' -> 'copper windings', but keep acronyms: 'PVC layer' stays."""
    return text[0].lower() + text[1:] if len(text) > 1 and text[1].islower() else text


def sentences(name, issues):
    """'G-7101 (...): First issue. Second issue.' - each issue becomes its own sentence."""
    return f"{name}: " + " ".join(i[0].upper() + i[1:] + "." for i in issues)


def demo_prices():
    prices = pd.read_csv(DATA / "market_prices.csv")
    return prices[prices["month"] != "note"].set_index("month").astype(float)


def load_context(quote_date, shares=None, prices=None):
    """Everything the checks need. `shares` and `prices` can be passed in (e.g. edited or
    imported in the app); by default the synthetic demo files are used."""
    if prices is None:
        prices = demo_prices()
    if shares is None:
        shares = pd.read_csv(DATA / "material_shares.csv")
    return {"quote_date": str(quote_date)[:7], "prices": prices,
            "shares": shares.set_index("part_no")}


def commodity_of(text):
    text = (text or "").lower()
    for word, key in KEYWORDS:
        if word in text:
            return key
    return None


def market_change(prices, commodity, quote_month):
    """12-month average before the quote vs. the 12 months before that, plus the peak monthly
    year-on-year change in the last 12 months (suppliers like to quote the peak).
    Uses explicit calendar months, so gaps in the data are detected, never skipped.
    Returns (None, None) if any of the 24 months is missing."""
    q = pd.Period(str(quote_month)[:7], freq="M")
    need = [str(q - k) for k in range(24, 0, -1)]
    if commodity not in prices.columns:
        return None, None
    s = prices[commodity].reindex(need)
    if s.isna().any():
        return None, None
    earlier, recent = s.iloc[:12].values, s.iloc[12:].values
    avg = (recent.mean() / earlier.mean() - 1) * 100
    peak = max((recent[k] / earlier[k] - 1) * 100 for k in range(12))
    return avg, peak


def check_material(item, claim, ctx, name):
    """Returns (verdict, email line, justified_pct) or None if we have no data for this part."""
    shares, prices = ctx["shares"], ctx["prices"]
    if item["id"] not in shares.index:
        return None
    part = shares.loc[item["id"]]
    key, share = part["material_key"], float(part["share_pct"]) / 100
    if key not in prices.columns:
        return None
    chg, idx = item["change"], item["index"]
    claimed_key = commodity_of(claim.material) or commodity_of(claim.summary)

    issues, verdicts = [], []
    part_avg, _ = market_change(prices, key, ctx["quote_date"])
    if part_avg is None:
        return None          # no complete data for this part's material -> generic rules apply

    # 1. right material?
    if claimed_key and claimed_key != key:
        verdicts.append("WRONG MATERIAL")
        issues.append(f"you refer to {COMMODITY_LABELS[claimed_key]} prices, but this part's main cost "
                      f"material is {lower_first(part['material'])}, which follows "
                      f"{COMMODITY_LABELS[key]}: {pct(part_avg)} over the last 12 months")

    # 2. right figure?
    check_key = claimed_key if claimed_key in prices.columns else key
    avg, peak = market_change(prices, check_key, ctx["quote_date"])
    if avg is None:
        check_key = key
        avg, peak = market_change(prices, key, ctx["quote_date"])
    if claim.claimed_pct is not None and claim.claimed_pct - avg > TOLERANCE_PTS:
        verdicts.append("NOT SUPPORTED BY MARKET DATA")
        if abs(claim.claimed_pct - peak) <= 2.0:
            issues.append(f"the {pct(claim.claimed_pct)} you cite matches the single peak month "
                          f"({pct(peak)}), but the 12-month average {COMMODITY_LABELS[check_key]} price "
                          f"rose only {pct(avg)}")
        else:
            issues.append(f"market data shows {COMMODITY_LABELS[check_key]} at {pct(avg)} over the last "
                          f"12 months, not {pct(claim.claimed_pct)}")

    # 3. pass-through: how much of the price increase can the material explain?
    justified = share * part_avg + (1 - share) * idx
    if chg - justified > GAP_PTS:
        if not verdicts:
            verdicts.append("PARTLY EXPLAINED")
        per_unit = item.get("old_price", float("nan")) * (chg - justified) / 100
        eur = f" (EUR {per_unit:,.2f} per unit)" if pd.notna(per_unit) else ""
        issues.append(f"with {lower_first(part['material'])} at about {share:.0%} of the part's cost, the "
                      f"actual material price change justifies about {pct(justified)} in total, while "
                      f"the quoted increase is {pct(chg)}{eur}. Please revise the price or provide a "
                      f"cost breakdown")

    if not issues:
        return ("SUPPORTED", f"{name}: thank you, your explanation is consistent with current market "
                f"data ({COMMODITY_LABELS[key]} {pct(part_avg)}).", justified)
    return " / ".join(verdicts), sentences(name, issues), justified


def check_sub_supplier(item, claim, ctx, name):
    """Sub-supplier claims cannot be verified directly - so ask for evidence, compute the
    pass-through, and point to direct sourcing for standard components.
    Once the evidence is provided, only the pass-through question remains."""
    shares = ctx["shares"]
    chg, idx = item["change"], item["index"]
    has_docs = claim.documents_attached
    issues = (["thank you for the sub-supplier's price notification"] if has_docs else
              ["please send us the sub-supplier's price notification (a redacted copy is sufficient)"])
    justified, gap = None, False
    if item["id"] in shares.index:
        part = shares.loc[item["id"]]
        share = float(part["share_pct"]) / 100
        if claim.claimed_pct is not None:
            justified = share * claim.claimed_pct + (1 - share) * idx
            gap = chg - justified > GAP_PTS
            if gap:
                issues.append(f"the bought-in component is about {share:.0%} of this part's cost, so "
                              f"{'the' if has_docs else 'even a'} {pct(claim.claimed_pct)} sub-supplier "
                              f"increase justifies about {pct(justified)}, not {pct(chg)}"
                              + (". Please revise the price accordingly" if has_docs else ""))
        if str(part.get("standard_component", "")).lower() == "yes" and not has_docs:
            issues.append("as this appears to be a standard catalogue component, please also state its "
                          "manufacturer and part number")
    elif not has_docs:
        issues.append("please state the share of the bought-in component in this part's cost")
    if not has_docs and claim.claimed_pct is not None and claim.claimed_pct - idx > TOLERANCE_PTS:
        issues.append(f"for comparison, the general price index for machine parts rose {pct(idx)}")

    if has_docs and not gap:
        return ("PLAUSIBLE - CHECK DOCUMENTS",
                sentences(name, issues + ["we will review it and come back to you if needed"]), justified)
    if has_docs:
        return "PARTLY EXPLAINED", sentences(name, issues), justified
    return "EVIDENCE REQUESTED", sentences(name, issues), justified
