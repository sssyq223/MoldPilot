"""Durable formal-start materials and department handoff evidence."""
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_core.model_base import Base, IdentityMixin, J


class InternalStartSnapshot(Base):
    """Immutable business material frozen when a start proposal is confirmed.

    The generic harness keeps no mold-specific fields.  This pack-owned record
    binds the formal notice to the exact intake revision, ERP mold identities,
    order/customer facts, contract state and dates reviewed by the user.
    """

    __tablename__ = "internal_start_snapshot"

    start_subject_id: Mapped[str] = mapped_column(
        ForeignKey("business_subject.id"), primary_key=True
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), index=True)
    bid_intake_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("bid_intake_revision.id"), index=True
    )
    linked_business: Mapped[dict] = mapped_column(J)
    expected_contract_date: Mapped[date | None] = mapped_column(Date)
    frozen_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InternalStartDispatch(IdentityMixin, Base):
    """Append-only evidence for one required project-role handoff.

    Delivery itself remains an Agent Core outbox/notification concern.  The
    mold pack freezes who was selected, why they were selected, and which
    outbox event represents that handoff without creating an ERP execution
    task.
    """

    __tablename__ = "internal_start_dispatch"

    start_subject_id: Mapped[str] = mapped_column(
        ForeignKey("business_subject.id"), index=True
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), index=True)
    role_key: Mapped[str] = mapped_column(String(60))
    role_name: Mapped[str] = mapped_column(String(100))
    department_label: Mapped[str] = mapped_column(String(100))
    assignment_source: Mapped[str] = mapped_column(String(30), default="PROJECT_ROLE")
    recipient_snapshot: Mapped[list] = mapped_column(J, default=list)
    dispatch_status: Mapped[str] = mapped_column(String(30))
    event_id: Mapped[str | None] = mapped_column(ForeignKey("outbox_event.id"))
    dispatched_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    dispatched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "start_subject_id", "role_key", name="internal_start_dispatch_role"
        ),
        CheckConstraint(
            "assignment_source = 'PROJECT_ROLE'",
            name="internal_start_dispatch_assignment_source",
        ),
        CheckConstraint(
            "dispatch_status IN ('QUEUED','UNASSIGNED')",
            name="internal_start_dispatch_status",
        ),
        CheckConstraint(
            "(dispatch_status = 'QUEUED' AND event_id IS NOT NULL) OR "
            "(dispatch_status = 'UNASSIGNED' AND event_id IS NULL)",
            name="internal_start_dispatch_event_state",
        ),
    )
