"""MoldPilot local Tools for read-only mail monitoring and confirmation cards."""

from uuid import uuid4

from pydantic import Field, ValidationError
from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint, require
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel


class MailStatusInput(StrictModel):
    account_name: str | None = Field(default=None, max_length=120)


class MailHistoryInput(StrictModel):
    account_name: str | None = Field(default=None, max_length=120)
    outcome: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=50, ge=1, le=100)


class MailMessageInput(StrictModel):
    message_id: str = Field(min_length=1, max_length=36)


class MailDocumentInput(StrictModel):
    document_id: str = Field(min_length=1, max_length=36)


class MailMonitorConfigInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=993, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=255)
    folder: str = Field(default="INBOX", min_length=1, max_length=255)
    transport: str = Field(default="ssl", pattern=r"^(ssl|starttls)$")
    secret_ref: str = Field(min_length=1, max_length=255)
    allowed_senders: list[str] = Field(default_factory=list, max_length=128)
    keywords: dict[str, list[str]] = Field(default_factory=dict)
    poll_interval_seconds: int = Field(default=60, ge=15, le=3600)
    lookback_days: int = Field(default=7, ge=0, le=90)
    routes: list[dict] = Field(default_factory=list, max_length=128)


class MailRescanInput(StrictModel):
    account_id: str = Field(min_length=1, max_length=36)
    message_id: str | None = Field(default=None, max_length=36)
    reason: str = Field(min_length=1, max_length=1000)


class MailReviewInput(StrictModel):
    message_id: str = Field(min_length=1, max_length=36)
    decision: str = Field(pattern=r"^(ACCEPT|REJECT|RETRY)$")
    basis: str = Field(min_length=1, max_length=4000)


INPUTS = {
    "query_mail_monitor_status": MailStatusInput,
    "query_mail_processing_history": MailHistoryInput,
    "query_mail_message_detail": MailMessageInput,
    "query_mail_document": MailDocumentInput,
    "prepare_mail_monitor_config": MailMonitorConfigInput,
    "prepare_mail_monitor_rescan": MailRescanInput,
    "prepare_mail_review": MailReviewInput,
}


def schema(key):
    return INPUTS[key].model_json_schema()


def _parse(key, arguments):
    try:
        return INPUTS[key].model_validate(arguments or {})
    except (ValidationError, KeyError) as error:
        message = error.errors()[0]["msg"] if hasattr(error, "errors") else "参数无效"
        raise DomainError("INVALID_TOOL_INPUT", f"邮件工具参数无效：{message}") from None


def _require_read(db, user):
    if not db:
        raise DomainError("MAIL_CONTEXT_REQUIRED", "邮件监听查询需要数据库会话", 500)
    if not user.active:
        raise DomainError("FORBIDDEN", "账号不可用", 403)
    require(db, user, "mail.read", {})


def _account(row):
    return {
        "id": row.id, "name": row.name, "host": row.host, "port": row.port,
        "username": row.username, "folder": row.folder, "transport": row.transport,
        "enabled": row.enabled, "status": row.status, "poll_interval_seconds": row.poll_interval_seconds,
        "last_error": row.last_error,
        "routes": getattr(row, "_mail_routes", []),
    }


def _message(row):
    return {
        "id": row.id, "account_id": row.account_id, "uid_validity": row.uid_validity,
        "uid": row.uid, "message_id": row.message_id, "subject": row.subject,
        "sender": row.sender, "source_sent_at": row.source_sent_at.isoformat() if row.source_sent_at else None,
        "received_at": row.received_at.isoformat() if row.received_at else None,
        "raw_sha256": row.raw_sha256, "outcome": row.outcome, "error_code": row.error_code,
        "error_message": row.error_message, "retry_count": row.retry_count, "detail": row.detail_json or {},
        "folder": row.folder, "direction": row.direction, "category": row.category,
        "category_status": row.category_status,
        "plain_body": (row.plain_body or "")[:120000],
        "html_body": (row.html_body or "")[:120000],
    }


def _document(row):
    return {
        "id": row.id, "message_id": row.message_id, "filename": row.filename,
        "source": row.source, "media_type": row.media_type, "sha256": row.sha256,
        "byte_size": row.byte_size, "business_type": row.business_type,
        "classification_reason": row.classification_reason, "file_object_id": row.file_object_id,
        "import_status": row.import_status, "preview_status": row.preview_status,
        "extracted_text": (row.extracted_text or "")[:120000],
    }


def _proposal(db, user, key, data, run=None, limitations=None):
    return {
        "data": [], "source": "agent_proposal", "as_of": now().isoformat(),
        "proposal": {
            "tool": key, "action": key.removeprefix("prepare_"),
            "input": data.model_dump(mode="json"), "operation_id": str(uuid4()),
            "security_version": user.security_version,
            "authorization_hash": fingerprint(db, user) if db else None,
            "requires_approval": True,
            "confirmation_policy": proposal_confirmation_policy(run, requires_approval=True),
        },
        "limitations": limitations or ["仅生成确认卡，尚未连接邮箱或写入监听配置。"],
    }


def execute_tool(db, user, key, arguments, run=None):
    data = _parse(key, arguments)
    if key.startswith("query_"):
        _require_read(db, user)
    if key == "query_mail_monitor_status":
        query = select(m.MailMonitorAccount).order_by(m.MailMonitorAccount.name)
        if data.account_name:
            query = query.where(m.MailMonitorAccount.name == data.account_name)
        accounts = list(db.scalars(query.limit(100)))
        route_rows = list(db.scalars(select(m.MailMonitorRoute).where(
            m.MailMonitorRoute.account_id.in_([row.id for row in accounts])
        ))) if accounts else []
        routes_by_account = {}
        for route in route_rows:
            routes_by_account.setdefault(route.account_id, []).append({
                "id": route.id, "name": route.name, "folder": route.folder,
                "direction": route.direction, "category": route.category,
                "priority": route.priority, "matcher": route.matcher or {},
                "enabled": bool(route.enabled), "notify_inbox": bool(route.notify_inbox),
                "archive": bool(route.archive),
            })
        for account in accounts:
            account._mail_routes = routes_by_account.get(account.id, [])
        cursors = {(row.account_id, row.folder): row for row in db.scalars(select(m.MailMonitorCursor))}
        return {"data": [{**_account(row), "cursors": [{"folder": cursor.folder, "uid_validity": cursor.uid_validity, "last_uid": cursor.last_uid, "last_polled_at": cursor.last_polled_at.isoformat() if cursor.last_polled_at else None} for (account_id, _folder), cursor in cursors.items() if account_id == row.id]} for row in accounts], "source": "agent_db", "as_of": now().isoformat(), "limitations": ["不代表当前 IMAP 连接已成功；需要后台 worker 的最近心跳和错误记录。"]}
    if key == "query_mail_processing_history":
        query = select(m.MailMessage).order_by(m.MailMessage.created_at.desc()).limit(data.limit)
        if data.account_name:
            account = db.scalar(select(m.MailMonitorAccount).where(m.MailMonitorAccount.name == data.account_name))
            query = query.where(m.MailMessage.account_id == account.id if account else "__missing__")
        if data.outcome:
            query = query.where(m.MailMessage.outcome == data.outcome)
        return {"data": [_message(row) for row in db.scalars(query)], "source": "agent_db", "as_of": now().isoformat(), "limit": data.limit, "limitations": ["只读处理台账；不会自动重试或确认导入。"]}
    if key == "query_mail_message_detail":
        row = db.get(m.MailMessage, data.message_id)
        if not row:
            raise DomainError("NOT_FOUND", "邮件处理记录不存在", 404)
        docs = list(db.scalars(select(m.MailDocument).where(m.MailDocument.message_id == row.id).order_by(m.MailDocument.created_at)))
        return {"data": {**_message(row), "documents": [_document(item) for item in docs]}, "source": "agent_db", "as_of": now().isoformat(), "limitations": ["原始邮件内容通过文件对象或审计引用访问，不在工具响应中回显。"]}
    if key == "query_mail_document":
        row = db.get(m.MailDocument, data.document_id)
        if not row:
            raise DomainError("NOT_FOUND", "邮件文档不存在", 404)
        return {"data": _document(row), "source": "agent_db", "as_of": now().isoformat(), "limitations": ["仅返回文档元数据；文件下载遵循 FileObject/RunFile 权限。"]}
    if db:
        require(db, user, "mail.manage" if key != "prepare_mail_review" else "mail.review", {})
    return _proposal(db, user, key, data, run=run)
