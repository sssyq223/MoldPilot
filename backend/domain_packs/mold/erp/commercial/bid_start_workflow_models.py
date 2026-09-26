"""中标确认到正式开工通知的领域事实。"""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_core.model_base import Base, IdentityMixin, J


class BidNoticeMatch(IdentityMixin, Base):
    """一条已人工确认的中标事件对应的项目匹配记录。"""

    __tablename__ = "bid_notice_match"
    confirmation_event_id: Mapped[str] = mapped_column(ForeignKey("audit_event.id"), unique=True)
    source_file_id: Mapped[str] = mapped_column(ForeignKey("file_object.id"), index=True)
    inbound_record_id: Mapped[str] = mapped_column(ForeignKey("document_intake.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="PENDING_MATCH")
    project_id: Mapped[str | None] = mapped_column(ForeignKey("project.id"), index=True)
    project_version: Mapped[int | None] = mapped_column(Integer)
    bid_intake_case_id: Mapped[str | None] = mapped_column(ForeignKey("bid_intake_case.id"))
    bid_intake_revision_id: Mapped[str | None] = mapped_column(ForeignKey("bid_intake_revision.id"))
    evidence_snapshot: Mapped[dict] = mapped_column(J, default=dict)
    match_evidence: Mapped[str | None] = mapped_column(Text)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    matched_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_MATCH','MATCHED','INTAKE_CONFIRMED','REJECTED')",
            name="bid_notice_match_status",
        ),
        CheckConstraint(
            "project_id IS NULL OR project_version IS NOT NULL",
            name="bid_notice_match_project_version",
        ),
        CheckConstraint("row_version >= 1", name="bid_notice_match_row_version_positive"),
    )


class StartNotice(IdentityMixin, Base):
    """一个中标接收版本对应的不可变开工通知版本。"""

    __tablename__ = "start_notice"
    bid_match_id: Mapped[str] = mapped_column(ForeignKey("bid_notice_match.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), index=True)
    bid_intake_revision_id: Mapped[str] = mapped_column(ForeignKey("bid_intake_revision.id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(35), default="DRAFT")
    material_snapshot: Mapped[dict] = mapped_column(J, default=dict)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    __table_args__ = (
        UniqueConstraint("bid_match_id", "version", name="start_notice_match_version"),
        CheckConstraint("version >= 1", name="start_notice_version_positive"),
        CheckConstraint(
            "status IN ('DRAFT','DEPARTMENT_REVIEW','PROJECT_ACCEPTED','FULL_OUTSOURCE_ACCEPTED','REJECTED','RETURNED')",
            name="start_notice_status",
        ),
    )


class StartNoticeDepartmentAck(IdentityMixin, Base):
    """单个部门对某一开工通知版本的独立回执。"""

    __tablename__ = "start_notice_department_ack"
    start_notice_id: Mapped[str] = mapped_column(ForeignKey("start_notice.id"))
    start_notice_version: Mapped[int] = mapped_column(Integer)
    department_key: Mapped[str] = mapped_column(String(60))
    department_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    handoff_status: Mapped[str] = mapped_column(String(20), default="UNASSIGNED")
    recipient_snapshot: Mapped[list] = mapped_column(J, default=list)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("outbox_event.id"))
    evidence: Mapped[str | None] = mapped_column(Text)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    responded_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "start_notice_id", "start_notice_version", "department_key",
            name="start_notice_department_ack_unique",
        ),
        Index("ix_start_notice_department_ack_notice", "start_notice_id"),
        CheckConstraint("start_notice_version >= 1", name="start_notice_ack_version_positive"),
        CheckConstraint(
            "status IN ('PENDING','ACCEPTED','RETURNED','NEED_INFO')",
            name="start_notice_department_ack_status",
        ),
        CheckConstraint(
            "handoff_status IN ('QUEUED','UNASSIGNED')",
            name="start_notice_department_handoff_status",
        ),
        CheckConstraint(
            "(handoff_status = 'QUEUED' AND event_id IS NOT NULL) OR "
            "(handoff_status = 'UNASSIGNED' AND event_id IS NULL)",
            name="start_notice_department_handoff_event",
        ),
    )


class ProjectStartDecision(IdentityMixin, Base):
    """项目部对某个开工通知版本作出的最终决定。"""

    __tablename__ = "project_start_decision"
    start_notice_id: Mapped[str] = mapped_column(ForeignKey("start_notice.id"))
    start_notice_version: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(35))
    reason: Mapped[str] = mapped_column(Text)
    department_snapshot: Mapped[list] = mapped_column(J, default=list)
    decided_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "start_notice_id", "start_notice_version", name="project_start_decision_notice_version",
        ),
        Index("ix_project_start_decision_notice", "start_notice_id"),
        CheckConstraint("start_notice_version >= 1", name="project_start_decision_version_positive"),
        CheckConstraint(
            "decision IN ('PROJECT_ACCEPTED','FULL_OUTSOURCE_ACCEPTED','REJECTED','RETURNED')",
            name="project_start_decision_value",
        ),
    )


class PostStartBinding(IdentityMixin, Base):
    """合同或核算清单在项目部最终决定后的正式绑定快照。"""

    __tablename__ = "post_start_binding"
    project_decision_id: Mapped[str] = mapped_column(ForeignKey("project_start_decision.id"))
    start_notice_id: Mapped[str] = mapped_column(ForeignKey("start_notice.id"))
    start_notice_version: Mapped[int] = mapped_column(Integer)
    target_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[str] = mapped_column(String(36))
    target_version: Mapped[int] = mapped_column(Integer)
    target_fingerprint: Mapped[str | None] = mapped_column(String(64))
    mold_snapshot: Mapped[list] = mapped_column(J, default=list)
    bound_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "target_type", "target_id", "target_version", name="post_start_binding_target_version",
        ),
        Index("ix_post_start_binding_decision", "project_decision_id"),
        Index("ix_post_start_binding_notice", "start_notice_id"),
        CheckConstraint("start_notice_version >= 1", name="post_start_binding_notice_version_positive"),
        CheckConstraint("target_version >= 1", name="post_start_binding_target_version_positive"),
        CheckConstraint(
            "target_type IN ('SALES_CONTRACT','ACCOUNTING_CHECKLIST')",
            name="post_start_binding_target_type",
        ),
    )
