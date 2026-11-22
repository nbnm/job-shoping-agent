"""A local, read-only Gmail Takeout importer and search CLI."""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import zipfile
from contextlib import closing
from datetime import date, datetime, timezone
from email import policy
from email.errors import MessageError
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath


DEFAULT_DIR = Path.home() / ".local/share/gmail-archive"
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
MAX_BODY_CHARS = 500_000
PARSER = BytesParser(policy=policy.default)
JUNK = {"spam", "trash", "deleted", "draft", "drafts"}
LOW_SIGNAL = {"promotions", "social", "forums"}
PURCHASE = re.compile(
    r"\b(order confirmation|order (?:confirmed|received|placed|cancelled|canceled)|"
    r"thank you for (?:your order|your purchase|shopping)|thanks for (?:your order|your purchase)|"
    r"(?:your |payment |purchase |order |tax |digital )?receipt|invoice|"
    r"order (?:number|no\.?)|order\s*#\s*[a-z0-9][a-z0-9_-]*|(?:has |have )?shipped|"
    r"(?:return|refund) (?:confirmation|received|approved|processed|issued)|"
    r"(?:is |was )?(?:on its way|out for delivery))\b", re.I,
)
APPAREL = re.compile(
    r"\b(shoes?|sneakers?|trainers?|footwear|boots?|sandals?|loafers?|heels?|"
    r"coats?|jackets?|blazers?|parkas?|vests?|hoodies?|sweaters?|cardigans?|"
    r"shirts?|t-?shirts?|tees?|blouses?|polo|trousers?|pants|jeans|denim|chinos|shorts|"
    r"dress(?:es)?|skirts?|socks?|underwear|leggings|activewear|swimwear|"
    r"apparel|clothing|slippers|suits?|scarves|scarf|beanies?|gloves?|hats?)\b", re.I,
)
EVENT = re.compile(
    r"\b(appointment|booking|reservation|itinerary|boarding pass|e-ticket|"
    r"interview|offer letter|enrollment|admission|graduation|lease|"
    r"rental agreement|moving confirmation|wedding invitation)\b", re.I,
)
SECURITY = re.compile(
    r"\b(verification code|security code|one[- ]time (?:code|password|passcode)|"
    r"password reset|reset your password|sign-in alert|security alert)\b", re.I,
)
TERMINAL_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]")
EXPORT_LABEL_HEADER = re.compile(rb"(?im)^X-Gmail-Labels:[^\n]*(?:\n[ \t][^\n]*)*\n?")

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    member TEXT,
    position INTEGER NOT NULL,
    message_id TEXT NOT NULL,
    sent_at TEXT,
    sender TEXT NOT NULL,
    recipients TEXT NOT NULL,
    subject TEXT NOT NULL,
    labels TEXT NOT NULL,
    body TEXT NOT NULL,
    timeline INTEGER NOT NULL,
    receipt INTEGER NOT NULL,
    wardrobe INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS message_text USING fts5(
    subject, sender, recipients, body, content='messages', content_rowid='seq'
);
CREATE TRIGGER IF NOT EXISTS message_insert AFTER INSERT ON messages BEGIN
    INSERT INTO message_text(rowid, subject, sender, recipients, body)
    VALUES (new.seq, new.subject, new.sender, new.recipients, new.body);
END;
"""


def private_path(root, name, *, directory=False, create=False):
    path = root / name
    if create and directory:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists() and not path.is_symlink():
        if create and not directory:
            with path.open("xb"):
                pass
        else:
            raise ValueError(f"Missing {path}. Run init first.")
    mode = path.lstat()
    right_type = stat.S_ISDIR(mode.st_mode) if directory else stat.S_ISREG(mode.st_mode)
    if not right_type or mode.st_uid != os.getuid() or mode.st_mode & 0o077:
        raise ValueError(f"{path} must be owned by you, private (0700/0600), and not a symlink.")
    return path


def db_path(root, *, initialize=False):
    resolved = root.resolve()
    if any((p / ".git").exists() for p in (resolved, *resolved.parents)):
        raise ValueError("Choose a data directory outside any Git checkout.")
    private_path(root.parent, root.name, directory=True, create=initialize)
    if initialize:
        private_path(root, "incoming", directory=True, create=True)
    return private_path(root, "mail.sqlite3", create=initialize)


def mbox_records(stream):
    """Yield (record number, bytes); an oversized record is represented by None."""
    position = 0
    data = bytearray()
    overflow = False
    at_line_start = True
    while line := stream.readline(1024 * 1024):
        if at_line_start and line.startswith(b"From "):
            if position:
                yield position, None if overflow else bytes(data)
            position += 1
            data.clear()
            overflow = False
        elif position and not overflow:
            if len(data) + len(line) > MAX_MESSAGE_BYTES:
                overflow = True
                data.clear()
            else:
                data.extend(line)
        elif not position and line.strip():
            raise ValueError("Expected an MBOX file beginning with a From separator.")
        at_line_start = line.endswith(b"\n")
    if position:
        yield position, None if overflow else bytes(data)


class VisibleText(HTMLParser):
    hidden_tags = {"script", "style", "head", "template", "noscript"}
    breaks = {"br", "p", "div", "li", "tr", "table", "section", "h1", "h2", "h3"}

    def __init__(self):
        super().__init__()
        self.hidden = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.hidden_tags:
            self.hidden.append(tag)
        elif not self.hidden:
            if tag in self.breaks:
                self.parts.append("\n")
            elif tag in {"td", "th"}:
                self.parts.append(" | ")

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        elif not self.hidden and tag in self.breaks:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag not in self.hidden_tags:
            self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean(text):
    return TERMINAL_CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def body_text(msg):
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None or part.get_filename():
        return ""
    try:
        value = part.get_content()
    except (UnicodeError, LookupError):
        value = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        parser = VisibleText()
        parser.feed(value)
        parser.close()
        value = "\n".join(
            line for row in "".join(parser.parts).splitlines() if (line := " ".join(row.split()))
        )
    return clean(value)[:MAX_BODY_CHARS]


def utc_date(value):
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


def gmail_labels(msg):
    labels = set()
    for header in msg.get_all("X-Gmail-Labels", []):
        for label in clean(str(header)).split(","):
            if label := label.strip():
                labels.add(label)
    return sorted(labels)


def parse_record(raw, source, member, position):
    msg = PARSER.parsebytes(raw)
    labels = gmail_labels(msg)
    kinds = {label.casefold() for label in labels}
    if kinds & JUNK:
        return None
    subject = clean(str(msg.get("Subject", "")))
    sender = clean(str(msg.get("From", "")))
    recipients = clean(", ".join(str(msg.get(field, "")) for field in ("To", "Cc", "Bcc")
                                 if msg.get(field)))
    body = body_text(msg)
    low_signal = {label.replace("category_", "").removeprefix("category ") for label in kinds}
    campaign = bool(low_signal & LOW_SIGNAL)
    bulk = bool(msg.get("List-Id")) or str(msg.get("Precedence", "")).strip().lower() in {
        "bulk", "list", "junk"
    }
    auto = str(msg.get("Auto-Submitted", "no")).split(";", 1)[0].strip().lower() != "no"
    security = bool(SECURITY.search(subject))
    # A product word alone doesn't turn a promotion into a purchase.
    receipt = not security and (bool(PURCHASE.search(subject)) or (
        not campaign and not bulk and bool(PURCHASE.search(body[:12_000]))
    ))
    wardrobe = receipt and bool(APPAREL.search(subject + "\n" + body[:30_000]))
    timeline = not security and (
        ("sent" in kinds or not (campaign or bulk or auto or receipt))
        or bool(EVENT.search(subject)) and not campaign
    )
    headers, _, payload = raw.replace(b"\r\n", b"\n").partition(b"\n\n")
    fingerprint = EXPORT_LABEL_HEADER.sub(b"", headers).rstrip(b"\n") + b"\n\n" + payload.rstrip(b"\n")
    return (
        hashlib.sha256(fingerprint).hexdigest(), source, member, position,
        clean(str(msg.get("Message-ID", ""))), utc_date(str(msg.get("Date", ""))),
        sender, recipients, subject, json.dumps(labels, ensure_ascii=False), body,
        int(timeline), int(receipt), int(wardrobe),
    )


def import_stream(db, stream, source, member, counts):
    for position, raw in mbox_records(stream):
        if raw is None:
            counts["oversized"] += 1
            continue
        try:
            row = parse_record(raw, source, member, position)
        except (MessageError, UnicodeError, LookupError, ValueError, RecursionError):
            counts["unreadable"] += 1
            continue
        if row is None:
            counts["excluded"] += 1
            continue
        result = db.execute("""
            INSERT INTO messages(id, source, member, position, message_id, sent_at, sender,
                                 recipients, subject, labels, body, timeline, receipt, wardrobe)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING
        """, row)
        counts["indexed" if result.rowcount else "duplicates"] += 1
        if position % 500 == 0:
            db.commit()
    db.commit()


def import_files(db, root):
    incoming = private_path(root, "incoming", directory=True)
    sources = sorted(p for p in incoming.iterdir() if p.suffix.lower() in {".mbox", ".zip"})
    if not sources:
        raise ValueError(f"No .zip or .mbox files in {incoming}.")
    counts = dict(indexed=0, duplicates=0, excluded=0, oversized=0, unreadable=0)
    skipped = 0
    for source in sources:
        path = private_path(incoming, source.name)
        if path.suffix.lower() == ".mbox":
            with path.open("rb") as stream:
                import_stream(db, stream, path.name, None, counts)
        else:
            with zipfile.ZipFile(path) as archive:
                members = [entry for entry in archive.infolist() if not entry.is_dir()
                           and PurePosixPath(entry.filename).parent.parts[-1:] == ("Mail",)
                           and PurePosixPath(entry.filename).suffix.lower() == ".mbox"]
                if not members:
                    skipped += 1
                    continue
                for entry in members:
                    with archive.open(entry) as stream:
                        import_stream(db, stream, path.name, entry.filename, counts)
    print(json.dumps({"mailbox_files_without_mail": skipped, **counts}, indent=2))
    if skipped == len(sources):
        raise ValueError("No Gmail MBOX members found in the ZIP files.")


SUMMARY_COLUMNS = "m.seq, m.id, m.sent_at, m.sender, m.subject, m.labels"
BATCH_COLUMNS = {"timeline": "timeline", "wardrobe": "wardrobe", "receipts": "receipt"}


def write_row(row):
    item = dict(row)
    item["labels"] = json.loads(item["labels"])
    print(json.dumps(item, ensure_ascii=True))


def positive_limit(value):
    number = int(value)
    if not 1 <= number <= 10_000:
        raise argparse.ArgumentTypeError("use a limit from 1 to 10000")
    return number


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("use a nonnegative cursor")
    return number


def main():
    os.umask(0o077)
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--data-dir", type=Path, default=DEFAULT_DIR)
    actions = cli.add_subparsers(dest="action", required=True)
    for name in ("init", "import", "stats"):
        actions.add_parser(name)
    search = actions.add_parser("search", help="Search indexed headers and message text with SQLite FTS5.")
    search.add_argument("query")
    search.add_argument("--limit", type=positive_limit, default=30)
    show = actions.add_parser("show", help="Read one message using its full archive ID.")
    show.add_argument("id")
    show.add_argument("--json", action="store_true", help="Write the source and text as JSON.")
    batch = actions.add_parser("batch", help="Write candidate records as JSON Lines.")
    batch.add_argument("purpose", choices=BATCH_COLUMNS)
    batch.add_argument("--since", type=date.fromisoformat, help="Inclusive UTC date (YYYY-MM-DD).")
    batch.add_argument("--before", type=date.fromisoformat, help="Exclusive UTC date (YYYY-MM-DD).")
    batch.add_argument("--after-seq", type=nonnegative, default=0, help="Resume after the last seq.")
    batch.add_argument("--limit", type=positive_limit, default=100)
    batch.add_argument("--include-body", action="store_true", help="Include private message text.")
    args = cli.parse_args()
    root = args.data_dir.expanduser().absolute()
    write = args.action in {"init", "import"}
    try:
        database = db_path(root, initialize=args.action == "init")
        uri = database.as_uri() + ("?mode=rw" if write else "?mode=ro")
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.row_factory = sqlite3.Row
            if args.action == "init":
                db.executescript(SCHEMA)
                print(root / "incoming")
            elif args.action == "import":
                import_files(db, root)
            elif args.action == "stats":
                result = db.execute("""
                    SELECT count(*) AS messages, min(sent_at) AS first_utc, max(sent_at) AS last_utc,
                           count(*) - count(sent_at) AS unknown_dates, count(nullif(timeline, 0)) AS timeline,
                           count(nullif(wardrobe, 0)) AS wardrobe, count(nullif(receipt, 0)) AS receipts
                    FROM messages
                """).fetchone()
                print(json.dumps(dict(result), indent=2))
            elif args.action == "show":
                result = db.execute("SELECT * FROM messages WHERE id = ?", (args.id,)).fetchone()
                if result is None:
                    raise ValueError("No message found for that ID.")
                if args.json:
                    write_row(result)
                else:
                    print(f"Subject: {result['subject']}\nFrom: {result['sender']}\n"
                          f"To: {result['recipients']}\nDate (UTC): {result['sent_at'] or 'Unknown'}\n"
                          f"Archive ID: {result['id']}\n\n{result['body']}")
            elif args.action == "search":
                for row in db.execute(f"""
                    SELECT {SUMMARY_COLUMNS} FROM message_text JOIN messages m ON m.seq = message_text.rowid
                    WHERE message_text MATCH ? ORDER BY rank, m.seq LIMIT ?
                """, (args.query, args.limit)):
                    write_row(row)
            elif args.action == "batch":
                conditions = [f"m.{BATCH_COLUMNS[args.purpose]} = 1", "m.seq > ?"]
                params = [args.after_seq]
                for day, operator in ((args.since, ">="), (args.before, "<")):
                    if day:
                        conditions.append(f"m.sent_at {operator} ?")
                        params.append(day.isoformat())
                body = ", m.body" if args.include_body else ""
                for row in db.execute(f"""
                    SELECT {SUMMARY_COLUMNS}{body} FROM messages m WHERE {" AND ".join(conditions)}
                    ORDER BY m.seq LIMIT ?
                """, (*params, args.limit)):
                    write_row(row)
    except (OSError, sqlite3.Error, ValueError, zipfile.BadZipFile, RuntimeError) as error:
        cli.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
