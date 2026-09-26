"""超级管理员开工通知草稿、部门分发回执与后置合同匹配事实。"""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_core.model_base import Base, IdentityMixin, J


ADMIN_START_DRAFT_STATUSES = (
    "ADMIN_PENDING_INPUT", "ADMIN_CONFIRMED", "DEPARTMENTS_NOTIFIED",
    "READY_FOR_CONTRACT_MATCH", "REJECTED", "NEEDS_REVIEW",
)
ADMIN_DEPARTMENT_KEYS = ("DESIGN", "PURCHASE", "MANUFACTURING", "ASSEMBLY", "FINANCE")


class AdminStartNoticeDraft(IdentityMixin, Base):
    """中标确认后自动生成、仅供超级管理员补充确认的内部开工草稿。"""

    __tablename__ = "admin_start_notice_draft"
    source_event_id: Mapped[str] = mapped_column(
        ForeignKey("audit_event.id"),
    )
    source_file_id: Mapped[str] = mapped_column(ForeignKey("file_object.id"), index=True)
    inbound_record_id: Mapped[str] = mapped_column(ForeignKey("document_intake.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("project.id"), index=True)
    project_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(35), default="ADMIN_PENDING_INPUT")
    decision: Mapped[str | None] = mapped_column(String(35))
    material_snapshot: Mapped[dict] = mapped_column(J, default=dict)
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("source_event_id", name="admin_start_notice_source_event"),
        CheckConstraint(
            "status IN ('ADMIN_PENDING_INPUT','ADMIN_CONFIRMED','DEPARTMENTS_NOTIFIED','READY_FOR_CONTRACT_MATCH','REJECTED','NEEDS_REVIEW')",
            name="admin_start_notice_draft_status",
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('INTERNAL_ACCEPTED','FULL_OUTSOURCE_ACCEPTED','REJECTED')",
            name="admin_start_notice_decision",
        ),
        CheckConstraint("current_revision >= 1", name="admin_start_notice_revision_positive"),
        CheckConstraint("row_version >= 1", name="admin_start_notice_row_version_positive"),
        CheckConstraint(
            "project_id IS NULL OR project_version IS NOT NULL",
            name="admin_start_notice_project_version",
        ),
    )


class AdminStartNoticeRevision(IdentityMixin, Base):
    """超级管理员对内部开工草稿的追加式修订。"""

    __tablename__ = "admin_start_notice_revision"
    draft_id: Mapped[str] = mapped_column(ForeignKey("admin_start_notice_draft.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(J, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    __table_args__ = (
        UniqueConstraint("draft_id", "revision", name="admin_start_notice_revision_unique"),
        CheckConstraint("revision >= 1", name="admin_start_notice_revision_number_positive"),
    )


class AdminStartDepartmentAck(IdentityMixin, Base):
    """项目部向单签部门分发内部通知单并跟踪其是否确认收到。"""

    __tablename__ = "admin_start_department_ack"
    draft_id: Mapped[str] = mapped_column(ForeignKey("admin_start_notice_draft.id"))
    department_key: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(30), default="SENT")
    notified_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    notified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acked_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ack_note: Mapped[str | None] = mapped_column(Text)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        UniqueConstraint("draft_id", "department_key", name="admin_start_department_ack_unique"),
        CheckConstraint(
            "status IN ('SENT','ACKNOWLEDGED')",
            name="admin_start_department_ack_status",
        ),
        CheckConstraint("row_version >= 1", name="admin_start_department_ack_row_version_positive"),
        CheckConstraint(
            "(status = 'SENT' AND acked_at IS NULL AND acked_by IS NULL) "
            "OR (status = 'ACKNOWLEDGED' AND acked_at IS NOT NULL AND acked_by IS NOT NULL)",
            name="admin_start_department_ack_receipt_consistency",
        ),
        Index("ix_admin_start_department_ack_draft_id", "draft_id"),
    )


class StartContractMatchCandidate(IdentityMixin, Base):
    """承接/委外确认后的合同匹配候选，不代表正式合同绑定。"""

    __tablename__ = "start_contract_match_candidate"
    draft_id: Mapped[str] = mapped_column(ForeignKey("admin_start_notice_draft.id"), index=True)
    draft_revision: Mapped[int] = mapped_column(Integer)
    target_fingerprint: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(35))
    target_id: Mapped[str] = mapped_column(String(36))
    target_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED")
    evidence_snapshot: Mapped[dict] = mapped_column(J, default=dict)
    proposed_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "draft_id", "draft_revision", "target_type", "target_id", "target_version", "target_fingerprint",
            name="start_contract_match_candidate_unique",
        ),
        CheckConstraint(
            "target_type IN ('SALES_CONTRACT','FULL_OUTSOURCE_CONTRACT','CONTRACT_INTAKE_GROUP')",
            name="start_contract_match_candidate_target_type",
        ),
        CheckConstraint(
            "status IN ('PROPOSED','CONFIRMED','REJECTED')",
            name="start_contract_match_candidate_status",
        ),
        CheckConstraint("target_version >= 1", name="start_contract_match_candidate_version_positive"),
    )
