from sqlalchemy import select

from . import models as m
from .authorization import access, select_fields
from .db import now
from .quote_tools import QuoteContextInput, _project_card, _resolve, _subjects


def _profile(db, user, project_id: str):
    profile = db.get(m.ProjectProfile, project_id)
    if not profile:
        return None
    fields = access(db, user, "project.read", {"project_id": project_id}).fields
    customer = db.get(m.Customer, profile.customer_id) if profile.customer_id else None
    return select_fields(
        {
            "customer_id": profile.customer_id,
            "customer_name": customer.name if customer else None,
            "customer_rule_key": customer.rule_key if customer else None,
            "owner_user_id": profile.owner_user_id,
            "execution_mode": profile.execution_mode,
            "customer_due_date": profile.customer_due_date.isoformat() if profile.customer_due_date else None,
            "settlement_status": profile.settlement_status,
        },
        fields | {"customer_id", "customer_name", "customer_rule_key", "owner_user_id", "execution_mode", "customer_due_date", "settlement_status"},
    )


def _has_dossier_access(db, user, project_id: str):
    return access(db, user, "project.dossier.read", {"project_id": project_id}).allowed


def _molds(db, user, project_id: str):
    if not _has_dossier_access(db, user, project_id):
        return [], False
    rows = []
    for link, mold in db.execute(
        select(m.ProjectMold, m.Mold)
        .join(m.Mold, m.Mold.id == m.ProjectMold.mold_id)
        .where(m.ProjectMold.project_id == project_id)
        .order_by(m.Mold.internal_number)
        .limit(21)
    ):
        rows.append({"id": mold.id, "internal_number": mold.internal_number, "name": mold.name, "status": mold.status})
    return rows[:20], len(rows) > 20


def _limited_subjects(db, user, project_id: str, kind: str, allowed_tools: set[str]):
    context_tools = {
        "quote_acceptance": {"query_bid_intake_context", "query_quote_acceptance_context", "query_quote_evaluation_context"},
        "sales_contract": {"query_contract_context"},
        "internal_start": {"query_internal_start_readiness"},
    }
    direct_tool = "query_" + kind
    if direct_tool not in allowed_tools and not (context_tools.get(kind, set()) & allowed_tools):
        return [], False
    return _subjects(db, user, project_id, kind, allowed_tools | {direct_tool})


def _decision(row: dict):
    detail = row.get("detail") or {}
    return {
        "id": row.get("id"),
        "number": row.get("number"),
        "status": row.get("status"),
        "decision": detail.get("decision"),
        "execution_mode": detail.get("execution_mode"),
        "effective_date": detail.get("effective_date"),
        "amount": detail.get("amount"),
        "currency": detail.get("currency"),
        "evidence": detail.get("evidence"),
        "source_subject_id": detail.get("source_subject_id"),
    }


def _contract(row: dict):
    detail = row.get("detail") or {}
    return {
        "id": row.get("id"),
        "number": row.get("number"),
        "status": row.get("status"),
        "contract_number": detail.get("contract_number"),
        "amount": detail.get("amount"),
        "currency": detail.get("currency"),
        "expected_date": detail.get("expected_date"),
        "stages_count": len(detail.get("stages") or []),
    }


def _latest(rows: list[dict], decision: str | None = None):
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        if row.get("status") == "EFFECTIVE" and (decision is None or detail.get("decision") == decision):
            return row
    return None


def _analysis(project, profile: dict | None, quote_rows: list[dict], contracts: list[dict], starts: list[dict], molds: list[dict]):
    latest_accept = _latest(quote_rows, "ACCEPT")
    latest_reject = _latest(quote_rows, "REJECT")
    latest_start = _latest(starts, "START")
    effective_contracts = [row for row in contracts if row.get("status") == "EFFECTIVE"]
    gaps = []
    warnings = []
    if not profile or not profile.get("customer_id"):
        gaps.append("未见已关联并人工确认的客户分类。")
    if profile and not profile.get("customer_rule_key"):
        gaps.append("未见客户规则键，不能区分海信、海尔或其他客户处理规则。")
    if not molds:
        gaps.append("未见当前可见的内部模具或历史模具关联。")
    gaps.append("未见客户邮件、客户平台文件或人工上传来源记录；不能判断中标资料来自邮件、合同还是平台。")
    gaps.append("未见模具图片或 UG 图片上传依据；设计文员图片补充仍需专门材料或附件识别。")
    if not (latest_accept or latest_reject):
        gaps.append("未见有效承接或有效拒单决定。")
    if latest_accept and not latest_accept.get("detail", {}).get("execution_mode"):
        warnings.append("有效承接缺少最终加工方式，不能判断内部生产或整套委外。")
    if effective_contracts and not latest_accept:
        warnings.append("当前可见销售合同不等于已人工确认承接。")
    if latest_start and not latest_accept:
        warnings.append("当前可见正式开工缺少可见承接依据，需要核对历史权限或资料。")
    if project.status == "ACTIVE" and not latest_start:
        warnings.append("项目已进行中但当前未见有效正式开工通知，请核对开工依据。")
    return {
        "customer_classification": {
            "customer_id": (profile or {}).get("customer_id"),
            "customer_name": (profile or {}).get("customer_name"),
            "customer_rule_key": (profile or {}).get("customer_rule_key"),
            "is_confirmed_from_current_facts": bool(profile and profile.get("customer_id")),
        },
        "latest_effective_acceptance": _decision(latest_accept) if latest_accept else None,
        "latest_effective_rejection": _decision(latest_reject) if latest_reject else None,
        "latest_effective_internal_start": _decision(latest_start) if latest_start else None,
        "effective_sales_contracts": [_contract(row) for row in effective_contracts[:20]],
        "known_molds": molds,
        "gaps": gaps,
        "warnings": warnings,
        "derived_status": {
            "has_customer_classification": bool(profile and profile.get("customer_id")),
            "has_effective_acceptance": bool(latest_accept),
            "has_effective_rejection": bool(latest_reject),
            "has_sales_contract": bool(effective_contracts),
            "has_internal_start": bool(latest_start),
            "has_mold_relation": bool(molds),
            "has_source_document_record": False,
            "has_mold_image_evidence": False,
        },
    }


def query(db, user, data: QuoteContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data, allowed_tools)
    limitations = [
        "只读取当前用户可见且具备报价与承接读取权限的项目。",
        "本工具只核对中标接收、客户分类、承接/拒单、合同和开工上下文，不读取邮箱、不连接客户平台、不上传合同或图片。",
        "海尔等客户平台自动对接不作为已具备能力；当前只保留人工接收、维护、确认和依据核对口径。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project:
        profile = _profile(db, user, project.id)
        molds, molds_truncated = _molds(db, user, project.id)
        quote_rows, quote_truncated = _limited_subjects(db, user, project.id, "quote_acceptance", allowed_tools)
        contracts, contracts_truncated = _limited_subjects(db, user, project.id, "sales_contract", allowed_tools)
        starts, starts_truncated = _limited_subjects(db, user, project.id, "internal_start", allowed_tools)
        if not _has_dossier_access(db, user, project.id):
            limitations.append("未具备项目业务档案读取权限，不能返回模具关系或更宽业务线索。")
        if molds_truncated:
            limitations.append("模具关联最多返回前20条。")
        if quote_truncated:
            limitations.append("承接/拒单记录最多返回最新20条。")
        if contracts_truncated:
            limitations.append("销售合同最多返回最新20条。")
        if starts_truncated:
            limitations.append("正式开工通知最多返回最新20条。")
        return {
            "resolution": "RESOLVED",
            "data": [
                {
                    "project": _project_card(db, user, project, alternatives or ("项目定位",)),
                    "project_profile": profile,
                    "quote_acceptance": [_decision(row) for row in quote_rows],
                    "sales_contracts": [_contract(row) for row in contracts],
                    "internal_starts": [_decision(row) for row in starts],
                    "analysis": _analysis(project, profile, quote_rows, contracts, starts, molds),
                }
            ],
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations,
        }
    if alternatives is None:
        return {
            "resolution": "NOT_FOUND_OR_FORBIDDEN",
            "data": [],
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations,
        }
    if alternatives:
        return {
            "resolution": "MULTIPLE_CANDIDATES",
            "data": alternatives,
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations + ["线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。"],
        }
    return {
        "resolution": "NOT_FOUND",
        "data": [],
        "source": "agent_db",
        "as_of": now().isoformat(),
        "limitations": limitations,
    }
