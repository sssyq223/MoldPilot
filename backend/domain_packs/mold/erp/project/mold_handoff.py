"""Read-only ERP to Agent project/mold handoff evidence.

The ERP remains authoritative for the internal mold number. This module only
returns bounded candidate evidence for a human-controlled handoff; it never
creates a local mold, changes the ERP project, or infers a relation from a
similar number.
"""

from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.config import settings
from domain_packs.mold.erp_adapter import ERPClient, decrypt
from domain_packs.mold.ports.errors import DomainError


def _local_numbers(db, project_id):
    return [
        value
        for value in db.scalars(
            select(m.Mold.internal_number)
            .join(m.ProjectMold, m.ProjectMold.mold_id == m.Mold.id)
            .where(m.ProjectMold.project_id == project_id)
            .order_by(m.Mold.internal_number)
            .limit(20)
        )
        if value
    ]


def _mapped_project_code(db, project):
    """Return the confirmed ERP project code, if this Agent project has one."""
    scalar = getattr(db, "scalar", None)
    if not callable(scalar):
        return None
    mapping = scalar(
        select(m.ProjectERPMapping).where(
            m.ProjectERPMapping.project_id == project.id,
            m.ProjectERPMapping.status == "CONFIRMED",
        )
    )
    return mapping.erp_project_code if mapping else None


def _candidate_state(project_code, records, local_numbers):
    wanted = str(project_code or "").strip().casefold()
    exact = [
        row
        for row in records
        if str(row.get("project_code") or "").strip().casefold() == wanted
        and str(row.get("mold_code") or "").strip()
    ]
    if local_numbers:
        return "LOCAL_ASSOCIATION_PRESENT", exact
    if len(exact) == 1:
        return "ERP_CANDIDATE_REQUIRES_HANDOFF", exact
    if len(exact) > 1:
        return "ERP_MULTIPLE_CANDIDATES", exact
    return "ERP_NO_UNIQUE_CANDIDATE", exact


def query(db, user, project, client_factory=None, erp_project_code=None):
    local_numbers = _local_numbers(db, project.id)
    mapped_code = _mapped_project_code(db, project)
    requested_code = str(erp_project_code or "").strip()
    effective_project_code = (
        requested_code
        or mapped_code
        or project.code
    )
    result = {
        "status": "NOT_CONFIGURED",
        "handoff_state": (
            "LOCAL_ASSOCIATION_PRESENT" if local_numbers else "ERP_NOT_CONFIGURED"
        ),
        "source": "ERP",
        "project_code": project.code,
        "agent_project_code": project.code,
        # Leave this empty until an ERP code is explicitly supplied or a
        # confirmed mapping exists; the Agent project code is not an ERP fact.
        "erp_project_code": requested_code or mapped_code,
        "project_mapping_state": "CONFIRMED" if mapped_code else "UNCONFIRMED",
        "local_internal_mold_numbers": local_numbers,
        "records": [],
        "limitations": [],
    }
    if not settings().erp_base_url:
        result["limitations"].append("ERP 服务地址未配置，未读取原系统项目/模具候选。")
        return result
    identity = db.get(m.ERPIdentity, user.id)
    if not identity or not identity.token_ciphertext:
        result["status"] = "LOGIN_REQUIRED"
        result["handoff_state"] = (
            "LOCAL_ASSOCIATION_PRESENT" if local_numbers else "ERP_LOGIN_REQUIRED"
        )
        result["limitations"].append("当前用户尚未绑定或验证 ERP 身份，未读取原系统项目/模具候选。")
        return result
    try:
        token = decrypt(identity.token_ciphertext)
        client_factory = client_factory or ERPClient
        client = client_factory(token)
        try:
            context = client.business_molds(project_no=effective_project_code)
            # When the Agent project has no confirmed ERP mapping, a strict
            # project-code lookup cannot discover a differently numbered ERP
            # project.  A bounded read of the registered ERP candidate feed
            # makes those candidates visible while keeping the mapping human.
            if (
                not context.get("records")
                and not requested_code
                and not mapped_code
            ):
                context = client.business_molds()
                result["project_mapping_state"] = "REQUIRES_HUMAN_MAPPING"
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
    except DomainError as error:
        result["status"] = error.code
        result["handoff_state"] = (
            "LOCAL_ASSOCIATION_PRESENT" if local_numbers else "ERP_READ_FAILED"
        )
        result["limitations"].append(error.message)
        return result
    records = context.get("records") or []
    state, exact = _candidate_state(effective_project_code, records, local_numbers)
    if (
        not exact
        and records
        and not requested_code
        and not mapped_code
    ):
        state = "ERP_PROJECT_MAPPING_REQUIRED"
        result["project_mapping_state"] = "REQUIRES_HUMAN_MAPPING"
    result.update(
        {
            "status": "RESOLVED",
            "handoff_state": state,
            "records": records[:200],
            "exact_project_records": exact[:20],
            "as_of": context.get("as_of"),
            "limitations": (context.get("limitations") or [])
            + [
                "ERP 模具号只有在原系统确认并由 Agent 侧人工完成交接后，才能作为正式开工冻结对象；本次查询不会自动建立 project_mold。"
            ],
        }
    )
    return result
