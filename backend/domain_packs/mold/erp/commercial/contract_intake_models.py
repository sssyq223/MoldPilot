"""Persistent intake facts for PDF classification and sales-contract OCR review."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from agent_core.model_base import Base, IdentityMixin, J


DOCUMENT_TYPES = (
    "BID_NOTICE",
    "CUSTOMER_START_NOTICE",
    "SALES_CONTRACT",
    "MOLD_DRAWING",
    "OTHER",
)
DOCUMENT_INTAKE_STATUSES = (
    "UPLOADED",
    "PRECLASSIFYING",
    "AWAITING_TYPE_CONFIRMATION",
    "CLASSIFIED_ARCHIVED",
    "FULL_OCR_QUEUED",
    "FULL_OCR_PROCESSING",
    "OCR_FAILED",
    "AWAITING_FIELD_CONFIRMATION",
    "READY_FOR_DRAFT",
    "CONTRACT_DRAFT_CREATED",
)
OCR_PHASES = ("PRECLASSIFY", "FULL_CONTRACT")
OCR_STATUSES = ("QUEUED", "PROCESSING", "RETRY_WAIT", "SUCCEEDED", "FAILED")
OCR_PAGE_SOURCE_KINDS = ("TEXT_LAYER", "PADDLE_OCR", "HYBRID")
CONTRACT_DOCUMENT_ROLES = ("MAIN", "ATTACHMENT", "STAMP_PAGE", "PAYMENT_TERMS", "OTHER")
CONTRACT_RELATION_TYPES = ("DUPLICATE", "REVISION", "SUPPLEMENT", "REPLACEMENT")
CONTRACT_INTAKE_RELATION_TYPES = ("NEW",) + CONTRACT_RELATION_TYPES


def _quoted(values):
    return ",".join(f"'{value}'" for value in values)


class DocumentIntake(IdentityMixin, Base):
    __tablename__ = "document_intake"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("ai_conversation.id"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    request_key: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(40), default="UPLOADED")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        UniqueConstraint("created_by", "request_key", name="uq_document_intake_request"),
        CheckConstraint(
            f"status IN ({_quoted(DOCUMENT_INTAKE_STATUSES)})",
            name="document_intake_status",
        ),
    )


class ContractIntakeGroup(IdentityMixin, Base):
    __tablename__ = "contract_intake_group"
    intake_id: Mapped[str] = mapped_column(ForeignKey("document_intake.id"), index=True)
    group_key: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(40), default="FULL_OCR_QUEUED")
    project_id: Mapped[str | None] = mapped_column(ForeignKey("project.id"), index=True)
    project_version: Mapped[int | None] = mapped_column(Integer)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customer.id"))
    contract_subject_id: Mapped[str | None] = mapped_column(ForeignKey("business_subject.id"), unique=True)
    duplicate_of_contract_id: Mapped[str | None] = mapped_column(ForeignKey("business_subject.id"))
    relation_type: Mapped[str | None] = mapped_column(String(30))
    relation_target_contract_id: Mapped[str | None] = mapped_column(ForeignKey("business_subject.id"))
    relation_reason: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_warnings: Mapped[list] = mapped_column(J, default=list)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        UniqueConstraint("intake_id", "group_key", name="uq_contract_intake_group_key"),
        CheckConstraint(
            f"status IN ({_quoted(DOCUMENT_INTAKE_STATUSES)})",
            name="contract_intake_group_status",
        ),
        CheckConstraint("project_version IS NULL OR project_version >= 1", name="contract_intake_project_version"),
        CheckConstraint(
            f"relation_type IS NULL OR relation_type IN ({_quoted(CONTRACT_INTAKE_RELATION_TYPES)})",
            name="contract_intake_relation_type",
        ),
        CheckConstraint(
            "(relation_type IS NULL) OR (relation_type = 'NEW' AND relation_target_contract_id IS NULL) "
            "OR (relation_type <> 'NEW' AND relation_target_contract_id IS NOT NULL)",
            name="contract_intake_relation_target",
        ),
    )


class DocumentIntakeFile(IdentityMixin, Base):
    __tablename__ = "document_intake_file"
    intake_id: Mapped[str] = mapped_column(ForeignKey("document_intake.id"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("file_object.id"), unique=True)
    contract_group_id: Mapped[str | None] = mapped_column(ForeignKey("contract_intake_group.id"), index=True)
    suggested_type: Mapped[str | None] = mapped_column(String(40))
    suggested_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    confirmed_type: Mapped[str | None] = mapped_column(String(40))
    confirmed_role: Mapped[str | None] = mapped_column(String(30))
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            f"suggested_type IS NULL OR suggested_type IN ({_quoted(DOCUMENT_TYPES)})",
            name="document_suggested_type",
        ),
        CheckConstraint(
            f"confirmed_type IS NULL OR confirmed_type IN ({_quoted(DOCUMENT_TYPES)})",
            name="document_confirmed_type",
        ),
        CheckConstraint(
            f"confirmed_role IS NULL OR confirmed_role IN ({_quoted(CONTRACT_DOCUMENT_ROLES)})",
            name="document_confirmed_role",
        ),
        CheckConstraint(
            "suggested_confidence IS NULL OR (suggested_confidence >= 0 AND suggested_confidence <= 1)",
            name="document_suggested_confidence",
        ),
    )


class DocumentRecognizedPage(IdentityMixin, Base):
    __tablename__ = "document_recognized_page"
    intake_file_id: Mapped[str] = mapped_column(ForeignKey("document_intake_file.id"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    source_kind: Mapped[str] = mapped_column(String(20))
    source_sha256: Mapped[str] = mapped_column(String(64))
    pipeline_version: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)
    blocks: Mapped[list] = mapped_column(J)
    average_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    text_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint(
            "intake_file_id",
            "page_number",
            "source_sha256",
            "pipeline_version",
            name="uq_document_recognized_page_version",
        ),
        CheckConstraint("page_number >= 1", name="document_recognized_page_number"),
        CheckConstraint(
            f"source_kind IN ({_quoted(OCR_PAGE_SOURCE_KINDS)})",
            name="document_recognized_page_source",
        ),
        CheckConstraint(
            "average_confidence IS NULL OR "
            "(average_confidence >= 0 AND average_confidence <= 1)",
            name="document_recognized_page_confidence",
        ),
    )


class DocumentOcrJob(IdentityMixin, Base):
    __tablename__ = "document_ocr_job"
    intake_file_id: Mapped[str] = mapped_column(ForeignKey("document_intake_file.id"), index=True)
    phase: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="QUEUED", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_id: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(80), default="")
    last_error: Mapped[str | None] = mapped_column(String(120))
    __table_args__ = (
        UniqueConstraint("intake_file_id", "phase", name="uq_document_ocr_job_phase"),
        CheckConstraint(f"phase IN ({_quoted(OCR_PHASES)})", name="document_ocr_phase"),
        CheckConstraint(f"status IN ({_quoted(OCR_STATUSES)})", name="document_ocr_status"),
        CheckConstraint("attempts >= 0", name="document_ocr_attempts"),
        Index("ix_document_ocr_claim", "status", "retry_at", "lease_until"),
    )


class DocumentExtractedField(IdentityMixin, Base):
    __tablename__ = "document_extracted_field"
    job_id: Mapped[str] = mapped_column(ForeignKey("document_ocr_job.id"), index=True)
    scope: Mapped[str] = mapped_column(String(20))
    row_key: Mapped[str] = mapped_column(String(80), default="header")
    field_key: Mapped[str] = mapped_column(String(80))
    raw_value: Mapped[dict] = mapped_column(J)
    normalized_value: Mapped[dict] = mapped_column(J)
    source_block_ids: Mapped[list] = mapped_column(J, default=list)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    page_number: Mapped[int] = mapped_column(Integer)
    bbox: Mapped[dict | None] = mapped_column(J)
    confirmed_value: Mapped[dict | None] = mapped_column(J)
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("job_id", "scope", "row_key", "field_key", name="uq_document_extracted_field"),
        CheckConstraint("scope IN ('HEADER','MOLD','PAYMENT')", name="document_field_scope"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="document_field_confidence"),
        CheckConstraint("page_number >= 1", name="document_field_page"),
    )


class ContractIntakeMoldMatch(IdentityMixin, Base):
    __tablename__ = "contract_intake_mold_match"
    group_id: Mapped[str] = mapped_column(ForeignKey("contract_intake_group.id"), index=True)
    row_key: Mapped[str] = mapped_column(String(80))
    customer_mold_number: Mapped[str | None] = mapped_column(String(100))
    machine_model: Mapped[str | None] = mapped_column(String(120))
    material_number: Mapped[str | None] = mapped_column(String(120))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    due_date: Mapped[date | None] = mapped_column(Date)
    mold_id: Mapped[str | None] = mapped_column(ForeignKey("mold.id"), index=True)
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("group_id", "row_key", name="uq_contract_intake_mold_row"),
        CheckConstraint("amount IS NULL OR amount > 0", name="contract_intake_mold_amount"),
    )


class ContractMoldLine(IdentityMixin, Base):
    __tablename__ = "contract_mold_line"
    contract_subject_id: Mapped[str] = mapped_column(ForeignKey("business_subject.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    mold_id: Mapped[str] = mapped_column(ForeignKey("mold.id"), index=True)
    customer_mold_number: Mapped[str | None] = mapped_column(String(100))
    machine_model: Mapped[str | None] = mapped_column(String(120))
    material_number: Mapped[str | None] = mapped_column(String(120))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    due_date: Mapped[date | None] = mapped_column(Date)
    source_row_key: Mapped[str] = mapped_column(String(80))
    __table_args__ = (
        UniqueConstraint("contract_subject_id", "line_no", name="uq_contract_mold_line_no"),
        CheckConstraint("line_no >= 1", name="contract_mold_line_number"),
        CheckConstraint("amount IS NULL OR amount > 0", name="contract_mold_line_amount"),
    )


class ContractRelation(IdentityMixin, Base):
    __tablename__ = "contract_relation"
    source_contract_id: Mapped[str] = mapped_column(ForeignKey("business_subject.id"), index=True)
    target_contract_id: Mapped[str] = mapped_column(ForeignKey("business_subject.id"), index=True)
    relation_type: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    __table_args__ = (
        UniqueConstraint(
            "source_contract_id",
            "target_contract_id",
            "relation_type",
            name="uq_contract_relation",
        ),
        CheckConstraint(
            "source_contract_id <> target_contract_id",
            name="contract_relation_not_self",
        ),
        CheckConstraint(
            f"relation_type IN ({_quoted(CONTRACT_RELATION_TYPES)})",
            name="contract_relation_type",
        ),
    )
