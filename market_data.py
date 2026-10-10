"""
Getting real market data into the tool - without the tool ever going online.

Route 1 (main):  import the World Bank "Pink Sheet" monthly prices file
                 (CMO-Historical-Data-Monthly.xlsx, free, updated monthly at
                 worldbank.org/en/research/commodity-markets).
Route 2 (gaps):  for series the World Bank does not publish (e.g. steel), the tool writes a
                 data request that contains ONLY public information (commodity names, months,
                 file format). The buyer pastes it into any external AI or gives it to a colleague,
                 pastes the answer back, and the tool checks it before using it.

Files written (folder market_data/, next to demo_data/):
  world_bank.csv     monthly prices from the World Bank file   (month, commodity columns)
  extra_series.csv   series added through route 2
  sources.json       where each series came from
"""
import io
import json
import re
from datetime import date
from pathlib import Path

import pandas as pd

FOLDER = Path(__file__).parent / "market_data"

# World Bank column header (lower case, "contains")  ->  tool commodity key.
# First match wins, so the preferred series is listed first.
WB_COLUMNS = [
    ("crude oil, brent", "brent"),
    ("aluminum", "aluminium"),
    ("copper", "copper"),
    ("nickel", "nickel"),
    ("iron ore", "iron_ore"),
    ("rubber, tsr20", "natural_rubber"),
    ("rubber, rss3", "natural_rubber"),
    ("natural gas, europe", "gas_europe"),
]
WB_DATE = re.compile(r"^\s*(\d{4})M(\d{2})\s*$")


# ------------------------------------------------------------- route 1
def import_world_bank(file):
    """Read the World Bank monthly workbook (path or uploaded file object).
    Finds the header row and date column by content, not by fixed position.
    Returns (DataFrame indexed by 'YYYY-MM', list of (commodity, World Bank column name))."""
    sheets = pd.read_excel(file, sheet_name=None, header=None)
    for name, raw in sheets.items():
        header_row = None
        for r in range(min(len(raw), 30)):
            cells = [str(c).strip().lower() for c in raw.iloc[r].tolist()]
            if any("copper" in c for c in cells) and any("brent" in c for c in cells):
                header_row = r
                break
        if header_row is None:
            continue
        headers = [" ".join(str(c).split()).lower() for c in raw.iloc[header_row].tolist()]
        # the date column: the column with the most values like 1960M01
        date_col = max(range(raw.shape[1]),
                       key=lambda c: raw.iloc[header_row:, c].astype(str).str.match(WB_DATE).sum())
        picked, used = {}, []
        for pattern, key in WB_COLUMNS:
            if key in picked:
                continue
            for c, h in enumerate(headers):
                if pattern in h:
                    picked[key] = c
                    used.append((key, " ".join(str(raw.iloc[header_row, c]).split())))
                    break
        rows = []
        for r in range(header_row + 1, len(raw)):
            m = WB_DATE.match(str(raw.iloc[r, date_col]))
            if not m:
                continue
            row = {"month": f"{m.group(1)}-{m.group(2)}"}
            for key, c in picked.items():
                row[key] = pd.to_numeric(raw.iloc[r, c], errors="coerce")   # '..' -> NaN
            rows.append(row)
        if rows and picked:
            df = pd.DataFrame(rows).set_index("month").sort_index()
            return df, used
    raise ValueError("Could not find the monthly price table. Please use the World Bank file "
                     "'CMO-Historical-Data-Monthly.xlsx' (Pink Sheet, monthly prices).")


def save_world_bank(df, used, filename):
    FOLDER.mkdir(exist_ok=True)
    df.to_csv(FOLDER / "world_bank.csv")
    sources = _load_sources()
    for key, column in used:
        sources[key] = {"source": f"World Bank Pink Sheet, column '{column}'", "file": filename,
                        "imported": date.today().isoformat(), "last_month": df[key].last_valid_index()}
    _save_sources(sources)


# ------------------------------------------------------------- combined view
def load_real_prices():
    """World Bank data plus any extra series. None if nothing has been imported yet."""
    frames = [pd.read_csv(FOLDER / f, index_col="month")
              for f in ("world_bank.csv", "extra_series.csv") if (FOLDER / f).exists()]
    if not frames:
        return None
    combined = frames[0]
    for extra in frames[1:]:
        combined = extra.combine_first(combined)        # pasted series win where both exist
    return combined.sort_index().astype(float)


def sources():
    return _load_sources()


def _load_sources():
    p = FOLDER / "sources.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _save_sources(data):
    FOLDER.mkdir(exist_ok=True)
    (FOLDER / "sources.json").write_text(json.dumps(data, indent=2))


# ------------------------------------------------------------- what is missing
def months_needed(quote_date):
    """The 24 full months before the quote month (12 recent + 12 for comparison)."""
    q = pd.Period(str(quote_date)[:7], freq="M")
    return [str(q - k) for k in range(24, 0, -1)]


def missing(prices, commodities, quote_date):
    """Commodities with no data, or gaps, in the 24 months the checks need."""
    need = months_needed(quote_date)
    out = []
    for key in sorted(set(commodities)):
        if prices is None or key not in prices.columns:
            out.append(key)
            continue
        series = prices[key].reindex(need)
        if series.isna().any():
            out.append(key)
    return out


# ------------------------------------------------------------- route 2
DESCRIPTIONS = {
    "steel": "Steel - a published monthly price or price index for Europe, e.g. a hot-rolled coil "
             "price or a producer price index for basic iron and steel (Eurostat or national statistics)",
    "copper": "Copper - LME grade A cash price, monthly average",
    "aluminium": "Aluminium - LME primary aluminium cash price, monthly average",
    "nickel": "Nickel - LME cash price, monthly average",
    "brent": "Crude oil, Brent - monthly average spot price",
    "natural_rubber": "Natural rubber - TSR20 or RSS3, monthly average price",
    "iron_ore": "Iron ore - 62% Fe fines, monthly average price",
    "gas_europe": "Natural gas, Europe (TTF) - monthly average price",
}


def data_request(commodities, quote_date):
    """A request containing only public information - no supplier, part, price or company name."""
    need = months_needed(quote_date)
    cols = ",".join(["month"] + list(commodities))
    lines = "\n".join(f"- {c}: {DESCRIPTIONS.get(c, c.replace('_', ' '))}" for c in commodities)
    return f"""Please find official published monthly data for the following series:
{lines}

Period: {need[0]} to {need[-1]} (one value per month, {len(need)} months).

Rules:
1. Use only official or primary sources (statistics offices, exchanges, international
   organisations). Look the figures up - do not estimate, interpolate or invent values.
2. If a month is not available, leave the value empty. Do not fill gaps.
3. A price index is fine instead of a price, as long as it is the same series for all months.
4. Answer in exactly this format and nothing else:

DATA
{cols}
{need[0]},<value>
...
{need[-1]},<value>

SOURCES
<series>: <name of the series and publisher>, <URL>
"""


def check_pasted(text, commodities, quote_date):
    """Validate a pasted answer before it is used. Returns (DataFrame or None, errors, warnings)."""
    errors, warnings = [], []
    need = months_needed(quote_date)

    rows = [l.strip() for l in text.splitlines() if re.match(r"^\s*\d{4}-\d{2}\s*,", l)]
    header = next((l.strip() for l in text.splitlines() if l.strip().lower().startswith("month,")), None)
    if not header or not rows:
        return None, ["No data table found. The answer must contain a 'month,...' line followed by "
                      "lines like '2024-03,123.4'."], []
    try:
        df = pd.read_csv(io.StringIO("\n".join([header] + rows)), dtype={"month": str})
    except Exception as e:
        return None, [f"The data table could not be read ({e})."], []
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.set_index("month")

    wanted = [c for c in commodities if c in df.columns]
    for c in commodities:
        if c not in df.columns:
            errors.append(f"Column '{c}' is missing.")
    df = df[wanted].apply(pd.to_numeric, errors="coerce")

    missing_months = [m for m in need if m not in df.index]
    if missing_months:
        errors.append(f"{len(missing_months)} of the {len(need)} months are missing "
                      f"(e.g. {', '.join(missing_months[:3])}).")
    for c in wanted:
        s = df[c].reindex(need)
        if s.isna().any():
            errors.append(f"{c}: {int(s.isna().sum())} month(s) without a value.")
        if (s.dropna() <= 0).any():
            errors.append(f"{c}: zero or negative values.")
        jumps = s.pct_change().abs()
        big = jumps[jumps > 0.30]
        if not big.empty:
            warnings.append(f"{c}: month-to-month change above 30% in {', '.join(big.index[:3])} - "
                            "please verify against the source.")

    src_block = text.split("SOURCES", 1)[1] if "SOURCES" in text else ""
    for c in wanted:
        line = next((l for l in src_block.splitlines() if l.strip().lower().startswith(c)), "")
        if "http" not in line:
            errors.append(f"{c}: no source URL given. Data without a source is not used.")
    warnings.append("AI answers can contain wrong figures even with a source. Spot-check a few months "
                    "against the source before relying on the result.")
    if errors:
        return None, errors, warnings
    return df.reindex(need), errors, warnings


def save_extra(df, text):
    FOLDER.mkdir(exist_ok=True)
    path = FOLDER / "extra_series.csv"
    if path.exists():
        old = pd.read_csv(path, index_col="month")
        df = df.combine_first(old)
    df.sort_index().to_csv(path, index_label="month")
    sources = _load_sources()
    src_block = text.split("SOURCES", 1)[1] if "SOURCES" in text else ""
    for c in df.columns:
        line = next((l.strip() for l in src_block.splitlines() if l.strip().lower().startswith(c)), "")
        sources[c] = {"source": line or "pasted data", "imported": date.today().isoformat(),
                      "last_month": df[c].last_valid_index()}
    _save_sources(sources)


def extra_series():
    """Names of the series added through route 2 (pasted data)."""
    path = FOLDER / "extra_series.csv"
    return list(pd.read_csv(path, index_col="month").columns) if path.exists() else []


def remove_extra():
    """Take the pasted series out of use. Nothing is deleted: the file is moved to
    market_data/removed/ with a time stamp, so it can be restored by moving it back."""
    path = FOLDER / "extra_series.csv"
    if not path.exists():
        return None
    removed = FOLDER / "removed"
    removed.mkdir(exist_ok=True)
    from datetime import datetime
    target = removed / f"extra_series_{datetime.now():%Y%m%d_%H%M%S}.csv"
    names = extra_series()
    path.rename(target)
    sources = _load_sources()
    for n in names:
        if "World Bank" not in str(sources.get(n, {}).get("source", "")):
            sources.pop(n, None)
    _save_sources(sources)
    return target
