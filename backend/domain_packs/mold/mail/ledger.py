"""SQLAlchemy ledger adapter for the injected mail monitor."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.ports.db import now

from .monitor import MailCursor, MailMonitorConfig
from .parser import ParsedMail


class SqlAlchemyMailLedger:
    def __init__(self, db):
        self.db = db

    def load_cursor(self, account_id: str) -> MailCursor:
        row = self.db.scalar(select(m.MailMonitorCursor).where(m.MailMonitorCursor.account_id == account_id))
        return MailCursor(row.uid_validity, row.last_uid) if row else MailCursor()

    def save_cursor(self, account_id: str, cursor: MailCursor) -> None:
        row = self.db.scalar(select(m.MailMonitorCursor).where(m.MailMonitorCursor.account_id == account_id))
        if row is None:
            row = m.MailMonitorCursor(account_id=account_id, uid_validity=cursor.uid_validity, last_uid=cursor.last_uid, last_polled_at=now())
            self.db.add(row)
        else:
            row.uid_validity = cursor.uid_validity
            row.last_uid = cursor.last_uid
            row.last_polled_at = now()
        self.db.flush()

    def record_message(self, account: MailMonitorConfig, uid: int, uid_validity: str, raw: bytes, parsed: ParsedMail, raw_sha256: str, archive_path: str) -> None:
        existing = self.db.scalar(select(m.MailMessage).where(
            m.MailMessage.account_id == account.account_id,
            m.MailMessage.uid_validity == uid_validity,
            m.MailMessage.uid == uid,
        ))
        if existing:
            return
        row = m.MailMessage(
            account_id=account.account_id, uid_validity=uid_validity, uid=uid,
            message_id=parsed.message_id, subject=parsed.subject, sender=parsed.sender,
            source_sent_at=parsed.source_sent_at, received_at=now(), raw_sha256=raw_sha256,
            outcome=("IMPORTED" if parsed.sender_allowed and parsed.documents else "IGNORED_SENDER" if not parsed.sender_allowed else "IGNORED_NO_DOCUMENT"),
            detail_json={"archive_path": archive_path, "business_types": list(parsed.business_types), "classification_reasons": list(parsed.classification_reasons)},
        )
        self.db.add(row)
        self.db.flush()
        for index, document in enumerate(parsed.documents):
            self.db.add(m.MailDocument(
                message_id=row.id, filename=document.name, source=document.source,
                media_type=document.media_type or "", sha256=__import__("hashlib").sha256(document.data).hexdigest(),
                byte_size=len(document.data), business_type=parsed.business_types[index] if index < len(parsed.business_types) else "",
                classification_reason=parsed.classification_reasons[index] if index < len(parsed.classification_reasons) else "",
                import_status="PENDING",
            ))
        self.db.flush()

    def record_failure(self, account: MailMonitorConfig, uid: int, uid_validity: str, raw_sha256: str, error: str) -> None:
        row = self.db.scalar(select(m.MailMessage).where(
            m.MailMessage.account_id == account.account_id,
            m.MailMessage.uid_validity == uid_validity,
            m.MailMessage.uid == uid,
        ))
        if row is None:
            row = m.MailMessage(account_id=account.account_id, uid_validity=uid_validity, uid=uid, raw_sha256=raw_sha256 or "", outcome="FAILED", error_message=error[:1000], received_at=now(), detail_json={})
            self.db.add(row)
        else:
            row.outcome = "FAILED"
            row.error_message = error[:1000]
            row.retry_count += 1
        self.db.flush()
