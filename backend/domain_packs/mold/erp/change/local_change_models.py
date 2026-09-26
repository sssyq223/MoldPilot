"""MoldPilot 本地设变承接事实；不保存 ERP 外部身份或回执。"""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_core.model_base import Base, IdentityMixin, J


class LocalChangeIntake(IdentityMixin, Base):
    __tablename__ = "local_change_intake"

    number: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("project.id"), index=True)
    original_mold_id: Mapped[str | None] = mapped_column(ForeignKey("mold.id"), index=True)
    mold_mode: Mapped[str] = mapped_column(String(20))
    customer_mold_number: Mapped[str | None] = mapped_column(String(160))
    classification: Mapped[str] = mapped_column(String(20))
    execution_mode: Mapped[str] = mapped_column(String(20))
    charge_status: Mapped[str] = mapped_column(String(20))
    contract_status: Mapped[str] = mapped_column(String(20))
    execution_scope: Mapped[str] = mapped_column(Text)
    customer_basis: Mapped[str] = mapped_column(Text)
    acceptance_status: Mapped[str] = mapped_column(String(30), default="PENDING")
    status: Mapped[str] = mapped_column(String(30), default="PENDING_ACCEPTANCE")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    request_key: Mapped[str] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    latest_snapshot: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (
        UniqueConstraint("created_by", "request_key", name="uq_local_change_intake_request"),
        CheckConstraint("mold_mode IN ('EXISTING','NEW_EXTERNAL')", name="local_change_mold_mode"),
        CheckConstraint("(mold_mode = 'EXISTING' AND original_mold_id IS NOT NULL) OR (mold_mode = 'NEW_EXTERNAL' AND original_mold_id IS NULL)", name="local_change_mold_reference"),
        CheckConstraint("classification IN ('CUSTOMER','INTERNAL','OUTSOURCE')", name="local_change_classification"),
        CheckConstraint("execution_mode IN ('INTERNAL','OUTSOURCE')", name="local_change_execution_mode"),
        CheckConstraint("charge_status IN ('CHARGED','FREE','PENDING')", name="local_change_charge_status"),
        CheckConstraint("contract_status IN ('NONE','REQUIRED','AVAILABLE','PENDING')", name="local_change_contract_status"),
        CheckConstraint("acceptance_status IN ('PENDING','ACCEPTED','REJECTED')", name="local_change_acceptance_status"),
    )


class LocalChangeCustomerMoldHistory(IdentityMixin, Base):
    __tablename__ = "local_change_customer_mold_history"

    change_id: Mapped[str] = mapped_column(ForeignKey("local_change_intake.id"), index=True)
    customer_mold_number: Mapped[str] = mapped_column(String(160))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    recorded_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))


class LocalChangeAssociation(IdentityMixin, Base):
    __tablename__ = "local_change_association"

    change_id: Mapped[str] = mapped_column(ForeignKey("local_change_intake.id"), index=True)
    association_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[str] = mapped_column(String(80))
    target_revision: Mapped[int | None] = mapped_column(Integer)
    evidence: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    __table_args__ = (
        CheckConstraint("association_type IN ('PROJECT','MOLD','CONTRACT','DOCUMENT')", name="local_change_association_type"),
    )
