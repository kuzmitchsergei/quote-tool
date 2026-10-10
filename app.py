"""
Quote Check - web interface for the quotation analysis tool.

Runs entirely on this computer: the page is served at http://localhost:8501 and the AI model
runs in the local Ollama app. No quotation data is sent anywhere.

Start it (inside the quote-tool folder, with .venv active):
  streamlit run app.py
"""
import tempfile
from pathlib import Path

import ollama
import pandas as pd
import streamlit as st

import analyze_reply as ar
import market
import market_data as md
import compare as cmp
import draft_email as de
import extract as ex

DATA = Path(__file__).parent / "demo_data"
DEMO_QUOTES = {
    "OEM Alpha 2026 (demo)": ("new_quote_OEM_Alpha_2026.pdf", "reply_OEM_Alpha_2026.txt"),
    "OEM Beta 2026 (demo)": ("new_quote_OEM_Beta_2026.pdf", "reply_OEM_Beta_2026.txt"),
    "OEM Gamma 2026 (demo: material & sub-supplier claims)": ("new_quote_OEM_Gamma_2026.pdf",
                                                              "reply_OEM_Gamma_2026.txt"),
}
# status colours: (background, text) - used only on the status cell, always dark text
RED, ORANGE, YELLOW, GREEN, BLUE = (("#fde8e8", "#9b1c1c"), ("#feecdc", "#8a3c0b"),
                                    ("#fdf6b2", "#723b13"), ("#def7ec", "#03543f"),
                                    ("#e1effe", "#1e429f"))
FLAG_COLORS = {"ABOVE INDEX": RED, "PART REPLACED": ORANGE, "NEW CHARGE": ORANGE,
               "UNIFORM INCREASE": YELLOW, "DECREASE": GREEN}
VERDICT_COLORS = {"NO ANSWER": RED, "UNSUBSTANTIATED": RED, "ABOVE INDEX": RED,
                  "WRONG MATERIAL": RED, "NOT SUPPORTED": RED, "EVIDENCE REQUESTED": ORANGE,
                  "PARTLY EXPLAINED": ORANGE, "DOCUMENTS MISSING": ORANGE,
                  "CONCESSION OFFERED": BLUE, "SUPPORTED": GREEN, "PLAUSIBLE": GREEN}

COLUMNS = {"part_no": "Part no.", "description": "Description", "old_price": "Last price",
           "new_price": "New price", "change_pct": "Change", "index_pct": "Index",
           "above_index_pts": "Above index (pts)", "extra_cost_eur": "Extra cost",
           "flag": "Status", "note": "Note", "item": "Item", "verdict": "Verdict",
           "category": "Category", "claimed_pct": "Claimed by supplier", "price_change_pct": "Price change", "reading": "Reading",
           "justified_pct": "Justified", "pos": "Pos", "unit": "Unit",
           "qty": "Qty", "unit_price_eur": "Unit price", "total_eur": "Total"}

st.set_page_config(page_title="Quote Check", page_icon="🔍", layout="wide")
ss = st.session_state

st.markdown("""
<style>
  .block-container {padding-top: 2rem; max-width: 1200px;}
  h1 {font-weight: 700; letter-spacing: -0.5px;}
  h2 {font-size: 1.35rem !important; margin-top: 1.6rem; padding-top: 1rem;
      border-top: 1px solid #e5e7eb;}
  [data-testid="stMetric"] {background: #f4f6f9; border: 1px solid #e5e7eb;
      border-radius: 8px; padding: 12px 16px;}
  [data-testid="stMetricValue"] {font-size: 1.5rem;}
</style>""", unsafe_allow_html=True)


def styled(df, status_col=None, colors=None):
    """Readable table: status right after the first column, clear names, formatted numbers,
    blank instead of 'None', colour only on the status cell (always with dark text)."""
    df = df.drop(columns=[c for c in ("note",) if c in df.columns]).copy()
    if status_col in df.columns:
        first = df.columns[0]
        df = df[[first, status_col] + [c for c in df.columns if c not in (first, status_col)]]

    def fmt(x, spec):
        return "" if pd.isna(x) else format(x, spec)

    for c in ("old_price", "new_price", "extra_cost_eur", "unit_price_eur", "total_eur"):
        if c in df.columns:
            df[c] = df[c].map(lambda x: fmt(x, ",.2f"))
    for c in ("change_pct", "index_pct", "claimed_pct", "price_change_pct", "justified_pct"):
        if c in df.columns:
            df[c] = df[c].map(lambda x: "" if pd.isna(x) else f"{x:+.1f}%")
    if "above_index_pts" in df.columns:
        df["above_index_pts"] = df["above_index_pts"].map(lambda x: fmt(x, "+.1f"))
    if "category" in df.columns:
        df["category"] = (df["category"].fillna("").str.replace("sub_supplier", "sub-supplier")
                          .str.replace("_", " ").str.capitalize())
    df = df.fillna("").rename(columns=COLUMNS | {"supplier says": "Supplier says"})
    status = COLUMNS.get(status_col, status_col)

    def cell(value):
        for key, (bg, fg) in (colors or {}).items():
            if key in str(value):
                return f"background-color: {bg}; color: {fg}; font-weight: 600"
        return ""

    out = df.style
    if status in df.columns:
        out = out.map(cell, subset=[status])
    return out


def reset_from(step):
    """Clear results of this step and all later steps."""
    order = ["lines", "findings", "email", "reply_results", "claims", "frame", "asked_items",
             "corrections"]
    for key in order[order.index(step):]:
        ss.pop(key, None)


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### 🔒 Runs locally")
    try:
        models = [m.model for m in ollama.list().models]
        if any(m.startswith(ex.MODEL.split(":")[0]) for m in models):
            st.success(f"Local AI ready: {ex.MODEL}")
        else:
            st.warning(f"Ollama runs, but {ex.MODEL} is not installed. Run: ollama pull {ex.MODEL}")
    except Exception:
        st.error("Ollama is not running. Open the Ollama app and reload this page.")
    st.caption("The page runs at localhost and the model runs in Ollama on this computer. "
               "No quotation data leaves the machine.")
    st.divider()
    st.markdown("**Price history**")
    st.caption(f"{DATA.name}/quote_history.csv\n\n{DATA.name}/benchmark_index.csv\n\n"
               "Quote history and cost index are synthetic demo data.")
    st.divider()
    st.markdown("**Market data**")
    real = md.load_real_prices()
    source = st.radio("Use for the checks", ["Demo data (synthetic)", "Real data (imported)"],
                      index=0, disabled=real is None,
                      help=None if real is not None else "Import the World Bank file below first.")
    use_real = source.startswith("Real") and real is not None
    with st.expander("Import World Bank prices"):
        st.caption("Free monthly file 'CMO-Historical-Data-Monthly.xlsx' (Pink Sheet, monthly prices) "
                   "from worldbank.org/en/research/commodity-markets - updated monthly. Download it "
                   "with your browser, then drop it here. The tool itself never goes online.")
        wb_file = st.file_uploader("World Bank monthly prices (.xlsx)", type="xlsx",
                                   label_visibility="collapsed")
        if wb_file is not None and ss.get("wb_imported") != wb_file.name + str(wb_file.size):
            try:
                df, used = md.import_world_bank(wb_file)
                md.save_world_bank(df, used, wb_file.name)
                ss.wb_imported = wb_file.name + str(wb_file.size)
                ss.wb_message = (f"Imported {len(used)} series up to "
                                 f"{df.dropna(how='all').index[-1]}: "
                                 + ", ".join(market.COMMODITY_LABELS.get(k, k) for k, _ in used))
                st.rerun()
            except Exception as e:
                st.error(str(e))
        if ss.get("wb_message"):
            st.success(ss.wb_message)
    added = md.extra_series()
    if added:
        names = ", ".join(market.COMMODITY_LABELS.get(n, n) for n in added)
        st.caption(f"Added manually: {names}")
        if st.button("Remove added series", help="Moves them to market_data/removed/ - nothing is "
                                                 "deleted, you can restore them by moving the file back."):
            moved = md.remove_extra()
            ss.pop("frame", None)
            ss.wb_message = None
            st.toast(f"Removed {names}. Backup: market_data/removed/{moved.name}")
            st.rerun()
    st.divider()
    tone = st.radio("Email tone", list(de.TONES), index=0, format_func=str.capitalize)

st.title("Quote Check")
st.caption("Finds unjustified price increases in supplier quotations, drafts the questions, "
           "and checks the supplier's answers. Numbers are computed by rules; the local AI only "
           "reads documents and writes the prose.")

# ------------------------------------------------------------- 1. the quote
st.header("1 · Read the quotation")
col_a, col_b = st.columns([2, 1])
with col_a:
    uploaded = st.file_uploader("Upload a supplier quotation (PDF)", type="pdf")
with col_b:
    demo = st.selectbox("…or use a demo quotation", ["—"] + list(DEMO_QUOTES))

source = None
if uploaded is not None:
    tmp = Path(tempfile.gettempdir()) / uploaded.name
    tmp.write_bytes(uploaded.getvalue())
    source = (tmp, uploaded.name)
elif demo != "—":
    source = (DATA / DEMO_QUOTES[demo][0], demo)

if source and ss.get("source_name") != source[1]:
    reset_from("lines")
    ss.source_name = source[1]
    ss.demo_reply = DEMO_QUOTES[demo][1] if demo != "—" and uploaded is None else None

if source and st.button("Read quotation", type="primary"):
    reset_from("lines")
    with st.spinner("Reading the price table…"):
        lines, text = ex.read_table(source[0])
    with st.spinner("Local AI is reading the cover letter… (first run can take up to a minute)"):
        info = ex.read_cover_letter(text)
    ss.lines, ss.check, ss.meta = lines, ex.check_total(lines, text), info.model_dump()

if "lines" in ss:
    m = ss.meta
    c1, c2, c3 = st.columns([2, 1, 1])
    c1.metric("Supplier", m["supplier"])
    c2.metric("Quotation", m["quote_number"])
    c3.metric("Date", m["quote_date"])
    (st.success if ss.check.startswith("OK") else st.warning)(ss.check)
    with st.expander(f"Price table ({len(ss.lines)} lines)"):
        st.dataframe(styled(ss.lines), hide_index=True, width="stretch")
    st.markdown("**Reasons stated by the supplier** (read by the local AI)")
    reasons = pd.DataFrame(m["stated_reasons"]) if m["stated_reasons"] else None
    if reasons is not None:
        reasons["claimed_increase_pct"] = reasons["claimed_increase_pct"].map(
            lambda x: "" if pd.isna(x) else f"{x:.1f}%")
    if reasons is not None:
        st.dataframe(reasons.rename(columns={"reason": "Reason", "applies_to": "Applies to", "claimed_increase_pct": "Claimed %"}), hide_index=True, width="stretch")
    else:
        st.caption("No reasons stated.")
    if m["new_charges_mentioned"]:
        st.markdown("New charges announced: " + ", ".join(m["new_charges_mentioned"]))

# ----------------------------------------------------------- 2. comparison
if "lines" in ss:
    st.header("2 · Compare with price history and cost index")
    if st.button("Analyse prices", type="primary"):
        reset_from("findings")
        history = pd.read_csv(DATA / "quote_history.csv")
        index = pd.read_csv(DATA / "benchmark_index.csv").set_index("year")
        supplier, year, findings = cmp.compare(ss.lines, ss.meta, history, index)
        ss.findings, ss.quote_checks = findings, cmp.quote_level_checks(findings, ss.meta, index, year)

if "findings" in ss:
    f = ss.findings
    extra = f.loc[f["flag"] != "DECREASE", "extra_cost_eur"].sum()
    flagged = f[~f["flag"].isin(["OK"])]
    c1, c2, c3 = st.columns(3)
    c1.metric("Lines analysed", len(f))
    c2.metric("Lines needing clarification", int((~f["flag"].isin(["OK", "DECREASE"])).sum()))
    c3.metric("Cost above index", de.eur(extra))

    blanket = f[f["flag"] == "UNIFORM INCREASE"]
    if not blanket.empty:
        st.warning(f"Uniform increase: {len(blanket)} parts all up "
                   f"{de.pct(blanket['change_pct'].median())} while the cost index rose "
                   f"{de.pct(blanket['index_pct'].median())}.")
    for line in ss.quote_checks:
        if "FLAG" in line:
            st.warning(line.replace("-> FLAG:", "").strip())

    show = flagged[flagged["flag"] != "UNIFORM INCREASE"][
        ["part_no", "description", "old_price", "new_price", "change_pct", "index_pct",
         "extra_cost_eur", "flag", "note"]]
    st.caption("All amounts in EUR. Extra cost = amount above what the cost index would justify.")
    st.dataframe(styled(show, "flag", FLAG_COLORS), hide_index=True, width="stretch")
    with st.expander("All lines"):
        st.dataframe(styled(f, "flag", FLAG_COLORS), hide_index=True, width="stretch")

# --------------------------------------------------------------- 3. email
if "findings" in ss:
    st.header("3 · Ask the supplier to justify")
    if st.button("Draft email", type="primary"):
        reset_from("email")
        items, thanks, impact = de.fact_lines(ss.findings, "en")
        with st.spinner("Local AI is writing the opening and closing…"):
            frame = de.write_frame(ss.meta, tone, "en")
        ss.email = de.assemble(frame, items, thanks, impact, "en")

if "email" in ss:
    st.caption("All figures come from the analysis above. Edit freely before sending.")
    ss.email = st.text_area("Draft", ss.email, height=420, label_visibility="collapsed")
    st.download_button("Download email (.txt)", ss.email,
                       file_name=f"{ss.meta['quote_number']}_clarification.txt")

# -------------------------------------------------------- 4. supplier reply
if "email" in ss:
    st.header("4 · Check the supplier's answer")
    default_reply = ""
    if ss.get("demo_reply"):
        default_reply = (DATA / ss.demo_reply).read_text()
    reply = st.text_area("Paste the supplier's reply", value=default_reply, height=260)
    if reply.strip() and st.button("Check reply", type="primary"):
        reset_from("reply_results")
        items = ar.questioned_items(ss.findings)
        with st.spinner("Local AI is reading the reply…"):
            claims = ar.read_reply(reply, items)
        by_item = {}
        for c in claims:
            by_item.setdefault(c.item.strip().upper(), c)
        by_item, ss.corrections = ar.cross_check(reply, items, by_item)
        ss.asked_items, ss.claims = items, by_item
        ss.reply_results = True


def judge_all(shares):
    """Rules only - re-run instantly whenever the buyer edits the material shares."""
    ctx = market.load_context(ss.meta["quote_date"], shares, real if use_real else None)
    results, rows = [], []
    for item in ss.asked_items:
        claim = ss.claims.get(item["id"].upper())
        verdict, line, justified = ar.judge(item, claim, ctx)
        results.append((verdict, line))
        rows.append({"item": item["id"], "verdict": verdict,
                     "category": claim.category if claim else "",
                     "claimed_pct": claim.claimed_pct if claim else None,
                     "price_change_pct": item["change"],
                     "justified_pct": justified,
                     "supplier says": claim.summary if claim else "— not addressed —",
                     "reading": "corrected" if item["id"] in ss.corrections else "AI"})
    return results, pd.DataFrame(rows), ctx


if "reply_results" in ss:
    all_shares = pd.read_csv(DATA / "material_shares.csv")
    asked = [i["id"] for i in ss.asked_items]
    relevant = all_shares[all_shares["part_no"].isin(asked)]
    if not relevant.empty:
        with st.expander("Material shares – your assumptions (edit to see the effect)", expanded=False):
            st.caption("Which material drives each part's cost, and its share of the part price. "
                       "These are the buyer's assumptions, not AI guesses.")
            edited = st.data_editor(
                relevant[["part_no", "material", "share_pct", "standard_component"]],
                hide_index=True, width="stretch", key="shares_editor",
                disabled=["part_no", "material", "standard_component"],
                column_config={"part_no": "Part no.", "material": "Main cost material",
                               "share_pct": st.column_config.NumberColumn("Share %", min_value=0,
                                                                         max_value=100, step=5),
                               "standard_component": "Standard component"})
            all_shares = all_shares.set_index("part_no")
            all_shares.loc[edited["part_no"], "share_pct"] = edited["share_pct"].values
            all_shares = all_shares.reset_index()

    results, r, ctx = judge_all(all_shares)
    if "frame" not in ss:
        with st.spinner("Local AI is writing the follow-up…"):
            ss.frame = ar.write_followup_frame(ss.meta, results, tone)

    c1, c2, c3 = st.columns(3)
    c1.metric("Accepted", int(r["verdict"].isin(["SUPPORTED", "PLAUSIBLE - CHECK DOCUMENTS"]).sum()))
    c2.metric("Concessions", int((r["verdict"] == "CONCESSION OFFERED").sum()))
    c3.metric("Still open", int((~r["verdict"].isin(
        ["SUPPORTED", "PLAUSIBLE - CHECK DOCUMENTS", "CONCESSION OFFERED"])).sum()))
    st.dataframe(styled(r, "verdict", VERDICT_COLORS).map(
        lambda v: "background-color: #e1effe; color: #1e429f; font-weight: 600" if v == "corrected" else "",
        subset=["Reading"]), hide_index=True, width="stretch")
    if ss.corrections:
        with st.expander(f"Rule-based check corrected the AI's reading on {len(ss.corrections)} item(s)"):
            st.caption("For every part, Python re-reads the supplier's paragraph and compares it with the "
                       "AI's interpretation. Clear facts (a single percentage, a named material, a "
                       "sub-supplier) override the AI.")
            for part, note in ss.corrections.items():
                st.markdown(f"**{part}**: {note}")
    st.caption("Claimed by supplier = the cost increase the supplier states (e.g. copper +18%). "
               "Price change = how much this part's price actually rose. "
               "Justified = material share × actual market change + rest × general cost index. "
               "Accepted when the price change does not exceed what is justified.")

    claimed = {market.commodity_of(c.material) for c in ss.claims.values() if c.material}
    used = sorted(({k for k in relevant["material_key"]} | claimed) & set(ctx["prices"].columns))
    if not relevant.empty and used:
        with st.expander("Market data used (last 12 months vs. 12 months before)", expanded=False):
            rows, srcs = [], md.sources()
            for k in used:
                avg, peak = market.market_change(ctx["prices"], k, ctx["quote_date"])
                if avg is None:
                    continue
                label = market.COMMODITY_LABELS.get(k, k)
                rows.append({"Commodity": label[0].upper() + label[1:],
                             "12-month average": f"{avg:+.1f}%", "Peak single month": f"{peak:+.1f}%",
                             "Source": srcs.get(k, {}).get("source", "") if use_real else "synthetic demo"})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            st.caption("Real imported data." if use_real else
                       "SYNTHETIC demo data. Switch to real data in the sidebar after importing the "
                       "World Bank file.")

    # ---- commodities the checks need but the active market data does not cover
    needed = ({k for k in relevant["material_key"] if k in market.COMMODITY_LABELS} | claimed) - {None}
    gaps = md.missing(ctx["prices"], needed, ss.meta["quote_date"])
    if gaps:
        names = ", ".join(market.COMMODITY_LABELS.get(g, g) for g in gaps)
        st.warning(f"No complete market data for: {names}. Claims about these materials are checked "
                   "with general rules only.")
        with st.expander(f"Get the missing data ({names})", expanded=True):
            st.markdown("**1. Copy this request** into an external AI assistant with web search, "
                        "or send it to a colleague.")
            st.caption("It contains only commodity names, months and a file format - nothing from "
                       "the quotation, the supplier or your prices.")
            st.code(md.data_request(gaps, ss.meta["quote_date"]), language=None, wrap_lines=True)
            st.markdown("**2. Paste the answer here.** The tool checks it before using it.")
            with st.form("paste_form", border=False):
                pasted = st.text_area("Answer", height=180, key="pasted_data",
                                      label_visibility="collapsed")
                submitted = st.form_submit_button("Check and add data", type="primary")
            if submitted and not pasted.strip():
                st.warning("Paste the answer into the box first.")
            if submitted and pasted.strip():
                df, errors, warnings = md.check_pasted(pasted, gaps, ss.meta["quote_date"])
                for e in errors:
                    st.error(e)
                for w in warnings:
                    st.warning(w)
                if df is not None:
                    md.save_extra(df, pasted)
                    ss.pop("frame", None)
                    st.success("Data added. Re-checking with the new data…")
                    st.rerun()

    followup = ar.assemble(ss.frame, results)
    st.text_area("Follow-up email", followup, height=460, key=f"followup_{hash(followup)}")
    st.download_button("Download follow-up (.txt)", followup,
                       file_name=f"{ss.meta['quote_number']}_followup.txt")
