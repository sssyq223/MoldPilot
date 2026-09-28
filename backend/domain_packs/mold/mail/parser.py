"""Extract structured spreadsheet-like documents from inbound email bodies.

Some NetEase mailbox exports contain the business table in the message body rather
than as a MIME attachment.  This module deliberately emits CSV bytes so the
existing import parser and its validation rules remain the single source of truth.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.header import decode_header
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


SUPPORTED_SUFFIXES = {".xlsx", ".csv", ".tsv"}
MAX_BODY_CHARS = 12 * 1024 * 1024
MAX_TABLE_ROWS = 100_000
MAX_TABLE_COLUMNS = 512
MAX_TABLE_CELL_CHARS = 256 * 1024
MAX_TABLE_ROW_CHARS = 4 * 1024 * 1024
MAX_TABLE_BLOCKS = 32
MAX_ATTACHMENT_COUNT = 128
MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024
MAX_MESSAGE_BYTES = 100 * 1024 * 1024

BUSINESS_TYPES = ("WEEKLY_KIT", "NEW_PRODUCT", "EXPORT_DAILY", "PLAN_13W")
DEFAULT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "WEEKLY_KIT": ("齐套", "周齐套", "一周齐套"),
    "NEW_PRODUCT": ("新品", "新品计划", "激光新品"),
    "EXPORT_DAILY": ("出口日输", "出口日输单", "出口机日输"),
    "PLAN_13W": ("13周", "13周预测", "十三周预测"),
}


class MailDocumentLimitError(ValueError):
    """Raised when an email body cannot be parsed without truncation."""


@dataclass(frozen=True)
class MailDocument:
    name: str
    data: bytes
    source: str
    media_type: str | None = None


@dataclass(frozen=True)
class ParsedMail:
    """Bounded, provider-neutral result used by the MoldPilot mail worker."""

    message_id: str
    subject: str
    sender: str
    source_sent_at: datetime | None
    sender_allowed: bool
    documents: tuple[MailDocument, ...]
    business_types: tuple[str, ...]
    classification_reasons: tuple[str, ...]
    plain_body: str
    html_body: str


def decode_header_text(value: str | None) -> str:
    """Decode RFC 2047 headers without allowing malformed bytes to escape."""
    parts: list[str] = []
    for raw, charset in decode_header(value or ""):
        if isinstance(raw, bytes):
            parts.append(raw.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(str(raw))
    return "".join(parts).strip()


def sender_is_allowed(sender_header: str, allowed_senders: Iterable[str]) -> bool:
    """Apply exact-address or domain rules and fail closed for multiple From addresses."""
    parsed = {
        address.strip().lower()
        for _, address in getaddresses([sender_header or ""])
        if address and "@" in address
    }
    if len(parsed) != 1:
        return False
    address = next(iter(parsed))
    for raw_rule in allowed_senders:
        rule = str(raw_rule or "").strip().lower()
        if not rule:
            continue
        if "@" in rule and not rule.startswith("@"):
            if rule == address:
                return True
        elif address.rsplit("@", 1)[-1] == rule.lstrip("@."):
            return True
    return False


def business_keyword_hits(text: str, keywords: dict[str, tuple[str, ...]] | None = None) -> dict[str, list[str]]:
    haystack = str(text or "").lower()
    result: dict[str, list[str]] = {}
    for business, words in (keywords or DEFAULT_KEYWORDS).items():
        if business not in BUSINESS_TYPES:
            continue
        matches = [str(word).strip() for word in words if str(word).strip() and str(word).strip().lower() in haystack]
        if matches:
            result[business] = list(dict.fromkeys(matches))
    return result


def classify_document(
    subject: str,
    filename: str,
    body: str,
    keywords: dict[str, tuple[str, ...]] | None = None,
) -> tuple[str | None, str]:
    """Classify by subject, then filename, then body; reject ambiguous matches."""
    active = keywords or DEFAULT_KEYWORDS
    for source_label, text in (("邮件主题", subject), ("附件名称", filename), ("邮件正文", body)):
        hits = business_keyword_hits(text, active)
        if not hits:
            continue
        if len(hits) > 1:
            return None, f"{source_label}同时命中多个业务类型：{', '.join(hits)}"
        business = next(iter(hits))
        return business, f"{source_label}命中关键词：{', '.join(hits[business])}"
    return None, "未命中监听关键词"


def _message_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_name(value: str, fallback: str = "mail") -> str:
    name = Path(value or fallback).name
    name = re.sub(r"[^\w.()\-\u4e00-\u9fff]+", "_", name, flags=re.UNICODE)
    return name[:160] or fallback


def _decode_filename(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return _safe_name(value or "attachment.xlsx", "attachment.xlsx")


def _attachment_documents(message: Message) -> list[MailDocument]:
    documents: list[MailDocument] = []
    seen: set[tuple[str, str]] = set()
    total_bytes = 0
    for part in message.walk():
        filename = part.get_filename()
        if not filename:
            continue
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        if len(payload) > MAX_ATTACHMENT_BYTES:
            raise MailDocumentLimitError("邮件单个附件超过100MB解析上限")
        total_bytes += len(payload)
        if total_bytes > MAX_ATTACHMENT_BYTES:
            raise MailDocumentLimitError("邮件附件总大小超过100MB解析上限")
        name = _decode_filename(filename)
        key = (name.lower(), hashlib.sha256(payload).hexdigest())
        if key in seen:
            continue
        seen.add(key)
        if len(documents) >= MAX_ATTACHMENT_COUNT:
            raise MailDocumentLimitError(f"邮件可解析附件数量不能超过{MAX_ATTACHMENT_COUNT}")
        documents.append(
            MailDocument(
                name=name,
                data=payload,
                source="attachment",
                media_type=part.get_content_type(),
            )
        )
    return documents


def _split_markdown_row(line: str) -> list[str]:
    text = line.strip()
    if len(text) > MAX_TABLE_ROW_CHARS:
        raise MailDocumentLimitError("邮件正文表格单行内容超过安全上限")
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False

    def append_cell() -> None:
        cell = "".join(current).replace("\\|", "|").strip()
        if len(cell) > MAX_TABLE_CELL_CHARS:
            raise MailDocumentLimitError("邮件正文表格单元格内容超过安全上限")
        if len(cells) >= MAX_TABLE_COLUMNS:
            raise MailDocumentLimitError(f"邮件正文表格列数不能超过{MAX_TABLE_COLUMNS}")
        cells.append(cell)

    for char in text:
        if char == "|" and not escaped:
            append_cell()
            current = []
            continue
        current.append(char)
        if len(current) > MAX_TABLE_CELL_CHARS + 2:
            raise MailDocumentLimitError("邮件正文表格单元格内容超过安全上限")
        escaped = char == "\\" and not escaped
        if char != "\\":
            escaped = False
    append_cell()
    return cells


def _is_markdown_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def _is_separator_row(row: list[str]) -> bool:
    return bool(row) and all(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")) for cell in row)


def _markdown_tables(text: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    block: list[list[str]] = []
    for line in text[:MAX_BODY_CHARS].splitlines():
        if _is_markdown_line(line):
            block.append(_split_markdown_row(line))
            if len(block) > MAX_TABLE_ROWS:
                raise MailDocumentLimitError("邮件正文表格超过 100000 行")
            continue
        if block:
            if len(block) >= 2:
                if len(tables) >= MAX_TABLE_BLOCKS:
                    raise MailDocumentLimitError(f"邮件正文表格数量不能超过{MAX_TABLE_BLOCKS}")
                tables.append(block)
            block = []
    if len(block) >= 2:
        if len(tables) >= MAX_TABLE_BLOCKS:
            raise MailDocumentLimitError(f"邮件正文表格数量不能超过{MAX_TABLE_BLOCKS}")
        tables.append(block)

    output: list[list[list[str]]] = []
    for rows in tables:
        width = max((len(row) for row in rows), default=0)
        if width < 2:
            continue
        normalized = [row + [""] * (width - len(row)) for row in rows]
        if len(normalized) > 1 and _is_separator_row(normalized[1]):
            normalized.pop(1)
        output.append(normalized)
    return output


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_chars = 0
        self._row_chars = 0
        self._row_count = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001 - stdlib callback
        tag = tag.lower()
        if tag == "table":
            if self._table is not None:
                if len(self.tables) >= MAX_TABLE_BLOCKS:
                    raise MailDocumentLimitError(f"邮件正文表格数量不能超过{MAX_TABLE_BLOCKS}")
                self.tables.append(self._table)
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
            self._row_chars = 0
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
            self._cell_chars = 0

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
            self._cell_chars += len(data)
            if self._cell_chars > MAX_TABLE_CELL_CHARS:
                raise MailDocumentLimitError("邮件正文表格单元格内容超过安全上限")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            cell = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            if len(cell) > MAX_TABLE_CELL_CHARS:
                raise MailDocumentLimitError("邮件正文表格单元格内容超过安全上限")
            if len(self._row) >= MAX_TABLE_COLUMNS:
                raise MailDocumentLimitError(f"邮件正文表格列数不能超过{MAX_TABLE_COLUMNS}")
            self._row.append(cell)
            self._row_chars += len(cell)
            if self._row_chars > MAX_TABLE_ROW_CHARS:
                raise MailDocumentLimitError("邮件正文表格单行内容超过安全上限")
            self._cell = None
            self._cell_chars = 0
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(value for value in self._row):
                self._row_count += 1
                if self._row_count > MAX_TABLE_ROWS:
                    raise MailDocumentLimitError("邮件正文表格超过 100000 行")
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if len(self.tables) >= MAX_TABLE_BLOCKS:
                raise MailDocumentLimitError(f"邮件正文表格数量不能超过{MAX_TABLE_BLOCKS}")
            self.tables.append(self._table)
            self._table = None

    def close(self) -> None:
        super().close()
        if self._table:
            if len(self.tables) >= MAX_TABLE_BLOCKS:
                raise MailDocumentLimitError(f"邮件正文表格数量不能超过{MAX_TABLE_BLOCKS}")
            self.tables.append(self._table)
            self._table = None


def _html_tables(text: str) -> list[list[list[str]]]:
    parser = _TableParser()
    try:
        parser.feed(text[:MAX_BODY_CHARS])
        parser.close()
    except MailDocumentLimitError:
        raise
    except Exception:
        return []
    output: list[list[list[str]]] = []
    for rows in parser.tables:
        width = max((len(row) for row in rows), default=0)
        if width < 2 or len(rows) < 2:
            continue
        output.append([row + [""] * (width - len(row)) for row in rows])
    return output


def _header_score(row: Iterable[str]) -> int:
    text = " ".join(str(value).lower() for value in row)
    tokens = (
        "物料", "组件", "mdm", "工厂", "需求", "数量", "生产", "交货", "库存",
        "齐套", "周", "bom", "供应商", "供方", "型号", "日期",
    )
    return sum(1 for token in tokens if token in text)


def _table_is_business_data(table: list[list[str]]) -> bool:
    return len(table) >= 2 and len(table[0]) >= 2 and _header_score(table[0]) >= 1


def _table_csv(table: list[list[str]]) -> bytes:
    # Never turn an oversized table into a silently truncated import.  The
    # caller treats this as a non-importable body document and leaves the mail
    # available for a later manual review or a corrected attachment.
    if len(table) > MAX_TABLE_ROWS:
        raise MailDocumentLimitError("邮件正文表格超过 100000 行")
    total_chars = 0
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for row in table:
        if len(row) > MAX_TABLE_COLUMNS:
            raise MailDocumentLimitError(f"邮件正文表格列数不能超过{MAX_TABLE_COLUMNS}")
        row_chars = 0
        for cell in row:
            text = str(cell or "")
            if len(text) > MAX_TABLE_CELL_CHARS:
                raise MailDocumentLimitError("邮件正文表格单元格内容超过安全上限")
            row_chars += len(text)
        if row_chars > MAX_TABLE_ROW_CHARS:
            raise MailDocumentLimitError("邮件正文表格单行内容超过安全上限")
        total_chars += row_chars
        if total_chars > MAX_BODY_CHARS:
            raise MailDocumentLimitError("邮件正文表格转换结果超过安全上限")
        writer.writerow(row)
    return output.getvalue().encode("utf-8-sig")


def body_documents(
    *,
    text_plain: str,
    text_html: str,
    stem: str,
) -> list[MailDocument]:
    # Parsing a prefix of a large body could produce a valid-looking but
    # incomplete CSV.  Fail closed before table detection when the configured
    # body budget is exceeded.
    if len(text_plain) > MAX_BODY_CHARS or len(text_html) > MAX_BODY_CHARS:
        raise MailDocumentLimitError("邮件正文超过解析上限")
    tables = _markdown_tables(text_plain) + _html_tables(text_html)
    candidates = [table for table in tables if _table_is_business_data(table)]
    # Forwarded messages commonly contain the current supplier-specific table
    # followed by larger historical tables from the quoted thread. Prefer the
    # highest-confidence header and, on a tie, the first table in message order.
    # Emitting several tables would import both the current and quoted versions.
    candidates.sort(key=lambda table: _header_score(table[0]), reverse=True)
    seen_tables: set[str] = set()
    selected: list[list[list[str]]] = []
    for table in candidates:
        if len(table) > MAX_TABLE_ROWS:
            raise MailDocumentLimitError("邮件正文表格超过 100000 行")
        fingerprint = "\x1f".join(
            "\x1e".join(re.sub(r"\s+", " ", str(cell)).strip() for cell in row)
            for row in table
        )
        if fingerprint in seen_tables:
            continue
        seen_tables.add(fingerprint)
        selected.append(table)
        break
    return [
        MailDocument(
            name=f"{_safe_name(stem, 'mail')}-正文表格-1.csv",
            data=_table_csv(table),
            source="body_table",
            media_type="text/csv",
        )
        for table in selected
    ]


def message_documents(data: bytes, stem: str) -> tuple[Message, list[MailDocument], str, str]:
    """Parse an RFC822 message and return message, documents, plain and HTML bodies."""
    if len(data) > MAX_MESSAGE_BYTES:
        raise MailDocumentLimitError("邮件原始内容超过100MB解析上限")
    message = BytesParser(policy=policy.default).parsebytes(data)
    attachments = _attachment_documents(message)
    text_plain = ""
    text_html = ""
    for part in message.walk():
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if content_type == "text/plain" and not text_plain:
            text_plain = str(content)
        elif content_type == "text/html" and not text_html:
            text_html = str(content)
    # Body tables are a fallback for the common no-attachment format.  If an
    # actual spreadsheet exists, it is authoritative and avoids duplicate imports.
    documents = attachments or body_documents(text_plain=text_plain, text_html=text_html, stem=stem)
    return message, documents, text_plain, text_html


def extract_structured_documents(message: Message, *, subject: str | None = None) -> list[MailDocument]:
    """Return spreadsheet attachments or structured tables embedded in a message.

    The monitor already parsed the RFC822 payload, so this adapter avoids a second
    parse and keeps the public helper small for callers and tests.
    """
    attachments = _attachment_documents(message)
    if attachments:
        return attachments
    text_plain = ""
    text_html = ""
    for part in message.walk():
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if content_type == "text/plain" and not text_plain:
            text_plain = str(content)
        elif content_type == "text/html" and not text_html:
            text_html = str(content)
    return body_documents(text_plain=text_plain, text_html=text_html, stem=subject or "mail")


def parse_message(
    data: bytes,
    *,
    subject_stem: str = "mail",
    allowed_senders: Iterable[str] = (),
    keywords: dict[str, tuple[str, ...]] | None = None,
) -> ParsedMail:
    """Parse one RFC822 message with the same bounded rules as the listener.

    The function intentionally performs no network or persistence work.  A
    worker can store the returned documents and ledger metadata transactionally.
    """
    message, documents, plain_body, html_body = message_documents(data, subject_stem)
    subject = decode_header_text(message.get("Subject"))
    sender_header = decode_header_text(message.get("From"))
    sender_addresses = [address.lower() for _, address in getaddresses([sender_header]) if address]
    sender = sender_addresses[0] if len(sender_addresses) == 1 else sender_header
    rules = tuple(str(item).strip() for item in allowed_senders if str(item).strip())
    sender_allowed = True if not rules else sender_is_allowed(sender_header, rules)

    body_for_classification = plain_body or html_body
    types: list[str] = []
    reasons: list[str] = []
    for document in documents:
        business, reason = classify_document(subject, document.name, body_for_classification, keywords)
        reasons.append(reason)
        if business and business not in types:
            types.append(business)

    message_id = decode_header_text(message.get("Message-ID"))
    if not message_id:
        message_id = f"sha256:{hashlib.sha256(data).hexdigest()}"
    return ParsedMail(
        message_id=message_id,
        subject=subject,
        sender=sender,
        source_sent_at=_message_date(message.get("Date")),
        sender_allowed=sender_allowed,
        documents=tuple(documents),
        business_types=tuple(types),
        classification_reasons=tuple(reasons),
        plain_body=plain_body,
        html_body=html_body,
    )
