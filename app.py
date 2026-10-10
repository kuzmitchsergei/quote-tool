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
import cases as cs
import mailfile as mf
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
           "status": "Status", "round": "Round", "decided by": "Decided by",
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


def styled(df, status_col=None, colors=None, move_status=True):
    """Readable table: status right after the first column, clear names, formatted numbers,
    blank instead of 'None', colour only on the status cell (always with dark text)."""
    df = df.drop(columns=[c for c in ("note",) if c in df.columns]).copy()
    if status_col in df.columns and move_status:
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
    order = ["lines", "findings", "email"]
    for key in order[order.index(step):]:
        ss.pop(key, None)


# ------------------------------------------------------------------ sidebar
if "goto_case" in ss:
    ss.case_id = ss.pop("goto_case")
    ss.view = "Cases"
    ss.pop("pending", None)

with st.sidebar:
    n_cases = len(cs.list_cases())
    view = st.radio("View", ["New analysis", "Cases"], key="view", horizontal=True,
                    format_func=lambda v: f"Cases ({n_cases})" if v == "Cases" else v)
    st.divider()
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
            if ss.get("pending"):
                ss.pending["frame"] = None
            ss.wb_message = None
            st.toast(f"Removed {names}. Backup: market_data/removed/{moved.name}")
            st.rerun()
    st.divider()
    tone = st.radio("Email tone", list(de.TONES), index=0, format_func=str.capitalize)

def page_new():

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

    # ------------------------------------------------------ 4. open a case
    if "email" in ss:
        st.header("4 · Send and track")
        st.caption("Send the email from your mail program. Then open a case: the tool keeps the full "
                   "history, and each supplier reply is checked against the points that are still open.")
        existing = cs.list_cases()
        same = existing[(existing["quote_number"] == ss.meta["quote_number"])
                        & (existing["status"] == "open")] if not existing.empty else existing
        if not same.empty:
            st.info(f"An open case already exists for {ss.meta['quote_number']}.")
            if st.button("Go to the existing case"):
                ss.goto_case = int(same.iloc[0]["id"])
                st.rerun()
        elif st.button("Open case (email sent)", type="primary"):
            demo_key = None
            if ss.get("demo_reply"):
                demo_key = ss.demo_reply.replace("reply_", "").replace(".txt", "")
            cid = cs.create_case(ss.meta, ss.findings, ar.questioned_items(ss.findings), ss.email, demo_key)
            ss.goto_case = cid
            st.rerun()


# ======================================================================= shared: assess a reply
def judge_all(items, claims, corrections, quote_date, shares):
    """Rules only - re-run instantly whenever the buyer edits the material shares."""
    ctx = market.load_context(quote_date, shares, real if use_real else None)
    results, rows = [], []
    for item in items:
        claim = claims.get(item["id"].upper())
        verdict, line, justified = ar.judge(item, claim, ctx)
        results.append((verdict, line))
        rows.append({"item": item["id"], "verdict": verdict,
                     "category": claim.category if claim else "",
                     "claimed_pct": claim.claimed_pct if claim else None,
                     "price_change_pct": item["change"],
                     "justified_pct": justified,
                     "supplier says": claim.summary if claim else "— not addressed —",
                     "reading": "corrected" if item["id"] in corrections else "AI"})
    return results, pd.DataFrame(rows), ctx


def assessment_ui(meta, pending):
    """Verdicts, material shares, market data, missing data and the follow-up draft.
    Returns (verdict table, follow-up text)."""
    items, claims, corrections = pending["items"], pending["claims"], pending["corrections"]
    all_shares = pd.read_csv(DATA / "material_shares.csv")
    relevant = all_shares[all_shares["part_no"].isin([i["id"] for i in items])]
    if not relevant.empty:
        with st.expander("Material shares – your assumptions (edit to see the effect)", expanded=False):
            st.caption("Which material drives each part's cost, and its share of the part price. "
                       "These are the buyer's assumptions, not AI guesses.")
            edited = st.data_editor(
                relevant[["part_no", "material", "share_pct", "standard_component"]],
                hide_index=True, width="stretch", key=f"shares_{pending['key']}",
                disabled=["part_no", "material", "standard_component"],
                column_config={"part_no": "Part no.", "material": "Main cost material",
                               "share_pct": st.column_config.NumberColumn("Share %", min_value=0,
                                                                         max_value=100, step=5),
                               "standard_component": "Standard component"})
            all_shares = all_shares.set_index("part_no")
            all_shares.loc[edited["part_no"], "share_pct"] = edited["share_pct"].values
            all_shares = all_shares.reset_index()

    results, r, ctx = judge_all(items, claims, corrections, meta["quote_date"], all_shares)
    if pending.get("frame") is None:
        with st.spinner("Local AI is writing the follow-up…"):
            pending["frame"] = ar.write_followup_frame(meta, results, tone)

    closed = r["verdict"].isin(cs.CLOSED_VERDICTS)
    c1, c2, c3 = st.columns(3)
    c1.metric("Accepted", int(r["verdict"].isin(["SUPPORTED", "PLAUSIBLE - CHECK DOCUMENTS"]).sum()))
    c2.metric("Concessions", int((r["verdict"] == "CONCESSION OFFERED").sum()))
    c3.metric("Still open", int((~closed).sum()))
    st.dataframe(styled(r, "verdict", VERDICT_COLORS).map(
        lambda v: "background-color: #e1effe; color: #1e429f; font-weight: 600" if v == "corrected" else "",
        subset=["Reading"]), hide_index=True, width="stretch")
    if corrections:
        with st.expander(f"Rule-based check corrected the AI's reading on {len(corrections)} item(s)"):
            st.caption("For every part, Python re-reads the supplier's paragraph and compares it with the "
                       "AI's interpretation. Clear facts (a single percentage, a named material, a "
                       "sub-supplier, an attachment, an offered reduction) override the AI.")
            for part, note in corrections.items():
                st.markdown(f"**{part}**: {note}")
    st.caption("Claimed by supplier = the cost increase the supplier states (e.g. copper +18%). "
               "Price change = how much this part's price actually rose. "
               "Justified = material share × actual market change + rest × general cost index. "
               "Accepted when the price change does not exceed what is justified.")

    claimed = {market.commodity_of(c.material) for c in claims.values() if c.material}
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

    needed = ({k for k in relevant["material_key"] if k in market.COMMODITY_LABELS} | claimed) - {None}
    gaps = md.missing(ctx["prices"], needed, meta["quote_date"])
    if gaps:
        names = ", ".join(market.COMMODITY_LABELS.get(g, g) for g in gaps)
        st.warning(f"No complete market data for: {names}. Claims about these materials are checked "
                   "with general rules only.")
        with st.expander(f"Get the missing data ({names})", expanded=True):
            st.markdown("**1. Copy this request** into an external AI assistant with web search, "
                        "or send it to a colleague.")
            st.caption("It contains only commodity names, months and a file format - nothing from "
                       "the quotation, the supplier or your prices.")
            st.code(md.data_request(gaps, meta["quote_date"]), language=None, wrap_lines=True)
            st.markdown("**2. Paste the answer here.** The tool checks it before using it.")
            with st.form(f"paste_form_{pending['key']}", border=False):
                pasted = st.text_area("Answer", height=180, key="pasted_data",
                                      label_visibility="collapsed")
                submitted = st.form_submit_button("Check and add data", type="primary")
            if submitted and not pasted.strip():
                st.warning("Paste the answer into the box first.")
            if submitted and pasted.strip():
                df, errors, warnings = md.check_pasted(pasted, gaps, meta["quote_date"])
                for e in errors:
                    st.error(e)
                for w in warnings:
                    st.warning(w)
                if df is not None:
                    md.save_extra(df, pasted)
                    pending["frame"] = None
                    st.success("Data added. Re-checking with the new data…")
                    st.rerun()

    followup = ar.assemble(pending["frame"], results)
    followup = st.text_area("Follow-up email (edit freely before sending)", followup, height=420,
                            key=f"followup_{pending['key']}_{hash(followup)}")
    st.download_button("Download follow-up (.txt)", followup,
                       file_name=f"{meta['quote_number']}_followup.txt")
    return r, followup


# ======================================================================= cases page
EVENT_ICONS = {"quote": "📄", "request": "✉️", "reply": "📥", "assessment": "⚖️",
               "followup": "✉️", "closed": "✅", "decision": "✍️"}


def demo_reply_for(case):
    """Demo reply files: reply_<key>.txt for round 1, reply_<key>_round<N>.txt after that."""
    if not case["demo_key"]:
        return None
    name = (f"reply_{case['demo_key']}.txt" if case["round"] == 1
            else f"reply_{case['demo_key']}_round{case['round']}.txt")
    return DATA / name if (DATA / name).exists() else None


def page_cases():
    cases = cs.list_cases()
    if cases.empty:
        st.info("No cases yet. Analyse a quotation under 'New analysis' and click 'Open case'.")
        return
    labels = {int(r.id): f"{r.quote_number} · {r.supplier} · "
                         f"{'closed' if r.status == 'closed' else f'round {r.round}, {int(r.open_items)} open'}"
              for r in cases.itertuples()}
    ids = list(labels)
    if ss.get("case_id") not in ids:
        ss.case_id = ids[0]
    cid = st.selectbox("Case", ids, index=ids.index(ss.case_id), format_func=labels.get)
    ss.case_id = cid
    case = cs.get_case(cid)
    meta = case["meta"]

    open_ = [i for i in case["items"] if i["status"] == "open"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", "Closed" if case["status"] == "closed" else f"Open · round {case['round']}")
    c2.metric("Open points", len(open_))
    c3.metric("Closed points", len(case["items"]) - len(open_))
    c4.metric("Open amount above index", de.eur(sum(i["extra_cost"] for i in open_)))

    st.subheader("Points questioned")
    tbl = pd.DataFrame([{"item": i["item_id"], "description": i["data"].get("label", ""),
                         "status": i["status"], "verdict": i["verdict"],
                         "round": i["updated_round"] or "", "decided by": i["decided_by"],
                         "extra_cost_eur": i["extra_cost"]} for i in case["items"]])
    st.dataframe(styled(tbl, "verdict", VERDICT_COLORS | FLAG_COLORS, move_status=False).map(
        lambda v: ("background-color: #def7ec; color: #03543f; font-weight: 600" if v == "closed" else
                   "background-color: #fde8e8; color: #9b1c1c; font-weight: 600" if v == "open" else ""),
        subset=["Status"]), hide_index=True, width="stretch",
        column_config={"Verdict": st.column_config.Column(width="medium")})
    with st.expander("Close or reopen a point manually"):
        st.caption("Your decision overrides the rules, e.g. after a phone call. It is recorded in the history.")
        m1, m2 = st.columns([3, 1])
        pick = m1.selectbox("Point", [i["item_id"] for i in case["items"]],
                            format_func=lambda x: f"{x} ({next(i['status'] for i in case['items'] if i['item_id'] == x)})")
        current = next(i["status"] for i in case["items"] if i["item_id"] == pick)
        if m2.button("Reopen" if current == "closed" else "Close", width="stretch"):
            cs.set_status(cid, pick, "open" if current == "closed" else "closed")
            ss.pop("pending", None)
            st.rerun()

    st.subheader("History")
    for e in reversed(case["events"]):
        with st.expander(f"{EVENT_ICONS.get(e['kind'], '•')}  {e['created']} · "
                         f"{'Start' if e['round'] == 0 else 'Round ' + str(e['round'])} · {e['title']}"):
            st.text(e["body"] or "")
    st.download_button("Download negotiation record (.txt)", cs.export_text(cid),
                       file_name=f"{case['quote_number']}_negotiation_record.txt")

    if case["status"] == "closed":
        st.success("All points are resolved. The negotiation record documents every step.")
        return

    # ---- next round: the supplier's reply
    rnd = case["round"]
    st.header(f"Round {rnd} · Supplier's reply")
    st.caption(f"Only the {len(open_)} open point(s) are checked. Drop the email file "
               "(.eml from Mail / Outlook for Mac, .msg from Outlook for Windows) or paste the text.")
    key = f"{cid}_{rnd}"
    up = st.file_uploader("Email file", type=["eml", "msg", "txt"], key=f"mail_{key}")
    demo_file = demo_reply_for(case)
    if demo_file and st.button("Load demo reply for this round"):
        ss[f"reply_{key}"] = demo_file.read_text()
        ss[f"replymeta_{key}"] = {"sender": "Demo supplier", "date": "", "subject": demo_file.name}
    if up is not None and ss.get(f"upname_{key}") != up.name + str(up.size):
        try:
            mail = mf.read_email_file(up.name, up.getvalue())
            ss[f"reply_{key}"] = mail["body"]
            ss[f"replymeta_{key}"] = mail
            ss[f"upname_{key}"] = up.name + str(up.size)
        except Exception as e:
            st.error(f"Could not read the email file ({e}). Please paste the text instead.")
    meta_mail = ss.get(f"replymeta_{key}")
    if meta_mail and meta_mail.get("sender"):
        st.caption(f"From: {meta_mail.get('sender')} · {meta_mail.get('date', '')} · "
                   f"{meta_mail.get('subject', '')} — quoted earlier emails removed")
    reply = st.text_area("Supplier's reply", key=f"reply_{key}", height=260)

    if reply.strip() and st.button("Check reply", type="primary"):
        items = cs.open_items(case)
        with st.spinner("Local AI is reading the reply…"):
            claims = ar.read_reply(reply, items)
        by_item = {}
        for c in claims:
            by_item.setdefault(c.item.strip().upper(), c)
        by_item, corrections = ar.cross_check(reply, items, by_item)
        ss.pending = {"key": key, "items": items, "claims": by_item, "corrections": corrections,
                      "reply": reply, "mail": meta_mail or {}, "frame": None}

    pending = ss.get("pending")
    if pending and pending["key"] == key:
        verdicts, followup = assessment_ui(meta, pending)
        st.caption("Saving records the reply, the assessment and the follow-up in the case history. "
                   "Points that are accepted, supported by documents or conceded are closed.")
        if st.button("Save round (follow-up sent)", type="primary"):
            mail = pending["mail"] or {}
            status = cs.save_round(cid, {"sender": mail.get("sender", ""), "date": mail.get("date", ""),
                                         "subject": mail.get("subject", ""), "body": pending["reply"]},
                                   verdicts[["item", "verdict"]].to_dict("records"), followup)
            ss.pop("pending", None)
            st.toast("Round saved." + (" All points resolved." if status == "closed" else ""))
            st.rerun()


# ======================================================================= main
st.title("Quote Check")
st.caption("Finds unjustified price increases in supplier quotations, drafts the questions, "
           "and checks the supplier's answers. Numbers are computed by rules; the local AI only "
           "reads documents and writes the prose.")
if view == "New analysis":
    page_new()
else:
    page_cases()
