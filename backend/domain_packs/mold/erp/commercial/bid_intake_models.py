"""Append-only bid intake drafts and lifecycle links for the mold domain."""
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from agent_core.model_base import Base, IdentityMixin


class BidIntakeCase(IdentityMixin, Base):
    """Stable identity that survives intake revisions and later approvals."""

    __tablename__ = "bid_intake_case"
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), unique=True, index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))


class BidIntakeRevision(IdentityMixin, Base):
    """Immutable snapshot of one manually confirmed customer intake."""

    __tablename__ = "bid_intake_revision"
    case_id: Mapped[str] = mapped_column(ForeignKey("bid_intake_case.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    previous_revision_id: Mapped[str | None] = mapped_column(ForeignKey("bid_intake_revision.id"))
    source_kind: Mapped[str] = mapped_column(String(30))
    source_ref: Mapped[str] = mapped_column(String(200))
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    received_date: Mapped[date] = mapped_column(Date)
    customer_classification: Mapped[str] = mapped_column(String(30))
    classification_evidence: Mapped[str] = mapped_column(Text)
    classification_confirmed_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    customer_company: Mapped[str] = mapped_column(String(200))
    customer_contact: Mapped[str] = mapped_column(String(200))
    customer_mold_number: Mapped[str | None] = mapped_column(String(120))
    customer_model_or_material: Mapped[str | None] = mapped_column(String(200))
    project_name_snapshot: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    our_recipient: Mapped[str] = mapped_column(String(200))
    external_order_number: Mapped[str | None] = mapped_column(String(120))
    external_start_date: Mapped[date | None] = mapped_column(Date)
    customer_due_date: Mapped[date | None] = mapped_column(Date)
    customer_process_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    customer_process_confirmation_evidence: Mapped[str | None] = mapped_column(Text)
    matched_quotation_subject_id: Mapped[str | None] = mapped_column(
        ForeignKey("business_subject.id")
    )
    historical_mold_number: Mapped[str | None] = mapped_column(String(120))
    historical_relation_kind: Mapped[str | None] = mapped_column(String(30))
    match_result: Mapped[str] = mapped_column(String(30))
    match_evidence: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    recorded_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    __table_args__ = (
        UniqueConstraint("case_id", "version", name="bid_intake_revision_case_version"),
        UniqueConstraint(
            "case_id", "source_fingerprint", name="bid_intake_revision_unique_fingerprint"
        ),
        CheckConstraint("version > 0", name="bid_intake_revision_version_positive"),
        CheckConstraint("amount IS NULL OR amount > 0", name="bid_intake_revision_amount_positive"),
        CheckConstraint(
            "(amount IS NULL AND currency IS NULL) OR (amount IS NOT NULL AND currency IS NOT NULL)",
            name="bid_intake_revision_amount_currency_pair",
        ),
        CheckConstraint(
            "source_kind IN ('UPLOAD','EMAIL','CUSTOMER_PLATFORM','OTHER')",
            name="bid_intake_revision_source_kind",
        ),
        CheckConstraint(
            "customer_classification IN ('HISENSE','HAIER','OTHER')",
            name="bid_intake_revision_customer_classification",
        ),
        CheckConstraint(
            "match_result IN ('MATCHED','PARTIAL','UNMATCHED','MANUAL')",
            name="bid_intake_revision_match_result",
        ),
        CheckConstraint(
            "historical_relation_kind IS NULL OR historical_relation_kind IN ('BACKUP','REFERENCE')",
            name="bid_intake_revision_historical_relation_kind",
        ),
        CheckConstraint(
            "(customer_process_confirmed AND "
            "customer_process_confirmation_evidence IS NOT NULL AND "
            "length(trim(customer_process_confirmation_evidence)) > 0) OR "
            "(NOT customer_process_confirmed AND "
            "customer_process_confirmation_evidence IS NULL)",
            name="bid_intake_revision_process_confirmation",
        ),
    )


class BidIntakeAttachment(IdentityMixin, Base):
    """Immutable file evidence attached to one intake revision."""

    __tablename__ = "bid_intake_attachment"
    revision_id: Mapped[str] = mapped_column(ForeignKey("bid_intake_revision.id"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("file_object.id"), index=True)
    role: Mapped[str] = mapped_column(String(40))
    content_sha256: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    __table_args__ = (
        UniqueConstraint("revision_id", "file_id", "role", name="bid_intake_attachment_unique"),
        CheckConstraint(
            "role IN ('BID_NOTICE','EXTERNAL_START_NOTICE','CONTRACT_REFERENCE','MOLD_IMAGE','OTHER')",
            name="bid_intake_attachment_role",
        ),
    )


class BidIntakeLifecycleLink(IdentityMixin, Base):
    """Append-only link from the stable intake case to later approved facts."""

    __tablename__ = "bid_intake_lifecycle_link"
    case_id: Mapped[str] = mapped_column(ForeignKey("bid_intake_case.id"), index=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("business_subject.id"), unique=True)
    source_revision_id: Mapped[str | None] = mapped_column(ForeignKey("bid_intake_revision.id"))
    link_kind: Mapped[str] = mapped_column(String(30))
    linked_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    __table_args__ = (
        UniqueConstraint("case_id", "link_kind", "subject_id", name="bid_intake_lifecycle_link_unique"),
        CheckConstraint(
            "link_kind IN ('ACCEPTANCE','REJECTION','INTERNAL_START')",
            name="bid_intake_lifecycle_link_kind",
        ),
    )
