"""Build a full DuckDB mail index and a shopping-only SQLite index from one MBOX."""

import argparse
import gzip
import hashlib
import json
import mmap
import multiprocessing
import os
import re
import sqlite3
import subprocess
import tempfile
from collections import Counter
from contextlib import closing
from email import policy
from email.errors import MessageError
from email.parser import BytesHeaderParser
from email.utils import parseaddr
from pathlib import Path

from archive import (
    APPAREL, EVENT, EXPORT_LABEL_HEADER, JUNK, LOW_SIGNAL, MAX_MESSAGE_BYTES,
    PARSER, SCHEMA, SECURITY, body_text, clean, gmail_labels, utc_date,
)


PURCHASE_LABELS = {"category purchases", "category_purchases", "purchases"}
PROMOTION_LABELS = {"category promotions", "category_promotions", "promotions"}
RUSSIAN_SYSTEM_LABELS = {
    "спам": "spam", "корзина": "trash", "удаленные": "deleted", "удалённые": "deleted",
    "черновики": "drafts", "отправленные": "sent", "категория покупок": "category purchases",
    "категория покупки": "category purchases", "категория промоакции": "category promotions",
    "категория соцсети": "category social", "категория форумы": "category forums",
}
NON_RETAIL = re.compile(
    r"\b(?:boarding pass|flight (?:booking|confirmation|receipt)|itinerary|"
    r"(?:hotel|car rental|table|restaurant) (?:booking|reservation)|"
    r"(?:subscription|membership) (?:renewal|renewed|receipt|payment|confirmation)|"
    r"(?:ride|trip) receipt|(?:electricity|water|mobile|internet|utility) bill|"
    r"(?:bank|credit card) statement|e-?transfer|money transfer|wire transfer|"
    r"(?:school|tax) payment|donation receipt|(?:renewal|renewed) of your subscription|"
    r"(?:earned|received|paid) a dividend|trade (?:confirmation|executed)|"
    r"your order has been filled|presto\s*[–-]\s*order|your (?:refund|tax) assessment)\b",
    re.I,
)
TRANSACTION = re.compile(
    r"\b(?:order (?:confirmation|confirmed|received|placed|canceled|cancelled|"
    r"update|status|details|ready|shipped|delivered|refunded|receipt)|"
    r"(?:confirming|confirmation (?:for|of)|thank you for|thanks for) your (?:recent )?(?:order|purchase)|"
    r"your (?:recent )?(?:online |store )?order.{0,85}(?:shipped|confirmed|received|"
    r"ready|delivered|on the way|on its way|canceled|cancelled|placed|refunded|dispatched)|"
    r"(?:return|refund) (?:confirmation|received|approved|processed|issued)|"
    r"(?:online|in-store|store|purchase|order) receipt|"
    r"confirmation de (?:votre )?commande|commande (?:confirmée|expédiée|livrée|annulée)|"
    r"(?:подтверждение заказа|заказ (?:подтвержд[её]н|оформлен|доставлен|отправлен)))\b|"
    r"\border\s*#\s*[\w-]*\d[\w-]*",
    re.I,
)
PARCEL = re.compile(
    r"\b(?:your (?:package|parcel|shipment)\b.{0,65}(?:on the way|on its way|shipped|"
    r"been delivered|was delivered|ready|arriv|track)|(?:package|parcel|shipment) "
    r"(?:delivered|delivery update|out for delivery|notification|confirmation)|"
    r"out for delivery|ready for (?:pick[ -]?up|collection)|"
    r"(?:suivi|livraison) de (?:votre )?colis)\b",
    re.I,
)
RECEIPT = re.compile(r"\b(?:receipt|invoice|proof of purchase|facture|reçu|refund|purchase)\b", re.I)
ORDER_DETAILS = re.compile(
    r"\b(?:order (?:number|date|total|summary|details)|shipping address|shipping method|"
    r"ship to|order\s*#|tracking (?:number|link)|numéro de commande|adresse de livraison)\b",
    re.I,
)
AD = re.compile(
    r"\b(?:shop now|buy now|flash sale|last chance|sale ends|place (?:an|your) order today|"
    r"free (?:shipping|delivery) on|abandoned cart|left .{0,30}in your (?:cart|bag)|"
    r"complete your (?:order|purchase)|\d+% off)\b|\b(?:save|up to)(?: an extra)? \d+%",
    re.I,
)
RETAIL_DOMAINS = re.compile(
    r"(?:^|\.)(?:amazon\.(?:ca|com|co\.uk)|ebay\.(?:ca|com)|etsy\.com|"
    r"aliexpress\.com|walmart\.(?:ca|com)|costco\.(?:ca|com)|bestbuy\.(?:ca|com)|"
    r"canadiantire\.ca|ikea\.com|lululemon\.com|decathlon\.(?:ca|com)|nike\.com|"
    r"adidas\.(?:ca|com)|sportchek\.ca|uniqlo\.com|ssense\.com|altitude-sports\.com)$",
    re.I,
)
NON_RETAIL_DOMAINS = re.compile(
    r"(?:^|\.)(?:wealthsimple\.com|kraken\.com|paytrie\.com|certs\.databricks\.com|"
    r"camis\.com|prestocard\.ca|tlscontact\.com)$", re.I,
)
RETAIL_STATUS = re.compile(r"^(?:ordered|shipped|delivered|dispatched|refunded|arriving):", re.I)
TNF_ORDER_ID = r"\s*#?\s*(?:CA|T)?\d{5,}"
TNF_ORDER = re.compile(rf"(?:Confirmation (?:of|for) (?:new|your) order{TNF_ORDER_ID}|Order{TNF_ORDER_ID} has shipped|"
                       rf"Return Receipt for (?:Your )?Order{TNF_ORDER_ID})", re.I)
ORDER_REFERENCE = re.compile(
    r"\b(?:order\s*(?:number|no\.?|#)|numéro de commande)\s*(?:is\b|[:#])?\s*"
    r"(?=[\w-]*\d)[\w-]{4,}\b", re.I,
)
HEADER_PARSER = BytesHeaderParser(policy=policy.default)
PARSE_ERRORS = (MessageError, UnicodeError, LookupError, ValueError, TypeError,
                AttributeError, AssertionError, IndexError, RecursionError)


def shopping_reason(labels, subject, sender, body, bulk):
    if labels & JUNK or SECURITY.search(subject) or NON_RETAIL.search(subject) or AD.search(subject):
        return None
    domain = parseaddr(sender)[1].split("@")[-1]
    if NON_RETAIL_DOMAINS.search(domain):
        return None
    retail = RETAIL_DOMAINS.search(domain)
    transaction = (TRANSACTION.search(subject) or (retail and RETAIL_STATUS.search(subject)) or
                   (re.search(r"(?:^|\.)thenorthface\.(?:com|ca)$", domain, re.I) and TNF_ORDER.fullmatch(subject.strip())))
    promo = labels & PROMOTION_LABELS
    if labels & PURCHASE_LABELS and (not promo or transaction):
        return "gmail_purchase_label"
    if transaction:
        return "order_or_return_subject"
    if promo:
        return None
    if PARCEL.search(subject):
        return "parcel_subject"
    body = body[:12_000]
    reference = ORDER_REFERENCE.search(body)
    if RECEIPT.search(subject) and (retail or (reference and ORDER_DETAILS.search(body))):
        return "receipt_with_retail_evidence"
    if not bulk and reference and TRANSACTION.search(body):
        return "order_reference_in_body"
    return None


def record_ranges(data):
    """Yield ordinal plus byte offsets for the complete MBOX record and its payload."""
    if not data[:5] == b"From ":
        raise ValueError("Expected a nonempty MBOX beginning with a From separator.")
    start, position = 0, 1
    while start < len(data):
        header = data.find(b"\n", start) + 1
        if not header:
            raise ValueError(f"Unterminated MBOX separator at byte {start}.")
        next_start = data.find(b"\nFrom ", header - 1) + 1
        end = next_start or len(data)
        yield position, start, header, end
        start, position = end, position + 1


def open_mailbox(path):
    global mailbox_file, mailbox_data
    mailbox_file = path.open("rb")
    mailbox_data = mmap.mmap(mailbox_file.fileno(), 0, access=mmap.ACCESS_READ)


def message_fields(msg):
    def text_header(field):
        return clean(str(msg.get(field, "")))

    return {
        "labels": gmail_labels(msg), "message_id": text_header("Message-ID"),
        "sent_at": utc_date(text_header("Date")), "sender": text_header("From"),
        "recipients": ", ".join(filter(None, map(text_header, ("To", "Cc", "Bcc")))),
        "subject": text_header("Subject"), "in_reply_to": text_header("In-Reply-To"),
    }


def parse_message(record):
    position, start, header, end = record
    length = end - header
    status = "ok" if length <= MAX_MESSAGE_BYTES else "oversized"
    raw = mailbox_data[header:end] if status == "ok" else None
    if raw is not None:
        hdr, _, content = raw.replace(b"\r\n", b"\n").partition(b"\n\n")
        digest = hashlib.sha256(EXPORT_LABEL_HEADER.sub(b"", hdr).rstrip(b"\n") + b"\n\n")
        digest.update(content.rstrip(b"\n"))
    else:
        hdr = re.split(rb"\r?\n\r?\n", mailbox_data[header:min(end, header + 262144)], 1)[0]
        with memoryview(mailbox_data)[header:end] as raw_view:
            digest = hashlib.sha256(raw_view)
    fields = {"labels": [], "message_id": "", "sent_at": None, "sender": "",
              "recipients": "", "subject": "", "in_reply_to": ""}
    body, attachments, bulk, auto, defects = "", [], False, False, []
    try:
        msg = PARSER.parsebytes(raw) if raw is not None else HEADER_PARSER.parsebytes(hdr)
        fields = message_fields(msg)
        if raw is not None:
            body = body_text(msg)
            for part in msg.walk():
                defects.extend(type(d).__name__ for d in part.defects)
                if part.get_filename() or part.get_content_disposition() == "attachment":
                    attachments.append({"filename": clean(part.get_filename() or ""),
                                        "content_type": part.get_content_type()})
        bulk = bool(msg.get("List-Id") or msg.get("List-Unsubscribe")) or str(msg.get("Precedence", "")).strip().lower() in {
            "bulk", "list", "junk",
        }
        auto = str(msg.get("Auto-Submitted", "no")).split(";", 1)[0].strip().lower() != "no"
    except PARSE_ERRORS as error:
        defects.append(type(error).__name__)
        status = "partial" if raw is not None else "oversized"
        try:
            fields = message_fields(HEADER_PARSER.parsebytes(hdr))
        except PARSE_ERRORS as header_error:
            defects.append(type(header_error).__name__)

    labels = {RUSSIAN_SYSTEM_LABELS.get(label.casefold().replace('"', ''), label.casefold())
              for label in fields["labels"]}
    subject = fields["subject"]
    reason = shopping_reason(labels, subject, fields["sender"], body, bulk)
    campaign = bool({label.removeprefix("category_").removeprefix("category ")
                     for label in labels} & LOW_SIGNAL)
    timeline = not (labels & JUNK or SECURITY.search(subject)) and (
        "sent" in labels or not (campaign or bulk or auto or reason)
        or bool(EVENT.search(subject)) and not campaign
    )
    return {
        "seq": position, "fingerprint": digest.hexdigest(), "position": position,
        "mbox_offset": start, "mbox_bytes": end - start, "header_offset": header,
        **fields, "body": body, "attachments": attachments, "parse_status": status,
        "parse_defects": sorted(set(defects)), "shopping_reason": reason,
        "timeline": int(timeline), "receipt": int(reason is not None),
        "wardrobe": int(bool(reason and APPAREL.search(subject + "\n" + body[:30_000]))),
    }


JSON_COLUMNS = """seq: 'BIGINT', id: 'VARCHAR', fingerprint: 'VARCHAR', position: 'BIGINT',
    mbox_offset: 'BIGINT', mbox_bytes: 'BIGINT', header_offset: 'BIGINT',
    labels: 'VARCHAR[]', message_id: 'VARCHAR', sent_at: 'VARCHAR', sender: 'VARCHAR',
    recipients: 'VARCHAR', subject: 'VARCHAR', in_reply_to: 'VARCHAR', body: 'VARCHAR',
    attachments: 'STRUCT(filename VARCHAR, content_type VARCHAR)[]', parse_status: 'VARCHAR',
    parse_defects: 'VARCHAR[]', shopping_reason: 'VARCHAR', timeline: 'INTEGER',
    receipt: 'INTEGER', wardrobe: 'INTEGER'"""


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def duckdb(database, sql, *, readonly=False):
    cmd = ["duckdb", "-batch", "-bail", "-no-stdin", "-init", os.devnull, "-json"]
    if readonly:
        cmd.append("-readonly")
    return subprocess.run([*cmd, str(database), "-c",
                           "SET autoinstall_known_extensions=false; SET TimeZone='UTC'; " + sql],
                          check=True, stdout=subprocess.PIPE, text=True).stdout


def publish_mail(source, root, workers):
    source_stat = source.stat()
    os.umask(0o077)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any((p / ".git").exists() for p in (root, *root.parents)):
        raise ValueError("Choose a data directory outside any Git checkout.")
    outputs = root / "mail.duckdb", root / "mail.sqlite3"
    if any(p.exists() or p.is_symlink() for p in outputs):
        raise ValueError("An output database already exists; choose another output directory.")

    with tempfile.TemporaryDirectory(prefix=".mail-import-", dir=root) as folder:
        folder = Path(folder)
        stage = folder / "messages.jsonl.gz"
        duck, sqlite = (folder / p.name for p in outputs)
        stats, seen = Counter(), Counter()
        with source.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            ranges = record_ranges(data)
            with multiprocessing.get_context("spawn").Pool(workers, open_mailbox, (source,)) as pool:
                with gzip.open(stage, "wt", encoding="utf-8", compresslevel=1) as spool:
                    for row in pool.imap(parse_message, ranges, chunksize=16):
                        key = row["fingerprint"]
                        seen[key] += 1
                        row["id"] = key if seen[key] == 1 else f"{key}-{seen[key]}"
                        stats["records"] += 1
                        stats["shopping"] += row["receipt"]
                        stats["wardrobe"] += row["wardrobe"]
                        stats[row["parse_status"]] += 1
                        spool.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                        if stats["records"] % 10000 == 0:
                            print(f"Parsed {stats['records']:,} messages", flush=True)

        current = source.stat()
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if signature(source_stat) != signature(current):
            raise ValueError("The input changed during import.")

        subset = folder / "shopping.jsonl.gz"
        print("Building DuckDB and exporting shopping rows", flush=True)
        duckdb(duck, f"""
            SET memory_limit='2GB'; SET threads=4;
            CREATE TABLE messages AS
                SELECT * REPLACE (sent_at::TIMESTAMPTZ AS sent_at),
                    {literal(source.name)}::VARCHAR AS source, NULL::VARCHAR AS member
                FROM read_ndjson({literal(stage)}, columns={{{JSON_COLUMNS}}},
                    maximum_object_size=16000000, compression='gzip');
            ALTER TABLE messages ADD PRIMARY KEY (seq);
            CREATE UNIQUE INDEX messages_id ON messages (id);
            CREATE VIEW shopping_messages AS SELECT * FROM messages WHERE shopping_reason IS NOT NULL;
            CREATE TABLE import_source AS SELECT {literal(source)}::VARCHAR AS path,
                {source_stat.st_size}::BIGINT AS bytes, {source_stat.st_mtime_ns}::BIGINT AS mtime_ns,
                current_timestamp AS imported_at, 'retail-v2'::VARCHAR AS shopping_rules;
            COPY (
                SELECT * REPLACE (strftime(sent_at, '%Y-%m-%dT%H:%M:%S+00:00') AS sent_at)
                FROM shopping_messages ORDER BY seq
            ) TO {literal(subset)} (FORMAT JSON, ARRAY false, COMPRESSION gzip);
            CHECKPOINT;
        """)
        stage.unlink()
        print("Building SQLite full-text search", flush=True)
        with closing(sqlite3.connect(sqlite)) as db:
            db.executescript(SCHEMA + """
                ALTER TABLE messages ADD COLUMN shopping_reason TEXT NOT NULL;
                ALTER TABLE messages ADD COLUMN mbox_offset INTEGER NOT NULL;
                ALTER TABLE messages ADD COLUMN mbox_bytes INTEGER NOT NULL;
                CREATE INDEX messages_sent_at ON messages (sent_at);
                CREATE TABLE import_source (path TEXT NOT NULL, bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL, shopping_rules TEXT NOT NULL);
            """)
            db.execute("INSERT INTO import_source VALUES (?,?,?,?)",
                       (str(source), source_stat.st_size, source_stat.st_mtime_ns, "retail-v2"))
            columns = ("seq", "id", "source", "member", "position", "message_id", "sent_at",
                       "sender", "recipients", "subject", "labels", "body", "timeline", "receipt",
                       "wardrobe", "shopping_reason", "mbox_offset", "mbox_bytes")
            with gzip.open(subset, "rt", encoding="utf-8") as rows:
                def values():
                    for line in rows:
                        row = json.loads(line)
                        row["labels"] = json.dumps(row["labels"], ensure_ascii=False)
                        yield tuple(row[name] for name in columns)

                db.executemany(f"INSERT INTO messages ({','.join(columns)}) "
                               f"VALUES ({','.join('?' for _ in columns)})", values())
            db.execute("INSERT INTO message_text(message_text) VALUES ('optimize')")
            db.execute("INSERT INTO message_text(message_text, rank) VALUES ('integrity-check', 1)")
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("SQLite integrity check failed.")
            actual = db.execute("SELECT count(*), count(CASE WHEN receipt=1 THEN 1 END), "
                                "count(CASE WHEN wardrobe=1 THEN 1 END) FROM messages").fetchone()
            if actual != (stats["shopping"], stats["shopping"], stats["wardrobe"]):
                raise ValueError("SQLite shopping totals did not match the import.")
            db.commit()

        result = json.loads(duckdb(duck, """
            WITH offsets AS (
                SELECT *, lag(mbox_offset + mbox_bytes, 1, 0) OVER (ORDER BY seq) AS expected,
                    row_number() OVER (ORDER BY seq) AS expected_seq FROM messages
            )
            SELECT count(*) AS records, sum(receipt) AS shopping, sum(wardrobe) AS wardrobe,
                count(*) FILTER (WHERE shopping_reason IS NOT NULL) AS shopping_view,
                count(*) FILTER (WHERE mbox_offset != expected OR seq != expected_seq
                    OR header_offset <= mbox_offset OR header_offset > mbox_offset + mbox_bytes) AS bad_offsets,
                max(mbox_offset + mbox_bytes) AS source_bytes,
                count(*) - count(DISTINCT fingerprint) AS duplicate_fingerprints,
                count(*) FILTER (WHERE sent_at IS NULL) AS unknown_dates,
                count(*) FILTER (WHERE len(attachments) > 0) AS messages_with_attachments,
                min(sent_at)::VARCHAR AS first_utc, max(sent_at)::VARCHAR AS last_utc
            FROM offsets;
        """, readonly=True))[0]
        if any(result[k] != stats[k] for k in ("records", "shopping", "wardrobe")) or (
            result["bad_offsets"] or result["source_bytes"] != source_stat.st_size
            or result["shopping_view"] != stats["shopping"]
        ):
            raise ValueError("DuckDB counts or source offsets did not match the import.")

        os.link(duck, outputs[0])
        try:
            os.link(sqlite, outputs[1])
        except OSError:
            outputs[0].unlink()
            raise
        print(json.dumps({**stats, **result}, indent=2))


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("mbox", type=Path)
    cli.add_argument("--output-dir", type=Path, required=True)
    cli.add_argument("--workers", type=int, default=4)
    args = cli.parse_args()
    if args.workers < 1:
        cli.error("--workers must be positive")
    publish_mail(args.mbox.expanduser().resolve(), args.output_dir.expanduser().resolve(), args.workers)


if __name__ == "__main__":
    main()
