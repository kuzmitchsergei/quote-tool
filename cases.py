"""
Case files: one per quotation, with the full negotiation history.

Everything is stored in ONE local file: cases/quote_cases.db (SQLite - no server, no installation;
back it up by copying the file). Nothing leaves the computer.

A case holds
  - the quotation data and the price analysis
  - the items we questioned, each with a status: open or closed
  - a timeline of events: quote received, request sent, supplier reply, assessment, follow-up sent

Each new round only checks the items that are still open.
"""
import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

FOLDER = Path(__file__).parent / "cases"
DB = FOLDER / "quote_cases.db"
CLOSED_VERDICTS = ("SUPPORTED", "PLAUSIBLE - CHECK DOCUMENTS", "CONCESSION OFFERED")


def _db():
    FOLDER.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY, created TEXT, supplier TEXT, quote_number TEXT,
            quote_date TEXT, status TEXT, round INTEGER, meta TEXT, findings TEXT, demo_key TEXT);
        CREATE TABLE IF NOT EXISTS items (
            case_id INTEGER, item_id TEXT, data TEXT, extra_cost REAL, status TEXT,
            verdict TEXT, updated_round INTEGER, decided_by TEXT,
            PRIMARY KEY (case_id, item_id));
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, case_id INTEGER, round INTEGER, kind TEXT, created TEXT,
            title TEXT, body TEXT, data TEXT);
    """)
    return con


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _extra_cost(findings, item):
    if item["id"] == "ALL":
        return float(findings.loc[findings["flag"] == "UNIFORM INCREASE", "extra_cost_eur"].sum())
    rows = findings.loc[findings["part_no"] == item["id"], "extra_cost_eur"]
    return float(rows.iloc[0]) if len(rows) else 0.0


def _clean(value):
    """JSON-safe: NaN -> None, numpy numbers -> float."""
    if isinstance(value, float) and value != value:
        return None
    if hasattr(value, "item"):
        return _clean(value.item())
    return value


# ------------------------------------------------------------------ create / read
def create_case(meta, findings, items, request_email, demo_key=None):
    con = _db()
    with con:
        cur = con.execute(
            "INSERT INTO cases (created, supplier, quote_number, quote_date, status, round, meta, "
            "findings, demo_key) VALUES (?,?,?,?,?,?,?,?,?)",
            (_now(), meta.get("supplier", ""), meta.get("quote_number", ""), meta.get("quote_date", ""),
             "open", 1, json.dumps(meta), findings.to_json(orient="records"), demo_key))
        cid = cur.lastrowid
        for it in items:
            clean = {k: _clean(v) for k, v in it.items()}
            con.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?)",
                        (cid, it["id"], json.dumps(clean), _extra_cost(findings, it), "open",
                         it["flag"], 0, "analysis"))
        extra = sum(_extra_cost(findings, it) for it in items)
        _event(con, cid, 0, "quote", "Quotation received and analysed",
               f"{len(findings)} lines analysed, {len(items)} points questioned, "
               f"EUR {extra:,.2f} above the cost index.")
        _event(con, cid, 1, "request", "Clarification request sent", request_email)
    return cid


def _event(con, cid, rnd, kind, title, body, data=None):
    con.execute("INSERT INTO events (case_id, round, kind, created, title, body, data) "
                "VALUES (?,?,?,?,?,?,?)", (cid, rnd, kind, _now(), title, body,
                                          json.dumps(data) if data is not None else None))


def list_cases():
    con = _db()
    rows = con.execute("""
        SELECT c.id, c.supplier, c.quote_number, c.quote_date, c.status, c.round, c.created,
               SUM(i.status = 'open') AS open_items, COUNT(i.item_id) AS items,
               SUM(CASE WHEN i.status = 'open' THEN i.extra_cost ELSE 0 END) AS open_amount
        FROM cases c LEFT JOIN items i ON i.case_id = c.id
        GROUP BY c.id ORDER BY c.id DESC""").fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def get_case(cid):
    con = _db()
    c = con.execute("SELECT * FROM cases WHERE id = ?", (cid,)).fetchone()
    if c is None:
        return None
    items = [dict(r) | {"data": json.loads(r["data"])}
             for r in con.execute("SELECT * FROM items WHERE case_id = ? ORDER BY rowid", (cid,))]
    events = [dict(r) for r in con.execute(
        "SELECT * FROM events WHERE case_id = ? ORDER BY id", (cid,))]
    return {"id": c["id"], "supplier": c["supplier"], "quote_number": c["quote_number"],
            "quote_date": c["quote_date"], "status": c["status"], "round": c["round"],
            "created": c["created"], "meta": json.loads(c["meta"]),
            "findings": pd.read_json(io.StringIO(c["findings"]), orient="records"),
            "demo_key": c["demo_key"], "items": items, "events": events}


def open_items(case):
    return [i["data"] for i in case["items"] if i["status"] == "open"]


# ------------------------------------------------------------------ a negotiation round
def save_round(cid, reply, verdict_rows, followup_email):
    """reply: dict(sender, date, subject, body). verdict_rows: list of dicts with item + verdict.
    Closes items whose verdict is in CLOSED_VERDICTS. Returns the new case status."""
    con = _db()
    with con:
        rnd = con.execute("SELECT round FROM cases WHERE id = ?", (cid,)).fetchone()["round"]
        who = f"{reply.get('sender') or 'Supplier'}" + (f", {reply['date']}" if reply.get("date") else "")
        _event(con, cid, rnd, "reply", f"Supplier reply received ({who})", reply.get("body", ""),
               {k: reply.get(k) for k in ("sender", "date", "subject")})
        closed = [r["item"] for r in verdict_rows if r["verdict"] in CLOSED_VERDICTS]
        summary = "\n".join(f"{r['item']}: {r['verdict']}" for r in verdict_rows)
        _event(con, cid, rnd, "assessment",
               f"Reply assessed: {len(closed)} closed, {len(verdict_rows) - len(closed)} still open",
               summary, verdict_rows)
        for r in verdict_rows:
            con.execute("UPDATE items SET verdict = ?, updated_round = ?, decided_by = 'rules', "
                        "status = ? WHERE case_id = ? AND item_id = ?",
                        (r["verdict"], rnd, "closed" if r["verdict"] in CLOSED_VERDICTS else "open",
                         cid, r["item"]))
        still_open = con.execute("SELECT COUNT(*) FROM items WHERE case_id = ? AND status = 'open'",
                                 (cid,)).fetchone()[0]
        if still_open:
            _event(con, cid, rnd, "followup", "Follow-up sent", followup_email)
            con.execute("UPDATE cases SET round = ? WHERE id = ?", (rnd + 1, cid))
            status = "open"
        else:
            _event(con, cid, rnd, "closed", "All points resolved - case closed", followup_email)
            status = "closed"
        con.execute("UPDATE cases SET status = ? WHERE id = ?", (status, cid))
    return status


def set_status(cid, item_id, status, note="buyer"):
    """Manual decision by the buyer (close or reopen an item)."""
    con = _db()
    with con:
        rnd = con.execute("SELECT round FROM cases WHERE id = ?", (cid,)).fetchone()["round"]
        con.execute("UPDATE items SET status = ?, decided_by = ? WHERE case_id = ? AND item_id = ?",
                    (status, note, cid, item_id))
        _event(con, cid, rnd, "decision", f"{item_id} {'closed' if status == 'closed' else 'reopened'} "
                                          f"by buyer", "")
        open_n = con.execute("SELECT COUNT(*) FROM items WHERE case_id = ? AND status = 'open'",
                             (cid,)).fetchone()[0]
        con.execute("UPDATE cases SET status = ? WHERE id = ?", ("open" if open_n else "closed", cid))


# ------------------------------------------------------------------ audit trail
def export_text(cid):
    """The negotiation record as plain text - for the file, the auditor or the next buyer."""
    c = get_case(cid)
    out = [f"NEGOTIATION RECORD - {c['supplier']}, quotation {c['quote_number']} ({c['quote_date']})",
           f"Case opened {c['created']}; status: {c['status']}; exported {_now()}", "",
           "POINTS QUESTIONED"]
    for i in c["items"]:
        d = i["data"]
        out.append(f"- {d['id']} {d.get('label', '')}: {i['status'].upper()} "
                   f"(last verdict: {i['verdict']}, decided by {i['decided_by']}, "
                   f"extra cost EUR {i['extra_cost']:,.2f})")
    out += ["", "TIMELINE"]
    for e in c["events"]:
        out += ["", f"[{e['created']}] Round {e['round']} - {e['title']}", "-" * 70, e["body"] or ""]
    return "\n".join(out)
