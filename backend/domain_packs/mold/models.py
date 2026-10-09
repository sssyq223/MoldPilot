"""ORM models owned by the mold business pack."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_core.model_base import Base, IdentityMixin, J
from agent_core import models as _core_models

# Business services may reference host-owned identities, workflow records and
# audit records through the pack model namespace.  The dependency points from
# the pack to the stable host model port; the host model facade never leaks
# mold types back into the framework implementation.
for _name, _value in vars(_core_models).items():
    if isinstance(_value, type) and hasattr(_value, "__table__"):
        globals()[_name] = _value


class Project(IdentityMixin, Base):
    __tablename__ = "project"
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class Material(IdentityMixin, Base):
    __tablename__ = "material"
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    category: Mapped[str] = mapped_column(String(60))
    unit: Mapped[str] = mapped_column(String(20))


class PurchaseRequest(IdentityMixin, Base):
    __tablename__ = "purchase_request"
    number: Mapped[str] = mapped_column(String(80), unique=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    remark: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    round_no: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','RETURNED')"),
    )


class PurchaseLine(IdentityMixin, Base):
    __tablename__ = "purchase_request_line"
    request_id: Mapped[str] = mapped_column(ForeignKey("purchase_request.id"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("material.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    due_date: Mapped[date] = mapped_column(Date)
    __table_args__ = (CheckConstraint("quantity > 0"),)


class MailMonitorAccount(IdentityMixin, Base):
    """Encrypted/secret-backed IMAP account metadata; password is never stored here."""

    __tablename__ = "mail_monitor_account"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=993)
    username: Mapped[str] = mapped_column(String(255))
    folder: Mapped[str] = mapped_column(String(255), default="INBOX")
    transport: Mapped[str] = mapped_column(String(20), default="ssl")
    secret_ref: Mapped[str] = mapped_column(String(255))
    allowed_senders: Mapped[list] = mapped_column(J, default=list)
    keywords: Mapped[dict] = mapped_column(J, default=dict)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    lookback_days: Mapped[int] = mapped_column(Integer, default=7)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="DISABLED")
    last_error: Mapped[str] = mapped_column(Text, default="")


class MailMonitorRoute(IdentityMixin, Base):
    """A mailbox/folder/category rule independent from account credentials."""

    __tablename__ = "mail_monitor_route"
    account_id: Mapped[str] = mapped_column(ForeignKey("mail_monitor_account.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    folder: Mapped[str] = mapped_column(String(255), default="INBOX")
    direction: Mapped[str] = mapped_column(String(20), default="INBOX")
    category: Mapped[str] = mapped_column(String(40))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    matcher: Mapped[dict] = mapped_column(J, default=dict)
    rule_version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_inbox: Mapped[bool] = mapped_column(Boolean, default=True)
    archive: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("account_id", "name", name="uq_mail_monitor_route_name"),
        CheckConstraint("category IN ('QUOTATION','BID_AWARDED','CONSTRUCTION_START','PROJECT_KICKOFF')"),
        CheckConstraint("direction IN ('INBOX','SENT','CUSTOM')"),
    )


class MailMonitorCursor(IdentityMixin, Base):
    __tablename__ = "mail_monitor_cursor"
    account_id: Mapped[str] = mapped_column(ForeignKey("mail_monitor_account.id"), index=True)
    folder: Mapped[str] = mapped_column(String(255), default="INBOX")
    uid_validity: Mapped[str] = mapped_column(String(80), default="")
    last_uid: Mapped[int] = mapped_column(Integer, default=0)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str] = mapped_column(String(120), default="")


class MailMessage(IdentityMixin, Base):
    __tablename__ = "mail_message"
    account_id: Mapped[str] = mapped_column(ForeignKey("mail_monitor_account.id"), index=True)
    route_id: Mapped[str | None] = mapped_column(ForeignKey("mail_monitor_route.id"), nullable=True, index=True)
    folder: Mapped[str] = mapped_column(String(255), default="INBOX")
    direction: Mapped[str] = mapped_column(String(20), default="INBOX")
    category: Mapped[str] = mapped_column(String(40), default="")
    category_status: Mapped[str] = mapped_column(String(30), default="UNMATCHED")
    uid_validity: Mapped[str] = mapped_column(String(80), default="")
    uid: Mapped[int] = mapped_column(Integer)
    message_id: Mapped[str] = mapped_column(String(512), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    sender: Mapped[str] = mapped_column(String(500), default="")
    source_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_sha256: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(40), default="RECEIVED")
    error_code: Mapped[str] = mapped_column(String(80), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    plain_body: Mapped[str] = mapped_column(Text, default="")
    html_body: Mapped[str] = mapped_column(Text, default="")
    raw_file_object_id: Mapped[str] = mapped_column(String(36), default="")
    detail_json: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (
        CheckConstraint("outcome IN ('RECEIVED','IMPORTED','DUPLICATE','IGNORED_SENDER','IGNORED_NO_DOCUMENT','IGNORED_NO_KEYWORD','FAILED','QUARANTINED','PARTIAL_FAILURE')"),
        UniqueConstraint("account_id", "uid_validity", "uid", name="uq_mail_message_uid"),
    )


class MailDocument(IdentityMixin, Base):
    __tablename__ = "mail_document"
    message_id: Mapped[str] = mapped_column(ForeignKey("mail_message.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(30))
    media_type: Mapped[str] = mapped_column(String(120), default="")
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    business_type: Mapped[str] = mapped_column(String(40), default="")
    classification_reason: Mapped[str] = mapped_column(Text, default="")
    file_object_id: Mapped[str] = mapped_column(String(36), default="")
    import_status: Mapped[str] = mapped_column(String(30), default="PENDING")
    preview_status: Mapped[str] = mapped_column(String(30), default="AVAILABLE")
    extracted_text: Mapped[str] = mapped_column(Text, default="")


def _mapped_exports(module):
    return {
        name: value
        for name, value in vars(module).items()
        if isinstance(value, type)
        and getattr(value, "__module__", None) == module.__name__
        and hasattr(value, "__table__")
    }


from domain_packs.mold import attachment_models as _attachments  # noqa: E402
from domain_packs.mold import contact_models as _contacts  # noqa: E402
from domain_packs.mold import domain_models as _domain  # noqa: E402
from domain_packs.mold.erp.commercial import contract_models as _contract_models  # noqa: E402
from domain_packs.mold.erp.commercial import bid_intake_models as _bid_intake_models  # noqa: E402
from domain_packs.mold.erp.commercial import bid_start_workflow_models as _bid_start_workflow_models  # noqa: E402
from domain_packs.mold.erp.commercial import quotation_models as _quotation_models  # noqa: E402
from domain_packs.mold.erp.commercial import contract_intake_models as _contract_intake  # noqa: E402
from domain_packs.mold.erp.design import design_models as _design_models  # noqa: E402
from domain_packs.mold.erp.project import start_models as _start_models  # noqa: E402
from domain_packs.mold.erp.project import admin_start_workflow_models as _admin_start_models  # noqa: E402
from domain_packs.mold.erp.change import local_change_models as _local_change_models  # noqa: E402

_exports = {
    "Project": Project,
    "Material": Material,
    "PurchaseRequest": PurchaseRequest,
    "PurchaseLine": PurchaseLine,
    "MailMonitorAccount": MailMonitorAccount,
    "MailMonitorRoute": MailMonitorRoute,
    "MailMonitorCursor": MailMonitorCursor,
    "MailMessage": MailMessage,
    "MailDocument": MailDocument,
    **_mapped_exports(_domain),
    **_mapped_exports(_contacts),
    **_mapped_exports(_attachments),
    **_mapped_exports(_contract_models),
    **_mapped_exports(_bid_intake_models),
    **_mapped_exports(_bid_start_workflow_models),
    **_mapped_exports(_quotation_models),
    **_mapped_exports(_contract_intake),
    **_mapped_exports(_design_models),
    **_mapped_exports(_start_models),
    **_mapped_exports(_admin_start_models),
    **_mapped_exports(_local_change_models),
}
globals().update(_exports)
EXPORTED_MODELS = tuple(_exports)
__all__ = EXPORTED_MODELS
