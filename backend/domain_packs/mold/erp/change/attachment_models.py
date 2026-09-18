"""Mold collaboration attachment relationships."""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_core.model_base import Base, IdentityMixin


class ContactAttachment(IdentityMixin, Base):
    __tablename__ = "contact_attachment"
    case_id: Mapped[str] = mapped_column(ForeignKey("contact_case.id"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("file_object.id"), index=True)
    document_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(150))
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("contact_attachment.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    __table_args__ = (
        UniqueConstraint("case_id", "document_id", "version"),
        UniqueConstraint("case_id", "file_id"),
        CheckConstraint("version > 0"),
    )
