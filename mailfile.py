"""
Read a supplier's email from a file dropped into the tool - no mailbox connection needed.

  .eml  standard email file (Apple Mail, Outlook for Mac, Thunderbird, most web mail "download")
  .msg  Outlook for Windows (drag an email to the desktop). Read with the small 'olefile' library.
  .txt  plain text

Also removes the quoted earlier emails below a reply ("-----Original Message-----", "On ... wrote:",
lines starting with ">"). That matters: our own request quotes part numbers and percentages, and
the tool must only read what the supplier wrote.
"""
import email
import html
import re
from email import policy

QUOTE_MARKERS = re.compile(
    r"^\s*(-{2,}\s*original message\s*-{2,}|-{2,}\s*ursprüngliche nachricht\s*-{2,}|"
    r"_{10,}|on .{5,120} wrote:|am .{5,120} schrieb .{0,80}:|"
    r"(from|von):\s.+)$", re.I)


def strip_quoted(text, keep_first_lines=4):
    """Cut the reply at the first sign of a quoted earlier email.
    A 'From:' line within the first few lines is the reply's own header and is kept."""
    out = []
    for n, line in enumerate(text.splitlines()):
        if line.lstrip().startswith(">"):
            break
        if n >= keep_first_lines and QUOTE_MARKERS.match(line):
            break
        out.append(line)
    return "\n".join(out).strip()


def _html_to_text(raw):
    raw = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", "", raw))


def read_eml(data: bytes):
    msg = email.message_from_bytes(data, policy=policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part is not None else ""
    if part is not None and part.get_content_type() == "text/html":
        body = _html_to_text(body)
    return {"sender": str(msg.get("From", "") or ""), "date": str(msg.get("Date", "") or ""),
            "subject": str(msg.get("Subject", "") or ""), "body": body}


def _msg_fields(read):
    """read(tag) -> bytes or None, for MAPI property streams. Separated out so it can be tested."""
    def text(tag):
        raw = read(f"__substg1.0_{tag}001F")
        if raw is not None:
            return raw.decode("utf-16-le", errors="replace").rstrip("\x00")
        raw = read(f"__substg1.0_{tag}001E")
        return raw.decode("latin-1", errors="replace").rstrip("\x00") if raw is not None else ""

    body = text("1000")
    if not body:
        raw_html = read("__substg1.0_10130102")
        body = _html_to_text(raw_html.decode("utf-8", errors="replace")) if raw_html else ""
    headers = text("007D")
    date = next((l[5:].strip() for l in headers.splitlines() if l.lower().startswith("date:")), "")
    name, address = text("0C1A"), text("5D01") or text("0C1F")
    sender = f"{name} <{address}>" if name and address else name or address
    return {"sender": sender, "date": date, "subject": text("0037"), "body": body}


def read_msg(data: bytes):
    import io
    import olefile
    ole = olefile.OleFileIO(io.BytesIO(data))

    def read(name):
        return ole.openstream(name).read() if ole.exists(name) else None
    return _msg_fields(read)


def read_email_file(name, data: bytes):
    """Returns dict(sender, date, subject, body) with quoted earlier emails removed."""
    lower = name.lower()
    if lower.endswith(".eml"):
        mail = read_eml(data)
    elif lower.endswith(".msg"):
        mail = read_msg(data)
    else:
        mail = {"sender": "", "date": "", "subject": "", "body": data.decode("utf-8", errors="replace")}
    mail["body"] = strip_quoted(mail["body"])
    return mail
