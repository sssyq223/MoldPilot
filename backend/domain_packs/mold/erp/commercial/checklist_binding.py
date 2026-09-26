"""MoldPilot 本地核算资料的只读绑定适配器。"""

from domain_packs.mold import models as m
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.errors import DomainError


LOCAL_KINDS = {"local_cost_sheet", "local_settlement_sheet"}


def resolve_reference(db, user, target_id, target_version, *, client_factory=None):
    """读取本地核算资料，不接受外部系统档案号或客户端。"""
    if target_version < 1:
        raise DomainError("TARGET_VERSION_INVALID", "本地核算资料版本无效")
    row = db.get(m.BusinessSubject, str(target_id))
    if not row or row.kind not in LOCAL_KINDS:
        raise DomainError("LOCAL_BINDING_NOT_READY", "请先登记可核对的本地核算资料", 409)
    if row.revision != target_version:
        raise DomainError("VERSION_CONFLICT", "本地核算资料版本已变化，请重新查询", 409)
    return {
        "kind": "local_accounting_checklist",
        "id": row.id,
        "project_id": row.project_id,
        "revision": row.revision,
        "fingerprint": content_hash({"id": row.id, "revision": row.revision, "number": row.number}),
        "source_system": "agent_db",
        "source_type": row.kind,
    }
