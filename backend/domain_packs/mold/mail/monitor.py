"""Bounded, testable IMAP polling for the MoldPilot mail worker."""

from __future__ import annotations

import hashlib
import imaplib
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from .parser import MAX_MESSAGE_BYTES, ParsedMail, parse_message
from .rules import MailRouteRule, classify_routes

MAX_POLL_BYTES = 200 * 1024 * 1024
MAX_SEARCH_RANGE_SIZE = 10_000
MAX_SEARCH_RESULT_UIDS = 20_000


@dataclass(frozen=True)
class MailRouteConfig:
    route_id: str
    name: str
    folder: str
    direction: str
    category: str
    priority: int = 100
    matcher: dict[str, Any] | None = None
    allowed_senders: tuple[str, ...] = ()
    notify_inbox: bool = True
    archive: bool = True

    def as_rule(self) -> MailRouteRule:
        return MailRouteRule(
            route_id=self.route_id,
            name=self.name,
            folder=self.folder,
            direction=self.direction,
            category=self.category,
            priority=self.priority,
            matcher=self.matcher,
            allowed_senders=self.allowed_senders,
            notify_inbox=self.notify_inbox,
            archive=self.archive,
        )


@dataclass(frozen=True)
class MailMonitorConfig:
    account_id: str
    account_name: str
    host: str
    port: int
    username: str
    password: str
    folder: str = "INBOX"
    transport: str = "ssl"
    allowed_senders: tuple[str, ...] = ()
    keywords: dict[str, tuple[str, ...]] | None = None
    routes: tuple[MailRouteConfig, ...] = ()


@dataclass(frozen=True)
class MailCursor:
    uid_validity: str = ""
    last_uid: int = 0


@dataclass(frozen=True)
class PollStats:
    scanned: int = 0
    accepted: int = 0
    ignored: int = 0
    failed: int = 0
    bytes_read: int = 0
    next_uid: int = 0


class MailLedger(Protocol):
    def load_cursor(self, account_id: str, folder: str = "INBOX") -> MailCursor: ...
    def save_cursor(self, account_id: str, cursor: MailCursor, folder: str = "INBOX") -> None: ...
    def record_message(self, account: MailMonitorConfig, uid: int, uid_validity: str, raw: bytes, parsed: ParsedMail, raw_sha256: str, archive_path: str) -> None: ...
    def record_failure(self, account: MailMonitorConfig, uid: int, uid_validity: str, raw_sha256: str, error: str) -> None: ...


def _status_number(response: Any, key: str) -> int | None:
    text = _response_text(response)
    match = re.search(rf"{key}\s+(\d+)", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(\d+)\b", text)
    return int(match.group(1)) if match else None


def _response_text(response: Any) -> str:
    if isinstance(response, (bytes, bytearray)):
        return bytes(response).decode("ascii", errors="ignore")
    if isinstance(response, (list, tuple)):
        return " ".join(_response_text(item) for item in response)
    return str(response or "")


def _fetch_rfc822(response: Any) -> bytes:
    """Extract RFC822 bytes from imaplib and strict fake-client responses."""
    if isinstance(response, tuple):
        for item in response:
            if isinstance(item, (bytes, bytearray)) and (b"\r\n" in item or b"From:" in item):
                return bytes(item)
            if isinstance(item, tuple):
                payload = _fetch_rfc822(item)
                if payload:
                    return payload
    if isinstance(response, list):
        for item in response:
            payload = _fetch_rfc822(item)
            if payload:
                return payload
    if isinstance(response, (bytes, bytearray)) and b"From:" in response:
        return bytes(response)
    return b""


def _legacy_classification(parsed: ParsedMail) -> ParsedMail:
    """Keep the pre-route parser behavior for existing configured accounts."""
    matched = bool(parsed.sender_allowed and (parsed.documents or parsed.attachments))
    return replace(
        parsed,
        mail_category=parsed.business_types[0] if parsed.business_types else "",
        category_status="MATCHED" if matched else "UNMATCHED",
        category_reason="兼容旧版文档分类" if matched else "发件人或结构化文档未通过旧版规则",
    )


class MailMonitor:
    def __init__(
        self,
        config: MailMonitorConfig,
        ledger: MailLedger,
        *,
        client_factory: Callable[[MailMonitorConfig], Any] | None = None,
        archive_root: str | Path | None = None,
    ):
        self.config = config
        self.ledger = ledger
        self.client_factory = client_factory or self._default_client
        self.archive_root = Path(archive_root) if archive_root else Path(tempfile.gettempdir()) / "moldpilot-mail-archive"

    @staticmethod
    def _default_client(config: MailMonitorConfig):
        if config.transport == "starttls":
            client = imaplib.IMAP4(config.host, config.port)
            client.starttls()
        else:
            client = imaplib.IMAP4_SSL(config.host, config.port)
        client.login(config.username, config.password)
        return client

    def _archive(self, raw: bytes, digest: str) -> str:
        directory = self.archive_root / self.config.account_name / self.config.folder / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest}.eml"
        if not target.exists():
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(raw)
            try:
                temporary.replace(target)
            except FileExistsError:
                temporary.unlink(missing_ok=True)
        return str(target)

    def _load_cursor(self) -> MailCursor:
        try:
            return self.ledger.load_cursor(self.config.account_id, self.config.folder)
        except TypeError:
            return self.ledger.load_cursor(self.config.account_id)

    def _save_cursor(self, cursor: MailCursor) -> None:
        try:
            self.ledger.save_cursor(self.config.account_id, cursor, self.config.folder)
        except TypeError:
            self.ledger.save_cursor(self.config.account_id, cursor)

    def _classify(self, parsed: ParsedMail) -> ParsedMail:
        if not self.config.routes:
            return _legacy_classification(parsed)
        rules = [route.as_rule() for route in self.config.routes if route.folder == self.config.folder]
        route, status, reason = classify_routes(parsed, rules)
        return replace(
            parsed,
            mail_category=route.category if route else "",
            category_status=status,
            category_reason=reason,
        )

    def poll_once(self) -> PollStats:
        cursor = self._load_cursor()
        client = self.client_factory(self.config)
        scanned = accepted = ignored = failed = bytes_read = 0
        next_uid = cursor.last_uid
        try:
            client.select(self.config.folder, readonly=True)
            status_type, status_data = client.status(self.config.folder, "(UIDVALIDITY UIDNEXT)")
            if str(status_type).upper() != "OK":
                raise RuntimeError("IMAP STATUS failed")
            status_text = _response_text(status_data)
            uid_validity = str(_status_number(status_text, "UIDVALIDITY") or cursor.uid_validity)
            uidnext = _status_number(status_text, "UIDNEXT") or (cursor.last_uid + 1)
            start_uid = cursor.last_uid + 1 if uid_validity == cursor.uid_validity else 1
            next_uid = cursor.last_uid if uid_validity == cursor.uid_validity else 0
            end_uid = min(uidnext - 1, start_uid + MAX_SEARCH_RANGE_SIZE - 1)
            if end_uid < start_uid:
                self._save_cursor(MailCursor(uid_validity, cursor.last_uid))
                return PollStats(next_uid=cursor.last_uid)
            _kind, search_data = client.uid("search", None, f"UID {start_uid}:{end_uid}")
            raw_uids = re.findall(r"\d+", _response_text(search_data))[:MAX_SEARCH_RESULT_UIDS]
            for raw_uid in raw_uids:
                uid = int(raw_uid)
                scanned += 1
                if bytes_read >= MAX_POLL_BYTES:
                    break
                _kind, fetched = client.uid("fetch", str(uid), "(RFC822)")
                raw = _fetch_rfc822(fetched)
                if not raw:
                    failed += 1
                    self.ledger.record_failure(self.config, uid, uid_validity, "", "IMAP FETCH 未返回 RFC822 内容")
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                if len(raw) > MAX_MESSAGE_BYTES or bytes_read + len(raw) > MAX_POLL_BYTES:
                    failed += 1
                    self.ledger.record_failure(self.config, uid, uid_validity, digest, "邮件或轮询总大小超过解析上限")
                    continue
                bytes_read += len(raw)
                try:
                    parsed = parse_message(
                        raw,
                        subject_stem=f"uid-{uid}",
                        allowed_senders=self.config.allowed_senders,
                        keywords=self.config.keywords,
                    )
                    parsed = self._classify(parsed)
                    archive_path = self._archive(raw, digest)
                    self.ledger.record_message(self.config, uid, uid_validity, raw, parsed, digest, archive_path)
                    if parsed.category_status == "MATCHED":
                        accepted += 1
                    else:
                        ignored += 1
                except Exception as exc:
                    failed += 1
                    self.ledger.record_failure(self.config, uid, uid_validity, digest, str(exc)[:1000])
                next_uid = max(next_uid, uid)
                self._save_cursor(MailCursor(uid_validity, next_uid))
            return PollStats(scanned, accepted, ignored, failed, bytes_read, next_uid)
        finally:
            try:
                client.logout()
            except Exception:
                pass


class MailMonitorWorker:
    """One-cycle worker adapter; lease acquisition is delegated to the host."""

    def __init__(self, accounts: Callable[[], list[MailMonitorConfig]], ledger: MailLedger, *, owner: str):
        self.accounts = accounts
        self.ledger = ledger
        self.owner = owner

    def run_cycle(self, *, client_factory: Callable[[MailMonitorConfig], Any] | None = None, archive_root: str | Path | None = None) -> dict[str, PollStats]:
        results = {}
        for account in self.accounts():
            monitor = MailMonitor(account, self.ledger, client_factory=client_factory, archive_root=archive_root)
            key = account.account_id if account.folder == "INBOX" else f"{account.account_id}:{account.folder}"
            results[key] = monitor.poll_once()
        return results
