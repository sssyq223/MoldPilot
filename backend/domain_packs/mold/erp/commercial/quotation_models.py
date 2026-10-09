"""Versioned customer quotation evidence owned by the mold commercial domain."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from agent_core.model_base import Base, IdentityMixin, J


# 报价拒绝类别枚举
REJECTION_CATEGORIES = [
    "PRICE_TOO_LOW",        # 价格过低
    "TIMELINE_IMPOSSIBLE",  # 交期不可行
    "TECHNICAL_DIFFICULTY", # 技术难度过高
    "CAPACITY_SHORTAGE",    # 产能不足
    "CUSTOMER_CREDIT",      # 客户信誉问题
    "MATERIAL_SHORTAGE",    # 材料缺货
    "RESOURCE_CONFLICT",    # 资源冲突
    "PROFIT_MARGIN_LOW",    # 利润率过低
    "OTHER",                # 其他原因
]


class QuoteInboundRecord(IdentityMixin, Base):
    """Immutable receipt evidence; source identity and file content are both retained."""

    __tablename__ = "quote_inbound_record"
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), index=True)
    source_kind: Mapped[str] = mapped_column(String(30))
    source_ref: Mapped[str] = mapped_column(String(200))
    file_id: Mapped[str] = mapped_column(ForeignKey("file_object.id"), index=True)
    content_sha256: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    received_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    __table_args__ = (
        UniqueConstraint(
            "project_id", "source_kind", "source_ref", "content_sha256",
            name="quote_inbound_record_unique_source_content",
        ),
        CheckConstraint(
            "source_kind IN ('UPLOAD','EMAIL','CUSTOMER_PLATFORM','OTHER')",
            name="quote_inbound_record_source_kind",
        ),
    )


class QuotationDetail(Base):
    """A BPM-controlled, immutable quotation version snapshot."""

    __tablename__ = "quotation_detail"
    subject_id: Mapped[str] = mapped_column(ForeignKey("business_subject.id"), primary_key=True)
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("business_subject.id"))
    quotation_number: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    preliminary_execution_mode: Mapped[str] = mapped_column(String(30))
    quoted_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    promised_delivery_date: Mapped[date] = mapped_column(Date)
    payment_terms: Mapped[str] = mapped_column(Text)
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    cost_evidence: Mapped[str] = mapped_column(Text)
    process_analysis: Mapped[str] = mapped_column(Text)
    duration_days: Mapped[int] = mapped_column(Integer)
    duration_evidence: Mapped[str] = mapped_column(Text)
    supplier_quote_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    supplier_delivery_date: Mapped[date | None] = mapped_column(Date)
    supplier_requirements: Mapped[str | None] = mapped_column(Text)
    supplier_quote_evidence: Mapped[str | None] = mapped_column(Text)
    customer_company_snapshot: Mapped[str] = mapped_column(String(200))
    customer_contact_snapshot: Mapped[str] = mapped_column(String(200))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    source_summary: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (
        UniqueConstraint("quotation_number", "version", name="quotation_number_version_unique"),
        CheckConstraint("version > 0", name="quotation_version_positive"),
        CheckConstraint("quoted_amount > 0", name="quotation_amount_positive"),
        CheckConstraint("cost_amount IS NULL OR cost_amount >= 0", name="quotation_cost_nonnegative"),
        CheckConstraint(
            "supplier_quote_amount IS NULL OR supplier_quote_amount >= 0",
            name="quotation_supplier_amount_nonnegative",
        ),
        CheckConstraint("duration_days > 0", name="quotation_duration_positive"),
        CheckConstraint(
            "preliminary_execution_mode IN ('INTERNAL','FULL_OUTSOURCE')",
            name="quotation_execution_mode",
        ),
    )


class QuotationSourceLink(Base):
    __tablename__ = "quotation_source_link"
    quotation_subject_id: Mapped[str] = mapped_column(
        ForeignKey("business_subject.id"), primary_key=True
    )
    inbound_record_id: Mapped[str] = mapped_column(
        ForeignKey("quote_inbound_record.id"), primary_key=True
    )


class QuotationFeedback(IdentityMixin, Base):
    """Immutable customer feedback fact; it never auto-accepts or rejects a project."""

    __tablename__ = "quotation_feedback"
    quotation_subject_id: Mapped[str] = mapped_column(ForeignKey("business_subject.id"), index=True)
    feedback_type: Mapped[str] = mapped_column(String(30))
    feedback_date: Mapped[date] = mapped_column(Date)
    evidence: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str] = mapped_column(String(200))
    recorded_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    __table_args__ = (
        UniqueConstraint(
            "quotation_subject_id", "source_ref", name="quotation_feedback_unique_source"
        ),
        CheckConstraint(
            "feedback_type IN ('ACCEPTED','REJECTED','REVISION_REQUESTED','NO_RESPONSE','OTHER')",
            name="quotation_feedback_type",
        ),
    )


class QuotationRejection(Base):
    """Internal decision to reject a quotation request before sending a quote."""

    __tablename__ = "quotation_rejection"
    subject_id: Mapped[str] = mapped_column(ForeignKey("business_subject.id"), primary_key=True)
    quotation_subject_id: Mapped[str] = mapped_column(ForeignKey("business_subject.id"), index=True)
    rejection_category: Mapped[str] = mapped_column(String(30))
    rejection_reason: Mapped[str] = mapped_column(Text)
    decision_date: Mapped[date] = mapped_column(Date)
    decided_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    evidence: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"))
    row_version: Mapped[int] = mapped_column(Integer, server_default="1")
    __table_args__ = (
        CheckConstraint(
            f"rejection_category IN ({','.join(repr(c) for c in REJECTION_CATEGORIES)})",
            name="quotation_rejection_category_check"
        ),
    )
