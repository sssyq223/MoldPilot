"""Collaboration facts are separate from binding approval decisions."""
from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, IdentityMixin, J


class ContactCase(IdentityMixin, Base):
    __tablename__ = 'contact_case'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    category: Mapped[str | None] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    request_key: Mapped[str] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    __table_args__ = (UniqueConstraint('created_by','request_key'), CheckConstraint("mode IN ('HISTORY','ONLINE')"))


class ContactTask(IdentityMixin, Base):
    __tablename__ = 'contact_task'
    case_id: Mapped[str] = mapped_column(ForeignKey('contact_case.id'), index=True)
    department_id: Mapped[str] = mapped_column(ForeignKey('assignment_group.id'))
    title: Mapped[str] = mapped_column(String(150))
    created_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    status: Mapped[str] = mapped_column(String(30), default='UNASSIGNED')
    response: Mapped[str | None] = mapped_column(Text)
    verified_plan_id: Mapped[str | None] = mapped_column(ForeignKey('business_subject.id'))
    __table_args__ = (CheckConstraint("status IN ('UNASSIGNED','ASSIGNED','RESPONDED','VERIFIED','CANCELLED')",name='contact_task_state'),)


class ContactRecord(IdentityMixin, Base):
    __tablename__ = 'contact_record'
    case_id: Mapped[str] = mapped_column(ForeignKey('contact_case.id'), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    request_key: Mapped[str] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(30))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict] = mapped_column(J)
    __table_args__ = (UniqueConstraint('case_id','author_id','request_key'),)


class ContactResolution(Base):
    __tablename__ = 'contact_resolution'
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'),primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey('contact_case.id'),index=True)
    case_revision: Mapped[int] = mapped_column(Integer)
    solution: Mapped[str] = mapped_column(Text)
    customer_evidence: Mapped[str | None] = mapped_column(Text)
    customer_due_affected: Mapped[bool] = mapped_column(Boolean)
    material_snapshot: Mapped[dict] = mapped_column(J)
