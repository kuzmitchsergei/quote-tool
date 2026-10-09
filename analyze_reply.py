"""
Step 4 of the quotation analysis tool: check the supplier's answer and draft the follow-up.

Who does what
  - The LOCAL AI reads the supplier's reply and sorts every justification into a category
    (raw material, energy, labour, technical change, market conditions...), notes any percentage
    the supplier states, whether documents are attached and whether they offer a concession.
  - Python judges each claim against the data (actual increase, cost index) with fixed rules.
    The AI never decides whether a justification is acceptable.
  - The follow-up email: Python writes every line with a number, the AI only the opening and
    closing (same guard as draft_email.py).

Note: the index is a general benchmark. A specific input (e.g. special steel) can rise faster than
the index. So the tool never says a claim is false - it says "not supported by the index" and asks
for evidence.

Usage (inside the quote-tool folder, after extract.py and compare.py):
  python3 analyze_reply.py demo_data/new_quote_OEM_Alpha_2026.pdf demo_data/reply_OEM_Alpha_2026.txt
  optional: --tone cooperative | firm   (default: firm)

Output: <name>_reply_check.csv and <name>_followup_email.txt next to the PDF.
"""
import argparse
import json
import re
from pathlib import Path
from typing import Literal

import pandas as pd
from ollama import chat
from pydantic import BaseModel

from draft_email import MODEL, SIGNOFF, TONES, EmailFrame, clean_text, has_figures, pct

TOLERANCE_PTS = 3.0   # claimed cost driver vs. index
GAP_PTS = 1.0         # claimed cost driver vs. actual price increase
QUESTIONED = ["ABOVE INDEX", "PART REPLACED", "NEW CHARGE", "UNIFORM INCREASE"]


# ------------------------------------------------------- the items we asked about
def questioned_items(df):
    """One entry per item we asked about; the uniform increase counts as one item 'ALL'."""
    items = []
    blanket = df[df["flag"] == "UNIFORM INCREASE"]
    if not blanket.empty:
        items.append({"id": "ALL", "label": f"general increase on {len(blanket)} parts",
                      "change": blanket["change_pct"].median(), "index": blanket["index_pct"].median(),
                      "flag": "UNIFORM INCREASE"})
    for _, r in df[df["flag"].isin(QUESTIONED[:3])].iterrows():
        label = re.sub(r"\s*\((replaces [^)]*|new)\)", "", r["description"])
        items.append({"id": r["part_no"], "label": label,
                      "change": r["change_pct"], "index": r["index_pct"], "flag": r["flag"]})
    return items


# -------------------------------------------------- 1. AI reads the reply
Category = Literal["raw_material", "energy", "labour", "technical_change",
                   "market_conditions", "other"]


class Claim(BaseModel):
    item: str                       # one of the item ids we give the model
    category: Category
    claimed_pct: float | None       # only if the supplier writes a percentage
    documents_attached: bool        # supplier says a document/datasheet/breakdown is attached
    concession: bool                # supplier offers to reduce, waive or withdraw something
    summary: str                    # one short sentence in neutral words


class ReplyAnalysis(BaseModel):
    claims: list[Claim]


PROMPT = """You analyse a supplier's reply for a procurement department.
We had asked the supplier to justify these items (id: description):
{items}

For every justification in the reply, return one claim with:
- item: the id from the list above that the justification refers to. Use "ALL" for a general
  increase on all parts. Use the exact id.
- category: raw_material, energy, labour, technical_change, market_conditions or other
- claimed_pct: the percentage the supplier states for this cost driver, only if written in the
  reply, otherwise null
- documents_attached: true only if the reply says a document, datasheet or breakdown is attached
- concession: true only if the supplier offers to reduce, waive or withdraw a price or charge
- summary: one short neutral sentence
Only use information written in the reply. Do not guess. Items the reply does not mention get no claim.

SUPPLIER REPLY:
{reply}
"""


def read_reply(reply_text, items):
    item_list = "\n".join(f"- {i['id']}: {i['label']}" for i in items)
    response = chat(
        model=MODEL,
        messages=[{"role": "user", "content": PROMPT.format(items=item_list, reply=reply_text)}],
        format=ReplyAnalysis.model_json_schema(),
        options={"temperature": 0},
    )
    return ReplyAnalysis.model_validate_json(response.message.content).claims


# ----------------------------------------------- 2. Python judges each claim
def judge(item, claim):
    """Fixed rules. Returns (verdict, follow-up line for the email)."""
    i, label = item["id"], item["label"]
    name = "General increase" if i == "ALL" else f"{i} ({label})"
    chg, idx = item["change"], item["index"]

    if claim is None:
        return "NO ANSWER", f"{name}: not addressed in your reply. Please provide your explanation."

    if claim.concession:
        return "CONCESSION OFFERED", (f"{name}: we take note of the offer in your reply. "
                                      "Please confirm it, with any conditions, in a revised quotation.")

    if claim.category == "technical_change":
        if claim.documents_attached:
            return "PLAUSIBLE - CHECK DOCUMENTS", (
                f"{name}: thank you for explaining the technical change and providing documentation, "
                "which we will review with our technical team.")
        return "DOCUMENTS MISSING", (f"{name}: please provide technical documentation of the change "
                                     "that justifies the new price.")

    if claim.category in ("market_conditions", "other"):
        extra = (f" Your stated adjustment of {pct(claim.claimed_pct)} compares with "
                 f"{pct(idx)} for the relevant cost index." if claim.claimed_pct is not None and pd.notna(idx) else "")
        return "UNSUBSTANTIATED", (f"{name}: a reference to market conditions is not a cost driver.{extra} "
                                   "Please provide a breakdown of the specific cost increases.")

    # raw_material / energy / labour
    if claim.claimed_pct is None:
        return "UNSUBSTANTIATED", (f"{name}: please quantify the stated {claim.category.replace('_', ' ')} "
                                   "cost increase and provide supporting evidence.")

    issues, verdicts = [], []
    if pd.notna(idx) and claim.claimed_pct - idx > TOLERANCE_PTS:
        verdicts.append("ABOVE INDEX")
        issues.append(f"you cite {pct(claim.claimed_pct)}, while the relevant cost index rose "
                      f"{pct(idx)}; please provide evidence such as supplier invoices")
    if pd.notna(chg) and chg - claim.claimed_pct > GAP_PTS:
        verdicts.append("PARTLY EXPLAINED")
        issues.append(f"a cost driver of {pct(claim.claimed_pct)} explains only part of the "
                      f"{pct(chg)} price increase; please explain the remaining "
                      f"{chg - claim.claimed_pct:.1f} points")
    if not issues:
        return "SUPPORTED", f"{name}: thank you, your explanation is consistent with our data."
    return " / ".join(verdicts), f"{name}: " + ". Also, ".join(issues) + "."


# ---------------------------------------------- 3. follow-up email
FOLLOWUP_PROMPT = """You are writing on behalf of the procurement department of a printing works to a supplier.
This is our follow-up after the supplier answered our questions about quotation {quote_no}.
Our program adds the item-by-item responses automatically between your opening and your closing.
Do not write them and do not write any placeholder for them.

Overall: {summary}
Tone: {tone}. Write in English.
Write:
- subject: short, starting with "RE:" and mentioning the quotation number
- opening: 2 sentences. Thank them for the reply; say we reviewed it item by item.
- closing: 2-3 sentences. Ask for the missing information and a revised quotation within two weeks,
  and say that we can only place the order once the open points are resolved. Offer a call.
IMPORTANT: no numbers, percentages, prices, part numbers or dates in opening or closing. The subject
may contain the quotation number only. Do not add a greeting or sign-off line at the end - it is
added automatically.
"""

FALLBACK = EmailFrame(
    subject="RE: Quotation {q} - our review of your reply",
    opening="Thank you for your reply. We have reviewed your explanations item by item.",
    closing="Please send us the missing information and a revised quotation within two weeks. "
            "We can only place the order once the open points are resolved, and we are happy to "
            "discuss them in a call.")


def write_followup_frame(meta, results, tone):
    open_points = sum(v not in ("SUPPORTED", "PLAUSIBLE - CHECK DOCUMENTS") for v, _ in results)
    summary = (f"some explanations are acceptable, but several points remain open"
               if open_points else "the explanations are acceptable")
    q = meta.get("quote_number", "")
    response = chat(
        model=MODEL,
        messages=[{"role": "user", "content": FOLLOWUP_PROMPT.format(
            quote_no=q, summary=summary, tone=TONES[tone])}],
        format=EmailFrame.model_json_schema(),
        options={"temperature": 0.3},
    )
    frame = EmailFrame.model_validate_json(response.message.content)
    frame.opening = clean_text(frame.opening)
    frame.closing = clean_text(frame.closing)
    bad = [n for n, t, a in (("subject", frame.subject, q), ("opening", frame.opening, q),
                             ("closing", frame.closing, q)) if has_figures(t, a) or not t.strip()]
    if bad:
        print(f"GUARD: AI text had figures or was empty in the {', '.join(bad)} - using standard text there.")
        frame = EmailFrame(subject=FALLBACK.subject.format(q=q) if "subject" in bad else frame.subject,
                           opening=FALLBACK.opening if "opening" in bad else frame.opening,
                           closing=FALLBACK.closing if "closing" in bad else frame.closing)
    return frame


def assemble(frame, results):
    accepted = [line for v, line in results if v in ("SUPPORTED", "PLAUSIBLE - CHECK DOCUMENTS")]
    offers = [line for v, line in results if v == "CONCESSION OFFERED"]
    open_ = [line for v, line in results if v not in ("SUPPORTED", "PLAUSIBLE - CHECK DOCUMENTS",
                                                       "CONCESSION OFFERED")]
    body = [frame.opening]
    for header, lines in (("Open points:", open_), ("Offers we take note of:", offers),
                          ("Explanations we accept:", accepted)):
        if lines:
            body += ["", header, ""] + [f"{n}. {line}" for n, line in enumerate(lines, start=1)]
    body += ["", frame.closing, "", SIGNOFF["en"]]
    return f"Subject: {frame.subject}\n\n" + "\n".join(body)


# -------------------------------------------------------------------- main
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("reply")
    ap.add_argument("--tone", choices=TONES, default="firm")
    args = ap.parse_args()

    base = Path(args.pdf).with_suffix("")
    findings = pd.read_csv(f"{base}_findings.csv")
    meta = json.loads(Path(f"{base}_meta.json").read_text())
    items = questioned_items(findings)

    print("Asking the local AI to read the supplier's reply...")
    claims = read_reply(Path(args.reply).read_text(), items)
    by_item = {}
    for c in claims:
        by_item.setdefault(c.item.strip().upper(), c)   # first claim per item

    results, rows = [], []
    for item in items:
        claim = by_item.get(item["id"].upper())
        verdict, line = judge(item, claim)
        results.append((verdict, line))
        rows.append({"item": item["id"], "verdict": verdict,
                     "category": claim.category if claim else "",
                     "claimed_pct": claim.claimed_pct if claim else None,
                     "supplier_says": claim.summary if claim else ""})
    unmatched = [c for c in claims if c.item.strip().upper() not in {i["id"].upper() for i in items}]

    table = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 70)
    print("\n" + table.to_string(index=False))
    if unmatched:
        print("\nClaims the AI could not link to an item (check manually):")
        for c in unmatched:
            print(f"  - {c.item}: {c.summary}")

    print("\nAsking the local AI to write the follow-up opening and closing...")
    email = assemble(write_followup_frame(meta, results, args.tone), results)

    table.to_csv(f"{base}_reply_check.csv", index=False)
    Path(f"{base}_followup_email.txt").write_text(email)
    print("\n" + email + f"\n\nSaved {base.name}_reply_check.csv and {base.name}_followup_email.txt")
