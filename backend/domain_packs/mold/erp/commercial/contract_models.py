"""Versioned contract documents, receipt evidence and settlement lineage."""
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_core.model_base import Base, IdentityMixin


class ContractAttachment(IdentityMixin, Base):
    __tablename__ = "contract_attachment"
    contract_subject_id: Mapped[str] = mapped_column(ForeignKey("business_subject.id"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("file_object.id"), index=True)
    document_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    source_kind: Mapped[str] = mapped_column(String(30), default="ELECTRONIC")
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("contract_attachment.id"))
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    __table_args__ = (
        UniqueConstraint("contract_subject_id", "document_id", "version"),
        UniqueConstraint("contract_subject_id", "file_id"),
        CheckConstraint("version > 0"),
        CheckConstraint("source_kind IN ('ELECTRONIC','PAPER_SCAN','OTHER')"),
    )


class ContractReceiptEvidence(Base):
    """Actual arrival date recorded together with the frozen contract files."""

    __tablename__ = "contract_receipt_evidence"
    contract_subject_id: Mapped[str] = mapped_column(
        ForeignKey("business_subject.id"), primary_key=True
    )
    received_date: Mapped[date] = mapped_column(Date)
    recorded_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))


class ContractSettlementAllocation(IdentityMixin, Base):
    """An immutable link from one confirmed cash fact into a replacement contract.

    Cash facts remain on their original contract.  This link only states which
    replacement payment stage consumes that history, preventing the same fact
    from disappearing or being counted as new cash.
    """
    __tablename__ = "contract_settlement_allocation"
    target_contract_id: Mapped[str] = mapped_column(ForeignKey("business_subject.id"), index=True)
    source_contract_id: Mapped[str] = mapped_column(ForeignKey("business_subject.id"), index=True)
    target_stage_id: Mapped[str] = mapped_column(ForeignKey("payment_stage.id"), index=True)
    record_type: Mapped[str] = mapped_column(String(30))
    source_record_id: Mapped[str] = mapped_column(String(36), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    evidence: Mapped[str] = mapped_column(Text)
    recorded_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    __table_args__ = (
        UniqueConstraint(
            "target_contract_id", "record_type", "source_record_id",
            name="contract_settlement_allocation_unique_record",
        ),
        CheckConstraint("amount <> 0", name="contract_settlement_allocation_nonzero"),
        CheckConstraint(
            "record_type IN ('CUSTOMER_RECEIPT','SUPPLIER_PAYMENT')",
            name="contract_settlement_allocation_record_type",
        ),
    )
