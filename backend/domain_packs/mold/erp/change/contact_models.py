"""Collaboration facts are separate from binding approval decisions."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import String, Text, Integer, Date, DateTime, Numeric, ForeignKey, UniqueConstraint, CheckConstraint, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from agent_core.model_base import Base, IdentityMixin, J


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
    customer_ref: Mapped[str | None] = mapped_column(String(200))
    customer_name: Mapped[str | None] = mapped_column(String(200))
    mold_number: Mapped[str | None] = mapped_column(String(100))
    product_ref: Mapped[str | None] = mapped_column(String(200))
    application_date: Mapped[date | None] = mapped_column(Date)
    problem_source: Mapped[str | None] = mapped_column(String(40))
    current_stage: Mapped[str | None] = mapped_column(String(200))
    change_type: Mapped[str | None] = mapped_column(String(30))
    urgency: Mapped[str | None] = mapped_column(String(20))
    __table_args__ = (UniqueConstraint('created_by','request_key'), CheckConstraint("mode IN ('HISTORY','ONLINE')"),
        CheckConstraint("problem_source IS NULL OR problem_source IN ('CUSTOMER_CHANGE','DESIGN_ISSUE','ASSEMBLY_ISSUE','MACHINING_ISSUE','PROCUREMENT_ISSUE','QUALITY_ISSUE','TRIAL_ISSUE','OUTSOURCE_DEFECT','COST_REDUCTION','PROCESS_IMPROVEMENT','OTHER')",name='contact_problem_source'),
        CheckConstraint("change_type IS NULL OR change_type IN ('CHANGE','EXCEPTION','IMPROVEMENT')",name='contact_change_type'),
        CheckConstraint("urgency IS NULL OR urgency IN ('NORMAL','URGENT','CRITICAL')",name='contact_urgency'))


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
    affected_type: Mapped[str] = mapped_column(String(40))
    affected_ref: Mapped[str] = mapped_column(String(300))
    impact_description: Mapped[str] = mapped_column(Text)
    planned_action: Mapped[str] = mapped_column(String(30))
    delivery_impact_days: Mapped[int] = mapped_column(Integer, default=0)
    estimated_amount: Mapped[Decimal | None] = mapped_column(Numeric(18,2))
    currency: Mapped[str | None] = mapped_column(String(3))
    source_system: Mapped[str] = mapped_column(String(20), default='AGENT')
    source_ref: Mapped[str | None] = mapped_column(String(300))
    source_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_hours: Mapped[Decimal | None] = mapped_column(Numeric(12,2))
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(18,2))
    actual_currency: Mapped[str | None] = mapped_column(String(3))
    execution_evidence: Mapped[str | None] = mapped_column(Text)
    execution_source_system: Mapped[str | None] = mapped_column(String(20))
    execution_source_ref: Mapped[str | None] = mapped_column(String(300))
    execution_source_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("status IN ('UNASSIGNED','ASSIGNED','RESPONDED','VERIFIED','CANCELLED')",name='contact_task_state'),
        CheckConstraint("affected_type IN ('DRAWING','MATERIAL','PURCHASE_ORDER','WIP_TASK','SUPPLIER_TASK','PLAN_NODE','CONTRACT','FINANCE','LOGISTICS','OTHER')",name='contact_task_affected_type'),
        CheckConstraint("planned_action IN ('CONTINUE','PAUSE','CANCEL','REWORK','REISSUE')",name='contact_task_planned_action'),
        CheckConstraint("source_system IN ('AGENT','ERP','MANUAL')",name='contact_task_source'),
        CheckConstraint("execution_source_system IS NULL OR execution_source_system IN ('AGENT','ERP','MANUAL')",name='contact_task_execution_source'),
        CheckConstraint('delivery_impact_days >= 0',name='contact_task_delivery_days'),
        CheckConstraint('estimated_amount IS NULL OR estimated_amount >= 0',name='contact_task_estimated_amount'),
        CheckConstraint('actual_hours IS NULL OR actual_hours >= 0',name='contact_task_actual_hours'),
        CheckConstraint('actual_amount IS NULL OR actual_amount >= 0',name='contact_task_actual_amount'))


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
