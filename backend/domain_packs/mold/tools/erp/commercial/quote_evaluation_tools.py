from domain_packs.mold import models as m
from domain_packs.mold.authorization import access, select_fields
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.commercial.quote_tools import QuoteContextInput, _project_card, _resolve, _subjects


def _project_profile(db, user, project_id: str):
    profile = db.get(m.ProjectProfile, project_id)
    if not profile:
        return None
    fields = access(db, user, "project.read", {"project_id": project_id}).fields
    return select_fields(
        {
            "customer_id": profile.customer_id,
            "owner_user_id": profile.owner_user_id,
            "execution_mode": profile.execution_mode,
            "customer_due_date": profile.customer_due_date.isoformat() if profile.customer_due_date else None,
            "settlement_status": profile.settlement_status,
        },
        fields | {"customer_id", "owner_user_id", "execution_mode", "customer_due_date", "settlement_status"},
    )


def _limited_subjects(db, user, project_id: str, kind: str, allowed_tools: set[str]):
    context_tools = {
        "quotation": {"query_quote_evaluation_context", "query_quote_acceptance_context"},
        "quote_acceptance": {"query_quote_evaluation_context", "query_quote_acceptance_context"},
        "sales_contract": {"query_contract_context"},
        "full_outsource_contract": {"query_contract_context"},
        "project_plan": {"query_project_plan_context"},
        "plan_change": {"query_project_plan_context"},
    }
    direct_tool = "query_" + kind
    if direct_tool not in allowed_tools and not (context_tools.get(kind, set()) & allowed_tools):
        return [], False
    return _subjects(db, user, project_id, kind, allowed_tools | {direct_tool})


def _quote_record(row: dict):
    detail = row.get("detail") or {}
    return {
        "id": row.get("id"),
        "number": row.get("number"),
        "status": row.get("status"),
        "revision": row.get("revision"),
        "decision": detail.get("decision"),
        "execution_mode": detail.get("execution_mode"),
        "effective_date": detail.get("effective_date"),
        "amount": detail.get("amount"),
        "currency": detail.get("currency"),
        "evidence": detail.get("evidence"),
        "source_subject_id": detail.get("source_subject_id"),
        "has_price_basis": bool(detail.get("amount") and detail.get("currency")),
        "has_processing_mode": bool(detail.get("execution_mode")),
        "has_written_evidence": bool((detail.get("evidence") or "").strip()),
    }


def _quotation_record(row: dict):
    detail = row.get("detail") or {}
    feedback = detail.get("feedback") or []
    return {
        "record_type": "quotation_version",
        "id": row.get("id"),
        "number": row.get("number"),
        "status": row.get("status"),
        "quotation_status": row.get("status"),
        "revision": row.get("revision"),
        "quotation_number": detail.get("quotation_number"),
        "version": detail.get("version"),
        "previous_id": detail.get("previous_id"),
        "preliminary_execution_mode": detail.get("preliminary_execution_mode"),
        "quoted_amount": detail.get("quoted_amount"),
        "currency": detail.get("currency"),
        "promised_delivery_date": detail.get("promised_delivery_date"),
        "payment_terms": detail.get("payment_terms"),
        "cost_amount": detail.get("cost_amount"),
        "cost_evidence": detail.get("cost_evidence"),
        "process_analysis": detail.get("process_analysis"),
        "duration_days": detail.get("duration_days"),
        "duration_evidence": detail.get("duration_evidence"),
        "supplier_quote_amount": detail.get("supplier_quote_amount"),
        "supplier_delivery_date": detail.get("supplier_delivery_date"),
        "supplier_requirements": detail.get("supplier_requirements"),
        "supplier_quote_evidence": detail.get("supplier_quote_evidence"),
        "customer_company_snapshot": detail.get("customer_company_snapshot"),
        "customer_contact_snapshot": detail.get("customer_contact_snapshot"),
        "owner_user_id": detail.get("owner_user_id"),
        "source_summary": detail.get("source_summary") or {},
        "sources": detail.get("sources") or [],
        "feedback": feedback,
        "has_structured_evaluation": bool(
            detail.get("cost_evidence") and detail.get("process_analysis")
            and detail.get("duration_days") and detail.get("duration_evidence")
        ),
    }


def _mode_label(value):
    return {"INTERNAL": "内部加工", "FULL_OUTSOURCE": "整套委外"}.get(value, value)


def _contract_summary(row: dict):
    detail = row.get("detail") or {}
    return {
        "number": row.get("number"),
        "status": row.get("status"),
        "contract_number": detail.get("contract_number"),
        "amount": detail.get("amount"),
        "currency": detail.get("currency"),
        "expected_date": detail.get("expected_date"),
        "stages_count": len(detail.get("stages") or []),
    }


def _plan_summary(rows: list[dict]):
    tasks = []
    for row in rows:
        for task in (row.get("detail") or {}).get("tasks") or []:
            tasks.append(
                {
                    "plan_number": row.get("number"),
                    "task_key": task.get("key"),
                    "task_name": task.get("name"),
                    "status": task.get("status"),
                    "planned_start": task.get("planned_start"),
                    "planned_end": task.get("planned_end"),
                }
            )
    return tasks[:20], len(tasks) > 20


def _analysis(project, profile: dict | None, quotation_records: list[dict], quote_records: list[dict], contracts: list[dict], outsource_contracts: list[dict], plan_tasks: list[dict]):
    effective_quotations = [row for row in quotation_records if row.get("quotation_status") == "EFFECTIVE"]
    open_quotations = [row for row in quotation_records if row.get("quotation_status") in {
        "DRAFT", "SUBMITTED", "RETURNED", "APPLY_BLOCKED"
    }]
    effective = [row for row in quote_records if row.get("status") == "EFFECTIVE"]
    latest_accept = next((row for row in effective if row.get("decision") == "ACCEPT"), None)
    latest_reject = next((row for row in effective if row.get("decision") == "REJECT"), None)
    preliminary_modes = [row.get("preliminary_execution_mode") for row in quotation_records if row.get("preliminary_execution_mode")]
    final_modes = [row.get("execution_mode") for row in quote_records if row.get("execution_mode")]
    modes = preliminary_modes + final_modes
    accepted_mode = latest_accept.get("execution_mode") if latest_accept else None
    profile_mode = (profile or {}).get("execution_mode")
    warnings = []
    gaps = []
    if not quotation_records and not quote_records:
        gaps.append("未见报价评估、报价提交或承接/拒单记录。")
    if not quotation_records and not any(row.get("has_price_basis") for row in quote_records):
        gaps.append("未见报价金额、币种或价格依据。")
    if not any(row.get("has_structured_evaluation") for row in quotation_records):
        gaps.append("未见结构化拆分的成本核算、粗略工艺分析和工期估算明细；当前只能读取综合依据文本。")
    if not quotation_records or not any(row.get("sources") for row in quotation_records):
        gaps.append("未见与报价版本冻结关联的客户资料或附件来源。")
    if not modes and not profile_mode:
        gaps.append("未见最终加工方式。")
    feedback = [item for row in quotation_records for item in row.get("feedback", [])]
    if not (feedback or latest_accept or latest_reject or contracts):
        gaps.append("未见客户反馈、承接、拒单或后续合同事实。")
    if latest_accept and not latest_accept.get("has_processing_mode"):
        warnings.append("有效承接缺少最终加工方式，不能下推执行方式。")
    if accepted_mode and profile_mode and accepted_mode != profile_mode:
        warnings.append(f"项目档案加工方式为{_mode_label(profile_mode)}，最新有效承接为{_mode_label(accepted_mode)}，需要核对是否有后续变更依据。")
    if accepted_mode and preliminary_modes and accepted_mode != preliminary_modes[0]:
        warnings.append("承接确认的最终加工方式与报价阶段初步方式不同，需要保留评估和审批依据。")
    if accepted_mode == "FULL_OUTSOURCE" and not outsource_contracts:
        warnings.append("最新有效承接为整套委外，但当前未见可见的整套委外合同。")
    return {
        "status_summary": {
            "project_status": project.status,
            "quotation_version_status": (
                "DATA_CONFLICT" if len(effective_quotations) > 1
                else "EFFECTIVE" if effective_quotations
                else "WAITING_APPROVAL" if open_quotations
                else "NOT_CREATED"
            ),
            "acceptance_decision_status": (
                "ACCEPTED" if latest_accept else "REJECTED" if latest_reject else "NOT_DECIDED"
            ),
            "sales_contract_status": (
                "EFFECTIVE" if any(row.get("status") == "EFFECTIVE" for row in contracts)
                else "NOT_RECORDED"
            ),
        },
        "current_effective_quotation": effective_quotations[0] if len(effective_quotations) == 1 else None,
        "open_quotation_versions": open_quotations,
        "latest_effective_acceptance": latest_accept,
        "latest_effective_rejection": latest_reject,
        "quotation_versions": quotation_records,
        "quote_acceptance_decisions": quote_records,
        "known_price_versions": quotation_records or [row for row in quote_records if row.get("has_price_basis")],
        "preliminary_processing_modes": sorted({_mode_label(mode) for mode in preliminary_modes if mode}),
        "final_processing_modes": sorted({_mode_label(mode) for mode in final_modes if mode}),
        "customer_feedback_signals": {
            "feedback_records": feedback,
            "has_effective_acceptance": bool(latest_accept),
            "has_effective_rejection": bool(latest_reject),
            "has_sales_contract": bool(contracts),
        },
        "related_plan_tasks": plan_tasks,
        "gaps": gaps,
        "warnings": warnings,
        "derived_status": {
            "has_any_quote_basis": bool(quotation_records or quote_records),
            "has_price_basis": bool(quotation_records) or any(row.get("has_price_basis") for row in quote_records),
            "has_processing_mode": bool(modes or profile_mode),
            "has_customer_feedback_or_downstream_fact": bool(feedback or latest_accept or latest_reject or contracts),
            "has_structured_cost_process_duration_breakdown": any(row.get("has_structured_evaluation") for row in quotation_records),
            "has_mode_conflict": bool(accepted_mode and profile_mode and accepted_mode != profile_mode),
        },
    }


def query(db, user, data: QuoteContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data, allowed_tools)
    limitations = [
        "只读取当前用户可见且具备报价与承接读取权限的项目。",
        "本工具只核对报价评估上下文，不接收客户资料、不生成报价版本、不提交客户反馈、不切换加工方式。",
        "成本核算、技术工艺分析、项目工期估算和客户反馈必须以正式材料或后续适配来源为准；综合证据文本不能替代结构化明细。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project:
        matched_by = alternatives or ("项目定位",)
        quotation_rows, quotation_truncated = _limited_subjects(db, user, project.id, "quotation", allowed_tools)
        quote_rows, quote_truncated = _limited_subjects(db, user, project.id, "quote_acceptance", allowed_tools)
        contracts, contracts_truncated = _limited_subjects(db, user, project.id, "sales_contract", allowed_tools)
        outsource_contracts, outsource_truncated = _limited_subjects(db, user, project.id, "full_outsource_contract", allowed_tools)
        plans, plans_truncated = _limited_subjects(db, user, project.id, "project_plan", allowed_tools)
        changes, changes_truncated = _limited_subjects(db, user, project.id, "plan_change", allowed_tools)
        if quotation_truncated:
            limitations.append("客户报价版本最多返回最新20条。")
        if quote_truncated:
            limitations.append("报价/承接记录最多返回最新20条。")
        if contracts_truncated:
            limitations.append("销售合同最多返回最新20条。")
        if outsource_truncated:
            limitations.append("整套委外合同最多返回最新20条。")
        if plans_truncated or changes_truncated:
            limitations.append("项目计划和计划变更最多各返回最新20条。")
        quotation_records = [_quotation_record(row) for row in quotation_rows]
        quote_records = [_quote_record(row) for row in quote_rows]
        plan_tasks, tasks_truncated = _plan_summary(plans + changes)
        if tasks_truncated:
            limitations.append("计划任务摘要最多返回前20条。")
        profile = _project_profile(db, user, project.id)
        analysis = _analysis(project, profile, quotation_records, quote_records, contracts, outsource_contracts, plan_tasks)
        quotation_workflows = []
        if "prepare_quotation_version" in allowed_tools:
            try:
                from domain_packs.mold.tools.erp.commercial.quotation_tools import workflow_options
                quotation_workflows = workflow_options(db, user, project)
            except DomainError as error:
                limitations.append("当前人员不可提交报价版本，未返回可选流程：" + error.message)
        return {
            "resolution": "RESOLVED",
            "data": [
                {
                    "project": _project_card(db, user, project, matched_by),
                    "project_profile": profile,
                    "analysis": analysis,
                    "quotation_workflow_options": quotation_workflows,
                    "sales_contracts": [_contract_summary(row) for row in contracts],
                    "full_outsource_contracts": [_contract_summary(row) for row in outsource_contracts],
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
