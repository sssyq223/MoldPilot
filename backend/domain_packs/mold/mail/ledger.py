"""SQLAlchemy ledger adapter for the injected mail monitor."""

from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import or_, select

from agent_core.events import record
from app import object_storage
from domain_packs.mold import models as m
from domain_packs.mold.ports.db import now

from .monitor import MailCursor, MailMonitorConfig
from .parser import ParsedMail

SENT_FOLDER_ALIASES = {"SENT", "&XfJT0ZAB-"}


class SqlAlchemyMailLedger:
    def __init__(self, db):
        self.db = db

    def load_cursor(self, account_id: str, folder: str = "INBOX") -> MailCursor:
        row = self.db.scalar(select(m.MailMonitorCursor).where(
            m.MailMonitorCursor.account_id == account_id,
            m.MailMonitorCursor.folder == folder,
        ))
        if row is None and folder == "INBOX":
            # Compatibility with databases created before folder-scoped cursors.
            row = self.db.scalar(select(m.MailMonitorCursor).where(m.MailMonitorCursor.account_id == account_id))
        return MailCursor(row.uid_validity, row.last_uid) if row else MailCursor()

    def save_cursor(self, account_id: str, cursor: MailCursor, folder: str = "INBOX") -> None:
        row = self.db.scalar(select(m.MailMonitorCursor).where(
            m.MailMonitorCursor.account_id == account_id,
            m.MailMonitorCursor.folder == folder,
        ))
        if row is None:
            row = m.MailMonitorCursor(
                account_id=account_id,
                folder=folder,
                uid_validity=cursor.uid_validity,
                last_uid=cursor.last_uid,
                last_polled_at=now(),
            )
            self.db.add(row)
        else:
            row.uid_validity = cursor.uid_validity
            row.last_uid = cursor.last_uid
            row.last_polled_at = now()
        self.db.flush()

    def try_acquire_lease(self, account_id: str, owner: str, ttl_seconds: int = 90, folder: str = "INBOX") -> bool:
        row = self.db.scalar(select(m.MailMonitorCursor).where(
            m.MailMonitorCursor.account_id == account_id,
            m.MailMonitorCursor.folder == folder,
        ).with_for_update())
        if row is None:
            row = m.MailMonitorCursor(
                account_id=account_id,
                folder=folder,
                lease_owner=owner,
                leased_until=now() + timedelta(seconds=ttl_seconds),
            )
            self.db.add(row)
            self.db.flush()
            return True
        if row.leased_until and row.leased_until > now() and row.lease_owner != owner:
            return False
        row.lease_owner = owner
        row.leased_until = now() + timedelta(seconds=ttl_seconds)
        self.db.flush()
        return True

    def release_lease(self, account_id: str, owner: str, folder: str = "INBOX") -> None:
        row = self.db.scalar(select(m.MailMonitorCursor).where(
            m.MailMonitorCursor.account_id == account_id,
            m.MailMonitorCursor.folder == folder,
        ).with_for_update())
        if row and row.lease_owner == owner:
            row.lease_owner = ""
            row.leased_until = None
            self.db.flush()

    def _archive_blob(self, data: bytes, filename: str, media_type: str) -> str:
        """Persist a mail object in the host object store when an admin exists."""
        admin = self.db.scalar(select(m.User).where(
            m.User.super_admin.is_(True), m.User.active.is_(True)
        ).order_by(m.User.created_at, m.User.id))
        if not admin:
            return ""
        conversation = self.db.scalar(select(m.Conversation).where(
            m.Conversation.user_id == admin.id,
            m.Conversation.title == "邮件归档",
        ))
        if conversation is None:
            conversation = m.Conversation(user_id=admin.id, title="邮件归档", pinned=False, archived=False)
            self.db.add(conversation)
            self.db.flush()
        digest = sha256(data).hexdigest()
        key = f"{uuid4().hex}/{digest}"
        try:
            storage = object_storage.put(key, data, media_type or "application/octet-stream")
        except Exception:
            return ""
        blob = m.FileObject(
            owner_id=admin.id,
            conversation_id=conversation.id,
            request_key=str(uuid4()),
            filename=filename[:200],
            media_type=media_type or "application/octet-stream",
            size=len(data),
            sha256=digest,
            object_key=key,
            **storage,
        )
        self.db.add(blob)
        self.db.flush()
        return blob.id

    def _recipients(self) -> list[str]:
        return list(self.db.scalars(select(m.User.id).where(
            m.User.super_admin.is_(True), m.User.active.is_(True)
        )))

    def _notify(self, kind: str, resource_id: str, detail: dict) -> None:
        recipients = self._recipients()
        if recipients:
            record(self.db, None, kind, resource_id, detail, recipients=recipients)

    def record_message(self, account: MailMonitorConfig, uid: int, uid_validity: str, raw: bytes, parsed: ParsedMail, raw_sha256: str, archive_path: str) -> None:
        existing = self.db.scalar(select(m.MailMessage).where(
            m.MailMessage.account_id == account.account_id,
            m.MailMessage.uid_validity == uid_validity,
            m.MailMessage.uid == uid,
        ))
        if existing:
            return
        duplicate = self.db.scalar(select(m.MailMessage).where(
            m.MailMessage.account_id == account.account_id,
            m.MailMessage.folder == account.folder,
            or_(m.MailMessage.raw_sha256 == raw_sha256,
                m.MailMessage.message_id == parsed.message_id),
        ))
        if duplicate:
            return

        route = next((
            item for item in account.routes
            if item.folder == account.folder and item.category == parsed.mail_category
        ), None)
        direction = route.direction if route else ("SENT" if account.folder.upper() in SENT_FOLDER_ALIASES else "INBOX")
        raw_file_id = self._archive_blob(raw, f"{parsed.message_id or raw_sha256}.eml", "message/rfc822")
        documents = list(parsed.attachments or parsed.documents)
        outcome = "RECEIVED"
        if not parsed.sender_allowed:
            outcome = "IGNORED_SENDER"
        elif parsed.category_status == "REVIEW":
            outcome = "QUARANTINED"
        elif parsed.category_status != "MATCHED":
            outcome = "IGNORED_NO_KEYWORD"
        row = m.MailMessage(
            account_id=account.account_id,
            route_id=(route.route_id or None) if route else None,
            folder=account.folder,
            direction=direction,
            category=parsed.mail_category,
            category_status=parsed.category_status,
            uid_validity=uid_validity,
            uid=uid,
            message_id=parsed.message_id,
            subject=parsed.subject,
            sender=parsed.sender,
            source_sent_at=parsed.source_sent_at,
            received_at=now(),
            raw_sha256=raw_sha256,
            outcome=outcome,
            plain_body=parsed.plain_body[:12 * 1024 * 1024],
            html_body=parsed.html_body[:12 * 1024 * 1024],
            raw_file_object_id=raw_file_id,
            detail_json={
                "archive_path": archive_path,
                "folder": account.folder,
                "direction": direction,
                "category": parsed.mail_category,
                "category_status": parsed.category_status,
                "category_reason": parsed.category_reason,
                "classification_reasons": list(parsed.classification_reasons),
                "recipients": list(parsed.recipients),
                "cc": list(parsed.cc),
                "attachment_names": [item.name for item in documents],
            },
        )
        self.db.add(row)
        self.db.flush()
        for index, document in enumerate(documents):
            file_id = self._archive_blob(document.data, document.name, document.media_type or "application/octet-stream")
            self.db.add(m.MailDocument(
                message_id=row.id,
                filename=document.name,
                source=document.source,
                media_type=document.media_type or "",
                sha256=sha256(document.data).hexdigest(),
                byte_size=len(document.data),
                business_type=parsed.business_types[index] if index < len(parsed.business_types) else "",
                classification_reason=parsed.classification_reasons[index] if index < len(parsed.classification_reasons) else parsed.category_reason,
                file_object_id=file_id,
                import_status="PENDING",
                preview_status="AVAILABLE" if document.media_type in {
                    "application/pdf", "image/png", "image/jpeg", "text/csv",
                    "text/tab-separated-values", "application/vnd.ms-excel",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                } else "UNSUPPORTED",
                extracted_text=(document.data.decode("utf-8", errors="replace")[:120000]
                                if (document.media_type or "").startswith("text/")
                                or document.name.lower().endswith((".csv", ".tsv")) else ""),
            ))
        self.db.flush()

        if parsed.category_status == "REVIEW":
            self._notify("mail.review.required", row.id, {
                "message_id": row.id, "category_reason": parsed.category_reason,
            })
        elif parsed.category_status == "MATCHED" and direction == "INBOX" and (route is None or route.notify_inbox):
            self._notify(f"mail.received.{parsed.mail_category.lower()}", row.id, {
                "message_id": row.id, "category": parsed.mail_category,
                "subject": parsed.subject, "sender": parsed.sender,
            })

    def record_failure(self, account: MailMonitorConfig, uid: int, uid_validity: str, raw_sha256: str, error: str) -> None:
        row = self.db.scalar(select(m.MailMessage).where(
            m.MailMessage.account_id == account.account_id,
            m.MailMessage.uid_validity == uid_validity,
            m.MailMessage.uid == uid,
        ))
        if row is None:
            row = m.MailMessage(
                account_id=account.account_id,
                folder=account.folder,
                direction="SENT" if account.folder.upper() in SENT_FOLDER_ALIASES else "INBOX",
                uid_validity=uid_validity,
                uid=uid,
                raw_sha256=raw_sha256 or "",
                outcome="FAILED",
                category_status="FAILED",
                error_message=error[:1000],
                received_at=now(),
                detail_json={"folder": account.folder},
            )
            self.db.add(row)
        else:
            row.outcome = "FAILED"
            row.category_status = "FAILED"
            row.error_message = error[:1000]
            row.retry_count += 1
        self.db.flush()
        self._notify("mail.processing.failed", row.id, {
            "message_id": row.id, "account_id": account.account_id,
            "folder": account.folder, "error": error[:500],
        })
