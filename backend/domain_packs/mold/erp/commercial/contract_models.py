"""Versioned contract documents owned by the mold commercial domain."""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
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
