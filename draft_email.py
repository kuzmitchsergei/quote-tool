"""
Step 3 of the quotation analysis tool: draft the email to the supplier.

Who writes what
  - Python writes every line that contains a number (prices, %, index, EUR impact),
    straight from the findings file. The AI cannot change these.
  - The LOCAL AI writes only the subject, the opening and the closing,
    in the tone and language you choose.
  - A guard checks that the AI text contains no figures at all. If it does,
    a neutral standard text is used instead, so no invented number ever reaches the email.

Usage (inside the quote-tool folder, after extract.py and compare.py):
  python3 draft_email.py demo_data/new_quote_OEM_Alpha_2026.pdf
  python3 draft_email.py demo_data/new_quote_OEM_Alpha_2026.pdf --tone firm --lang de

  --tone   cooperative (default) | firm
  --lang   en (default) | de

Output: <name>_email_<lang>.txt next to the PDF.
"""
import argparse
import json
import re
from pathlib import Path

import pandas as pd
from ollama import chat
from pydantic import BaseModel

MODEL = "gemma4:e2b"


# ------------------------------------------------- 1. the fact lines (Python)
def eur(x, lang="en"):
    s = f"{x:,.2f}"
    if lang == "de":                      # German format: 2.633,74
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"EUR {s}"


def pct(x, lang="en"):
    s = f"{x:+.1f}"
    return f"{s.replace('.', ',')} %" if lang == "de" else f"{s}%"


TEXT = {
    "en": {
        "above": "{pn} ({desc}): {old} -> {new} ({chg}). The relevant cost index rose "
                 "{idx} over the same period. Please explain the specific cost drivers "
                 "behind this increase.",
        "replaced": "{pn} ({desc}): new part number at {new}, compared with {old} for the "
                    "previous part ({chg}). Please describe the technical changes that "
                    "justify the new price, and confirm whether the previous part is still available.",
        "new": "{pn} ({desc}): {total}. This charge did not appear in any previous quotation. "
               "Please explain its basis and why it is no longer covered by the unit prices.",
        "blanket": "{n} spare parts increased uniformly by {chg}, while the relevant cost "
                   "index rose {idx}. Please provide a cost breakdown supporting the part "
                   "of the increase above the index.",
        "decrease": "We also note the reduced price for {pn} ({desc}), {old} -> {new}. Thank you.",
        "items_header": "Items we ask you to clarify:",
        "impact": "In total, these items add {extra} compared with prices adjusted by the cost index.",
    },
    "de": {
        "above": "{pn} ({desc}): {old} -> {new} ({chg}). Der massgebliche Kostenindex stieg "
                 "im gleichen Zeitraum um {idx}. Bitte erläutern Sie die konkreten "
                 "Kostentreiber für diese Erhöhung.",
        "replaced": "{pn} ({desc}): neue Teilenummer zu {new}, gegenüber {old} für das bisherige "
                    "Teil ({chg}). Bitte beschreiben Sie die technischen Änderungen, die den "
                    "neuen Preis rechtfertigen, und bestätigen Sie, ob das bisherige Teil weiterhin "
                    "lieferbar ist.",
        "new": "{pn} ({desc}): {total}. Diese Position war in keinem früheren Angebot enthalten. "
               "Bitte erläutern Sie die Grundlage und weshalb sie nicht mehr in den Einzelpreisen "
               "enthalten ist.",
        "blanket": "{n} Ersatzteile wurden einheitlich um {chg} erhöht, der massgebliche "
                   "Kostenindex jedoch nur um {idx}. Bitte legen Sie eine Kostenaufstellung "
                   "für den Teil der Erhöhung vor, der über dem Index liegt.",
        "decrease": "Die Preissenkung für {pn} ({desc}), {old} -> {new}, nehmen wir dankend zur Kenntnis.",
        "items_header": "Positionen, um deren Klärung wir bitten:",
        "impact": "Insgesamt ergeben diese Positionen Mehrkosten von {extra} gegenüber "
                  "indexbereinigten Preisen.",
    },
}


def fact_lines(df, lang):
    t = TEXT[lang]
    items, thanks = [], []

    blanket = df[df["flag"] == "BLANKET INCREASE"]
    if not blanket.empty:
        items.append(t["blanket"].format(n=len(blanket), chg=pct(blanket["change_pct"].median(), lang),
                                         idx=pct(blanket["index_pct"].median(), lang)))

    # biggest money impact first
    for _, r in df.sort_values("extra_cost_eur", ascending=False).iterrows():
        desc = re.sub(r"\s*\((replaces [^)]*|new)\)", "", r["description"])
        v = dict(pn=r["part_no"], desc=desc,
                 chg=pct(r["change_pct"], lang) if pd.notna(r["change_pct"]) else "",
                 idx=pct(r["index_pct"], lang) if pd.notna(r["index_pct"]) else "",
                 old=eur(r["old_price"], lang) if pd.notna(r["old_price"]) else "",
                 new=eur(r["new_price"], lang), total=eur(r["extra_cost_eur"], lang))
        if r["flag"] == "ABOVE INDEX":
            items.append(t["above"].format(**v))
        elif r["flag"] == "PART REPLACED":
            items.append(t["replaced"].format(**v))
        elif r["flag"] == "NEW CHARGE":
            items.append(t["new"].format(**v))
        elif r["flag"] == "DECREASE":
            thanks.append(t["decrease"].format(**v))

    extra = df.loc[df["flag"] != "DECREASE", "extra_cost_eur"].sum()
    return items, thanks, t["impact"].format(extra=eur(extra, lang))


# ------------------------------------------- 2. the prose around it (local AI)
class EmailFrame(BaseModel):
    subject: str
    opening: str
    closing: str


TONES = {
    "cooperative": "polite, constructive and partnership-oriented; we value the relationship "
                   "and want to understand the increase",
    "firm": "polite but firm and businesslike; we do not accept unexplained increases and expect "
            "a substantiated answer before we can place orders",
}
LANGS = {"en": "English", "de": "German (Swiss business style, use 'ss' instead of 'ß')"}

PROMPT = """You are writing on behalf of the procurement department of a printing works to a supplier.
The supplier sent quotation {quote_no} dated {quote_date}. Our analysis found price increases that we
want explained. Our program adds the detailed list of items automatically between your opening and your closing.
Do not write the list and do not write any placeholder for it.

Supplier's own explanation in their cover letter: {reasons}

Write in {language}. Tone: {tone}.
Write:
- subject: a short email subject that mentions the quotation number
- opening: 2-3 sentences. Thank them for the quotation and say that some positions need clarification
  before we can proceed. If the supplier gave reasons, acknowledge them briefly and neutrally.
- closing: 2-3 sentences. Ask for a written answer with supporting documents within two weeks
  and offer a call to discuss.

IMPORTANT: do NOT write any numbers, percentages, prices, part numbers or dates in the opening or
closing. The subject may contain the quotation number only. Do not add a greeting or sign-off
line at the end - it is added automatically.
"""

FALLBACK = {
    "en": EmailFrame(subject="Clarification request - quotation {q}",
                     opening="Thank you for your quotation. Before we can proceed, we would like to "
                             "ask you to clarify the following positions.",
                     closing="We would appreciate a written answer with supporting documents within "
                             "two weeks. We are happy to discuss this in a call."),
    "de": EmailFrame(subject="Bitte um Klärung - Angebot {q}",
                     opening="Vielen Dank für Ihr Angebot. Bevor wir weiterfahren können, bitten wir "
                             "Sie um Klärung der folgenden Positionen.",
                     closing="Wir bitten um eine schriftliche Antwort mit Belegen innert zwei Wochen. "
                             "Gerne besprechen wir die Punkte auch telefonisch."),
}


SIGNOFF = {"en": "Kind regards", "de": "Freundliche Grüsse"}
SIGNOFF_LINE = re.compile(r"^\W*(kind|best|warm)?\s*regards\W*$|^\W*(sincerely|yours.*|no name|"
                          r"freundliche grüsse|mit freundlichen grüssen|beste grüsse)\W*$", re.I)


PLACEHOLDER = re.compile(r"\[[^\]]*\]|\binsert\b|\bplaceholder\b", re.I)


def clean_text(text):
    """Remove placeholder lines like '[Insert list here]' and any greeting/sign-off lines.
    Python inserts the item list and adds the sign-off itself."""
    lines = [l for l in text.strip().splitlines()
             if not SIGNOFF_LINE.match(l.strip()) and not PLACEHOLDER.search(l)]
    return "\n".join(lines).strip().rstrip(",")


clean_closing = clean_text   # old name, kept for compatibility


def has_figures(text, allowed=""):
    """True if the text contains any digit outside the allowed string (e.g. the quote number)."""
    return bool(re.search(r"\d", text.replace(allowed, "")))


def write_frame(meta, tone, lang):
    reasons = "; ".join(r["reason"] for r in meta.get("stated_reasons", [])) or "none given"
    response = chat(
        model=MODEL,
        messages=[{"role": "user", "content": PROMPT.format(
            quote_no=meta.get("quote_number", ""), quote_date=meta.get("quote_date", ""),
            reasons=reasons, language=LANGS[lang], tone=TONES[tone])}],
        format=EmailFrame.model_json_schema(),
        options={"temperature": 0.3},
    )
    frame = EmailFrame.model_validate_json(response.message.content)
    frame.opening = clean_text(frame.opening)
    frame.closing = clean_text(frame.closing)

    q = meta.get("quote_number", "")
    problems = [part for part, text, allowed in (("subject", frame.subject, q),
                                                  ("opening", frame.opening, q),
                                                  ("closing", frame.closing, q))
                if has_figures(text, allowed) or not text.strip()]
    if problems:
        print(f"GUARD: AI text had figures or was empty in the {', '.join(problems)} - using standard text there.")
        fb = FALLBACK[lang]
        frame = EmailFrame(
            subject=fb.subject.format(q=q) if "subject" in problems else frame.subject,
            opening=fb.opening if "opening" in problems else frame.opening,
            closing=fb.closing if "closing" in problems else frame.closing,
        )
    return frame


# ------------------------------------------------------------- 3. assemble
def assemble(frame, items, thanks, impact, lang):
    body = [frame.opening, "", TEXT[lang]["items_header"], ""]
    body += [f"{i}. {line}" for i, line in enumerate(items, start=1)]
    body += ["", impact]
    if thanks:
        body += [""] + thanks
    body += ["", frame.closing, "", SIGNOFF[lang]]
    return f"Subject: {frame.subject}\n\n" + "\n".join(body)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--tone", choices=TONES, default="cooperative")
    ap.add_argument("--lang", choices=LANGS, default="en")
    args = ap.parse_args()

    base = Path(args.pdf).with_suffix("")
    findings = pd.read_csv(f"{base}_findings.csv")
    meta = json.loads(Path(f"{base}_meta.json").read_text())

    items, thanks, impact = fact_lines(findings, args.lang)
    print("Asking the local AI to write the opening and closing...")
    frame = write_frame(meta, args.tone, args.lang)
    email = assemble(frame, items, thanks, impact, args.lang)

    out = Path(f"{base}_email_{args.lang}.txt")
    out.write_text(email)
    print("\n" + email + f"\n\nSaved {out.name}")
