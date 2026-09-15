from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import String, DateTime, Date, Integer, Boolean, Text, Numeric, ForeignKey, UniqueConstraint, CheckConstraint, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, now

J = JSON().with_variant(JSONB, "postgresql")
def uid(): return str(uuid4())


class IdentityMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(IdentityMixin, Base):
    __tablename__ = "app_user"
    username: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    department: Mapped[str] = mapped_column(String(100), default="")
    password_hash: Mapped[str] = mapped_column(Text)
    super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    security_version: Mapped[int] = mapped_column(Integer, default=1)


class LoginSession(IdentityMixin, Base):
    __tablename__ = "login_session"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssignmentGroup(IdentityMixin, Base):
    __tablename__ = "assignment_group"
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint('kind', 'name'), CheckConstraint("kind IN ('ROLE','DEPARTMENT')"))


class AssignmentMember(Base):
    __tablename__ = "assignment_member"
    group_id: Mapped[str] = mapped_column(ForeignKey('assignment_group.id'), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('app_user.id'), primary_key=True)
    is_head: Mapped[bool] = mapped_column(Boolean, default=False)


class Grant(IdentityMixin, Base):
    __tablename__ = "permission_grant"
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    permission: Mapped[str] = mapped_column(String(100))
    effect: Mapped[str] = mapped_column(String(5))
    scope: Mapped[dict] = mapped_column(J)
    fields: Mapped[list] = mapped_column(J)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(Text)
    granted_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    __table_args__ = (CheckConstraint("effect IN ('ALLOW','DENY')"),)


class Project(IdentityMixin, Base):
    __tablename__ = "project"
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class Material(IdentityMixin, Base):
    __tablename__ = "material"
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    category: Mapped[str] = mapped_column(String(60))
    unit: Mapped[str] = mapped_column(String(20))


class PurchaseRequest(IdentityMixin, Base):
    __tablename__ = "purchase_request"
    number: Mapped[str] = mapped_column(String(80), unique=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    remark: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    round_no: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (CheckConstraint("status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','RETURNED')"),)


class PurchaseLine(IdentityMixin, Base):
    __tablename__ = "purchase_request_line"
    request_id: Mapped[str] = mapped_column(ForeignKey("purchase_request.id"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("material.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    due_date: Mapped[date] = mapped_column(Date)
    __table_args__ = (CheckConstraint("quantity > 0"),)


class WorkflowCategory(IdentityMixin, Base):
    __tablename__ = "workflow_category"
    name: Mapped[str] = mapped_column(String(100), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class MaterialTemplate(IdentityMixin, Base):
    __tablename__ = 'material_template'
    template_key: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(20),default='DRAFT')
    contract: Mapped[dict] = mapped_column(J)
    package_hash: Mapped[str] = mapped_column(String(64),default='')
    __table_args__ = (UniqueConstraint('template_key','version'),CheckConstraint("status IN ('DRAFT','PUBLISHED')"))


class MaterialTemplateXlsxMapping(IdentityMixin, Base):
    __tablename__ = 'material_template_xlsx_mapping'
    template_id: Mapped[str] = mapped_column(ForeignKey('material_template.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(150))
    mapping: Mapped[dict] = mapped_column(J)
    mapping_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    __table_args__ = (UniqueConstraint('template_id','version'),)


class WorkflowDefinition(IdentityMixin, Base):
    __tablename__ = "workflow_definition"
    process_key: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(150))
    category_id: Mapped[str | None] = mapped_column(ForeignKey('workflow_category.id'))
    material_template_id: Mapped[str | None] = mapped_column(ForeignKey('material_template.id'))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    config: Mapped[dict] = mapped_column(J)
    bpmn_xml: Mapped[str] = mapped_column(Text, default="")
    package_hash: Mapped[str] = mapped_column(String(64), default="")
    __table_args__ = (UniqueConstraint("process_key", "version"),)


class ApprovalInstance(IdentityMixin, Base):
    __tablename__ = "approval_instance"
    request_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_request.id"))
    subject_id: Mapped[str | None] = mapped_column(ForeignKey("business_subject.id"))
    definition_id: Mapped[str] = mapped_column(ForeignKey("workflow_definition.id"))
    revision: Mapped[int] = mapped_column(Integer)
    round_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="RUNNING")
    incident: Mapped[str | None] = mapped_column(Text)
    stage_index: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    snapshot: Mapped[dict] = mapped_column(J)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    engine_state: Mapped[dict] = mapped_column(J)
    assignment_snapshots: Mapped[dict] = mapped_column(J, default=dict)
    __table_args__ = (UniqueConstraint("request_id", "revision", "round_no"),
                     UniqueConstraint("subject_id", "revision", "round_no"),
                     CheckConstraint("(request_id IS NULL) <> (subject_id IS NULL)", name="approval_one_subject"))


class ApprovalSeat(IdentityMixin, Base):
    __tablename__ = "approval_seat"
    instance_id: Mapped[str] = mapped_column(ForeignKey("approval_instance.id"), index=True)
    stage_index: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint("instance_id", "stage_index", "user_id"),)


class ApprovalAction(IdentityMixin, Base):
    __tablename__ = "approval_action"
    instance_id: Mapped[str] = mapped_column(ForeignKey("approval_instance.id"))
    seat_id: Mapped[str] = mapped_column(ForeignKey("approval_seat.id"), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    user_snapshot: Mapped[dict] = mapped_column(J)
    decision: Mapped[str] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(Text)
    snapshot_hash: Mapped[str] = mapped_column(String(64))


class AgentApprovalDelegation(IdentityMixin, Base):
    __tablename__ = "agent_approval_delegation"
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    process_key: Mapped[str] = mapped_column(String(80))
    node_key: Mapped[str] = mapped_column(String(80))
    decision: Mapped[str] = mapped_column(String(20), default="APPROVE")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("user_id", "process_key", "node_key", "decision"),
        CheckConstraint("decision IN ('APPROVE')", name="agent_approval_delegation_decision"),
    )


class HumanIntent(IdentityMixin, Base):
    __tablename__ = "human_action_intent"
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    action: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(J)
    payload_hash: Mapped[str] = mapped_column(String(64))
    challenge_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    receipt: Mapped[dict | None] = mapped_column(J)


class AuditEvent(IdentityMixin, Base):
    __tablename__ = "audit_event"
    user_id: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    action: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict] = mapped_column(J)


class Outbox(IdentityMixin, Base):
    __tablename__ = "outbox_event"
    kind: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(J)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_id: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(80))
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Inbox(IdentityMixin, Base):
    __tablename__ = "inbox_event"
    event_id: Mapped[str] = mapped_column(ForeignKey("outbox_event.id"), unique=True)


class Notification(IdentityMixin, Base):
    __tablename__ = "notification"
    event_id: Mapped[str] = mapped_column(ForeignKey("outbox_event.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    title: Mapped[str] = mapped_column(String(150))
    resource_id: Mapped[str] = mapped_column(String(100))
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("event_id", "user_id"),)


class Conversation(IdentityMixin, Base):
    __tablename__ = "ai_conversation"
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    title: Mapped[str] = mapped_column(String(150))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class Run(IdentityMixin, Base):
    __tablename__ = "ai_run"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("ai_conversation.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    security_version: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="QUEUED")
    checkpoint: Mapped[dict] = mapped_column(J, default=dict)
    result: Mapped[dict | None] = mapped_column(J)
    lease_epoch: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Step(IdentityMixin, Base):
    __tablename__ = "ai_step"
    run_id: Mapped[str] = mapped_column(ForeignKey("ai_run.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    tool: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(J)
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)


class Capability(IdentityMixin, Base):
    __tablename__ = "agent_capability_assignment"
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    key: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("user_id", "kind", "key"),)


# Register all local domain tables in the same Alembic metadata/version chain.
from .domain_models import *  # noqa: E402,F403
from .contact_models import *  # noqa: E402,F403
from .file_models import *  # noqa: E402,F403
