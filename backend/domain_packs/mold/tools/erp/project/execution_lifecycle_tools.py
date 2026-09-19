from pydantic import Field, ValidationError, model_validator

from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.tools.erp.project.kickoff_lifecycle_tools import (
    _first_row,
    _project_card,
    _resolve,
)


class ProjectExecutionContextInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    identifier: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="项目编号、项目名称或当前权限内的项目线索。",
    )

    @model_validator(mode="after")
    def one_locator(self):
        if bool(self.project_id) == bool(self.identifier):
            raise ValueError("project_id 和 identifier 须且只能填写一项")
        if self.identifier:
            self.identifier = self.identifier.strip()
            if not self.identifier:
                raise ValueError("项目线索不能为空")
        return self


def parse(arguments):
    try:
        return ProjectExecutionContextInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError(
            "INVALID_TOOL_INPUT",
            "项目执行链路参数无效：" + error.errors()[0]["msg"],
        ) from None


def _analysis(row):
    return row.get("analysis") if isinstance(row, dict) and isinstance(row.get("analysis"), dict) else {}


def _profile(row):
    return row.get("profile") if isinstance(row, dict) and isinstance(row.get("profile"), dict) else {}


def _unavailable_stage(key, name, query_tool, *, conditional=False):
    return {
        "key": key,
        "name": name,
        "state": "UNAVAILABLE",
        "query_tool": query_tool,
        "conditional": conditional,
        "facts": {},
        "blockers": ["当前会话未分配本阶段查询能力，未读取也未推断该阶段业务事实。"],
    }


def _not_applicable_stage(key, name, query_tool, reason, *, conditional=False):
    return {
        "key": key,
        "name": name,
        "state": "NOT_APPLICABLE",
        "query_tool": query_tool,
        "conditional": conditional,
        "facts": {},
        "blockers": [reason],
    }


def _plan_stage(row):
    if row is None:
        return _unavailable_stage("baseline_plan", "基线计划交接", "query_project_plan_context")
    analysis = _analysis(row)
    derived = analysis.get("derived_status") or {}
    active = analysis.get("active_plan")
    pending = [
        item for item in (row.get("project_plans") or [])
        if item.get("status") in {"DRAFT", "SUBMITTED", "RETURNED", "APPLY_BLOCKED"}
    ]
    if derived.get("has_effective_plan") or active:
        state = "ACTIVE"
    elif pending:
        state = "WAITING_APPROVAL"
    else:
        state = "NOT_STARTED"
    blockers = []
    missing = (analysis.get("milestone_coverage") or {}).get("missing") or []
    if state != "ACTIVE":
        blockers.append("未见生效项目基线计划，后续阶段不能据此认定已获得正式计划依据。")
    if missing:
        blockers.append("基线计划缺少部分项目大节点。")
    return {
        "key": "baseline_plan",
        "name": "基线计划交接",
        "state": state,
        "query_tool": "query_project_plan_context",
        "facts": {
            "active_plan": active,
            "pending_count": len(pending),
            "task_count": len(analysis.get("tasks") or []),
            "missing_milestones": missing,
        },
        "blockers": blockers,
    }


def _design_stage(row, execution_mode):
    if execution_mode == "FULL_OUTSOURCE":
        return _not_applicable_stage(
            "design_route",
            "内部设计 / BOM / 路线",
            "query_design_route_context",
            "项目当前可见加工方式为整套委外；供应商设计、资料交接和节点证据应在整套委外协同阶段核对。",
            conditional=True,
        )
    if row is None:
        return _unavailable_stage("design_route", "设计 / BOM / 路线", "query_design_route_context")
    analysis = _analysis(row)
    derived = analysis.get("derived_status") or {}
    latest = analysis.get("latest_effective_design")
    open_rows = analysis.get("open_design_routes") or []
    route_summary = analysis.get("route_summary") or {}
    warnings = list(analysis.get("warnings") or [])
    if latest and (derived.get("has_unlinked_internal_route") or derived.get("has_engineering_contact_impacts")):
        state = "NEEDS_ATTENTION"
    elif latest and open_rows:
        state = "ACTIVE"
    elif latest:
        state = "COMPLETED"
    elif open_rows:
        state = "WAITING_APPROVAL"
    else:
        state = "NOT_STARTED"
    blockers = []
    if not latest:
        blockers.append("未见生效设计版本，不能认定正式 BOM 或加工路线已确认。")
    if derived.get("has_unlinked_internal_route"):
        blockers.append("存在内部加工物料未关联计划任务。")
    if derived.get("has_engineering_contact_impacts"):
        blockers.append("存在工程联络影响项，需结合实施与复验状态处理。")
    return {
        "key": "design_route",
        "name": "设计 / BOM / 路线",
        "state": state,
        "query_tool": "query_design_route_context",
        "facts": {
            "latest_effective_design": latest,
            "open_count": len(open_rows),
            "route_counts": route_summary.get("counts") or {},
            "warning_count": len(warnings),
        },
        "blockers": blockers,
    }


def _procurement_stage(row, design_row):
    if row is None:
        return _unavailable_stage(
            "procurement",
            "采购 / 价格 / 订单",
            "query_procurement_price_context",
            conditional=True,
        )
    analysis = _analysis(row)
    derived = analysis.get("derived_status") or {}
    design_derived = _analysis(design_row).get("derived_status") or {}
    design_visible = design_row is not None
    if design_visible and not design_derived.get("has_purchase_or_outsource_route") and not any(
        derived.get(key) for key in ("has_purchase_request", "has_purchase_order", "has_design_procurement_need")
    ):
        return _not_applicable_stage(
            "procurement",
            "采购 / 价格 / 订单",
            "query_procurement_price_context",
            "当前已读取设计路线中未见采购或局部委外物料，且未见采购执行事实。",
            conditional=True,
        )
    if derived.get("has_order_exception"):
        state = "NEEDS_ATTENTION"
    elif derived.get("has_unpriced_design_need"):
        state = "BLOCKED"
    elif derived.get("has_purchase_order") and derived.get("has_unshipped_order_line"):
        state = "ACTIVE"
    elif derived.get("has_purchase_order"):
        state = "COMPLETED"
    elif derived.get("has_purchase_request"):
        state = "ACTIVE"
    elif derived.get("has_design_procurement_need") and derived.get("has_effective_price"):
        state = "READY"
    elif derived.get("has_design_procurement_need"):
        state = "BLOCKED"
    else:
        state = "NOT_STARTED"
    blockers = []
    if derived.get("has_unpriced_design_need"):
        blockers.append("存在采购或委外设计需求未匹配当前可见有效价格。")
    if derived.get("has_order_exception"):
        blockers.append("存在供应商发货或订单异常，需关联整改、退换货或责任处理。")
    return {
        "key": "procurement",
        "name": "采购 / 价格 / 订单",
        "state": state,
        "query_tool": "query_procurement_price_context",
        "conditional": True,
        "facts": {
            "has_effective_price": bool(derived.get("has_effective_price")),
            "has_design_procurement_need": bool(derived.get("has_design_procurement_need")),
            "has_purchase_request": bool(derived.get("has_purchase_request")),
            "has_purchase_order": bool(derived.get("has_purchase_order")),
            "has_unshipped_order_line": bool(derived.get("has_unshipped_order_line")),
        },
        "blockers": blockers,
    }


def _full_outsource_stage(row, execution_mode):
    if execution_mode == "INTERNAL":
        return _not_applicable_stage(
            "full_outsource",
            "整套委外协同",
            "query_full_outsource_context",
            "项目当前可见加工方式为内部加工，整套委外主线不适用。",
            conditional=True,
        )
    if row is None:
        return _unavailable_stage(
            "full_outsource",
            "整套委外协同",
            "query_full_outsource_context",
            conditional=True,
        )
    analysis = _analysis(row)
    derived = analysis.get("derived_status") or {}
    if not derived.get("has_full_outsource_mode"):
        state = "NOT_APPLICABLE" if execution_mode else "NOT_STARTED"
    elif derived.get("has_supplier_progress_risk") or derived.get("has_open_outsource_issue") or derived.get("has_rejected_receipt"):
        state = "NEEDS_ATTENTION"
    elif not derived.get("has_effective_full_outsource_contract") or not derived.get("has_signed_full_outsource_contract_file"):
        state = "BLOCKED"
    elif derived.get("has_customer_acceptance_or_close_evidence"):
        state = "COMPLETED"
    elif derived.get("has_supplier_progress_report") or derived.get("has_supplier_shipment_or_receipt"):
        state = "ACTIVE"
    else:
        state = "READY"
    blockers = []
    if derived.get("has_full_outsource_mode") and not derived.get("has_effective_full_outsource_contract"):
        blockers.append("整套委外加工方式已知，但未见生效整套委外合同。")
    if derived.get("has_effective_full_outsource_contract") and not derived.get("has_signed_full_outsource_contract_file"):
        blockers.append("未见整套委外合同的人工签署文件或签署依据。")
    if derived.get("has_supplier_progress_risk") or derived.get("has_open_outsource_issue"):
        blockers.append("存在供应商进度风险或未关闭委外问题。")
    return {
        "key": "full_outsource",
        "name": "整套委外协同",
        "state": state,
        "query_tool": "query_full_outsource_context",
        "conditional": True,
        "facts": {
            "execution_mode": derived.get("profile_execution_mode") or execution_mode,
            "has_effective_contract": bool(derived.get("has_effective_full_outsource_contract")),
            "has_signed_contract_file": bool(derived.get("has_signed_full_outsource_contract_file")),
            "has_supplier_progress_policy": bool(derived.get("has_supplier_progress_policy")),
            "has_supplier_progress_report": bool(derived.get("has_supplier_progress_report")),
            "has_supplier_shipment_or_receipt": bool(derived.get("has_supplier_shipment_or_receipt")),
        },
        "blockers": blockers,
    }


def _manufacturing_stage(row, execution_mode):
    if execution_mode == "FULL_OUTSOURCE":
        return _not_applicable_stage(
            "manufacturing_quality",
            "内部制造 / 质检",
            "query_manufacturing_quality_context",
            "项目当前可见加工方式为整套委外；供应商制造和质检应在整套委外协同阶段核对。",
            conditional=True,
        )
    if row is None:
        return _unavailable_stage("manufacturing_quality", "内部制造 / 质检", "query_manufacturing_quality_context")
    analysis = _analysis(row)
    derived = analysis.get("derived_status") or {}
    process = analysis.get("process_tasks") or []
    started = analysis.get("started_process_tasks") or []
    done = analysis.get("done_process_tasks") or []
    if not derived.get("has_effective_plan"):
        state = "BLOCKED"
    elif derived.get("has_open_quality_or_rework_contact") or derived.get("has_internal_route_without_task"):
        state = "NEEDS_ATTENTION"
    elif process and len(done) == len(process):
        state = "COMPLETED"
    elif started:
        state = "ACTIVE"
    elif process:
        state = "READY"
    else:
        state = "NOT_STARTED"
    blockers = []
    if not derived.get("has_effective_plan"):
        blockers.append("未见生效项目计划，不能判断工序任务安排。")
    if derived.get("has_internal_route_without_task"):
        blockers.append("存在内部加工路线物料未关联计划任务。")
    if derived.get("has_open_quality_or_rework_contact"):
        blockers.append("存在未关闭质量或返工事项。")
    if process and not derived.get("has_independent_quality_report"):
        blockers.append("未见独立工序检测报告；任务完成不能替代检验合格。")
    return {
        "key": "manufacturing_quality",
        "name": "内部制造 / 质检",
        "state": state,
        "query_tool": "query_manufacturing_quality_context",
        "conditional": True,
        "facts": {
            "process_task_count": len(process),
            "started_count": len(started),
            "done_count": len(done),
            "has_independent_quality_report": bool(derived.get("has_independent_quality_report")),
        },
        "blockers": blockers,
    }


def _assembly_stage(row, execution_mode):
    if execution_mode == "FULL_OUTSOURCE":
        return _not_applicable_stage(
            "assembly_trial",
            "内部装配 / 试模",
            "query_assembly_trial_context",
            "项目当前可见加工方式为整套委外；供应商装配和试模应在整套委外协同阶段核对。",
            conditional=True,
        )
    if row is None:
        return _unavailable_stage("assembly_trial", "内部装配 / 试模", "query_assembly_trial_context")
    analysis = _analysis(row)
    derived = analysis.get("derived_status") or {}
    if not derived.get("has_effective_plan") or not derived.get("has_design_route"):
        state = "BLOCKED"
    elif derived.get("has_trial_failed") or derived.get("has_open_assembly_or_trial_issue"):
        state = "NEEDS_ATTENTION"
    elif derived.get("has_assembly_done") and derived.get("has_trial_passed"):
        state = "COMPLETED"
    elif derived.get("has_assembly_started") or derived.get("has_trial_request"):
        state = "ACTIVE"
    elif derived.get("has_assembly_plan_node") or derived.get("has_trial_plan_node"):
        state = "READY"
    else:
        state = "NOT_STARTED"
    blockers = []
    if not derived.get("has_effective_plan"):
        blockers.append("未见生效计划，无法核对装配与试模前置。")
    if not derived.get("has_design_route"):
        blockers.append("未见设计 BOM 与路线，不能判断装配齐套。")
    if derived.get("has_trial_failed"):
        blockers.append("存在试模未通过结果，需完成整改和重新验证。")
    if derived.get("has_open_assembly_or_trial_issue"):
        blockers.append("存在未关闭装配、试模或质量联络事项。")
    return {
        "key": "assembly_trial",
        "name": "内部装配 / 试模",
        "state": state,
        "query_tool": "query_assembly_trial_context",
        "conditional": True,
        "facts": {
            "has_assembly_plan_node": bool(derived.get("has_assembly_plan_node")),
            "has_assembly_order": bool(derived.get("has_assembly_order")),
            "has_assembly_done": bool(derived.get("has_assembly_done")),
            "has_trial_request": bool(derived.get("has_trial_request")),
            "has_trial_result": bool(derived.get("has_trial_result")),
            "has_trial_passed": bool(derived.get("has_trial_passed")),
        },
        "blockers": blockers,
    }


def _delivery_stage(row):
    if row is None:
        return _unavailable_stage("delivery_acceptance", "交付 / 签收 / 验收", "query_delivery_logistics_context")
    analysis = _analysis(row)
    derived = analysis.get("derived_status") or {}
    if (
        derived.get("has_rejected_receipt")
        or derived.get("has_trial_failed")
        or derived.get("has_open_delivery_or_quality_issue")
        or (derived.get("has_failed_customer_acceptance") and not derived.get("has_customer_recheck_passed"))
    ):
        state = "NEEDS_ATTENTION"
    elif derived.get("has_customer_signature") and derived.get("has_customer_acceptance"):
        state = "COMPLETED"
    elif derived.get("has_customer_signature") or derived.get("has_stock_out_movement") or derived.get("has_supplier_shipment"):
        state = "ACTIVE"
    elif derived.get("has_trial_passed"):
        state = "READY"
    elif not derived.get("has_effective_plan"):
        state = "BLOCKED"
    else:
        state = "NOT_STARTED"
    blockers = []
    if derived.get("has_rejected_receipt"):
        blockers.append("存在收货检验不合格数量。")
    if derived.get("has_trial_failed"):
        blockers.append("存在试模未通过结果，不能认定具备交付验收结论。")
    if derived.get("has_open_delivery_or_quality_issue"):
        blockers.append("存在未关闭质量、交付、物流或验收问题。")
    if derived.get("has_failed_customer_acceptance") and not derived.get("has_customer_recheck_passed"):
        blockers.append("客户验收未通过且未见复验通过。")
    return {
        "key": "delivery_acceptance",
        "name": "交付 / 签收 / 验收",
        "state": state,
        "query_tool": "query_delivery_logistics_context",
        "facts": {
            "has_delivery_plan_node": bool(derived.get("has_delivery_plan_node")),
            "has_stock_out_movement": bool(derived.get("has_stock_out_movement")),
            "has_customer_signature": bool(derived.get("has_customer_signature")),
            "has_customer_acceptance": bool(derived.get("has_customer_acceptance")),
            "has_structured_logistics_price": bool(derived.get("has_structured_logistics_price")),
        },
        "blockers": blockers,
    }


def _execution_mode(contexts):
    values = {
        _profile(row).get("execution_mode")
        for row in contexts.values()
        if row is not None and _profile(row).get("execution_mode")
    }
    if not values:
        return None, False
    if len(values) == 1:
        return next(iter(values)), False
    return "CONFLICT", True


def _current_focus(stages):
    applicable = [stage for stage in stages if stage["state"] not in {"UNAVAILABLE", "NOT_APPLICABLE"}]
    for stage in applicable:
        state = stage["state"]
        if state != "COMPLETED" and not (stage["key"] == "baseline_plan" and state == "ACTIVE"):
            return {"key": stage["key"], "name": stage["name"], "state": state}
    if applicable and all(stage["state"] in {"ACTIVE", "COMPLETED"} for stage in applicable):
        unfinished = [stage for stage in applicable if stage["state"] != "COMPLETED" and stage["key"] != "baseline_plan"]
        if unfinished:
            stage = unfinished[0]
            return {"key": stage["key"], "name": stage["name"], "state": stage["state"]}
        return {"key": "completed", "name": "项目执行链路", "state": "COMPLETED"}
    return {"key": "visibility", "name": "执行链路可见性", "state": "UNAVAILABLE"}


def _phase(focus):
    return {
        "baseline_plan": "PLAN_HANDOFF",
        "design_route": "DESIGN_ENGINEERING",
        "procurement": "PROCUREMENT",
        "full_outsource": "FULL_OUTSOURCE",
        "manufacturing_quality": "MANUFACTURING_QUALITY",
        "assembly_trial": "ASSEMBLY_TRIAL",
        "delivery_acceptance": "DELIVERY_ACCEPTANCE",
        "completed": "EXECUTION_COMPLETED",
        "visibility": "EXECUTION_VISIBILITY_GAP",
    }.get(focus["key"], "EXECUTION")


def _recommendations(focus, stages, allowed_tools):
    if focus["key"] in {"completed", "visibility"}:
        return []
    stage = next((item for item in stages if item["key"] == focus["key"]), None)
    tool = stage.get("query_tool") if stage else None
    if not tool or tool not in allowed_tools:
        return []
    return [{
        "kind": "PRIMARY",
        "stage": stage["key"],
        "tool": tool,
        "reason": "展开当前执行焦点的完整事实、缺口和来源后，再决定是否进入对应 ERP 或人工审批。",
        "requires_user_confirmation": False,
    }]


def query(db, user, data: ProjectExecutionContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data)
    limitations = [
        "只读取当前用户具备项目读取权限的项目；每个执行阶段还必须同时具备对应查询工具与业务权限。",
        "本工具只生成项目执行链路投影和当前焦点，不创建计划、设计、采购、制造、装配、试模、物流或验收记录。",
        "阶段状态来自各自正式业务事实；后续阶段已有事实时仍会展示，不以简单串行规则覆盖真实并行执行。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project is None:
        if alternatives is None:
            resolution, rows = "NOT_FOUND_OR_FORBIDDEN", []
        elif alternatives:
            resolution, rows = "MULTIPLE_CANDIDATES", alternatives
            limitations.append("线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。")
        else:
            resolution, rows = "NOT_FOUND", []
        return {
            "resolution": resolution,
            "data": rows,
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations,
        }

    project_id = project.id
    contexts = {}
    access_gaps = []

    if "query_project_plan_context" in allowed_tools:
        from domain_packs.mold.erp.core.contracts import ProjectPlanContextInput
        from domain_packs.mold.tools.erp.project.plan_tools import query as plan_query

        contexts["plan"] = _first_row(plan_query(
            db, user, ProjectPlanContextInput(project_id=project_id), allowed_tools
        ))
    else:
        access_gaps.append("基线计划")

    if "query_design_route_context" in allowed_tools:
        from domain_packs.mold.tools.erp.design.design_tools import DesignRouteContextInput, query as design_query

        contexts["design"] = _first_row(design_query(
            db, user, DesignRouteContextInput(project_id=project_id), allowed_tools
        ))
    else:
        access_gaps.append("设计/BOM/路线")

    if "query_procurement_price_context" in allowed_tools:
        from domain_packs.mold.tools.erp.procurement.procurement_tools import ProcurementPriceContextInput, query as procurement_query

        contexts["procurement"] = _first_row(procurement_query(
            db, user, ProcurementPriceContextInput(project_id=project_id), allowed_tools
        ))
    else:
        access_gaps.append("采购/价格/订单")

    if "query_full_outsource_context" in allowed_tools:
        from domain_packs.mold.erp.core.contracts import ProjectPlanContextInput
        from domain_packs.mold.tools.erp.procurement.full_outsource_tools import query as outsource_query

        contexts["full_outsource"] = _first_row(outsource_query(
            db, user, ProjectPlanContextInput(project_id=project_id), allowed_tools
        ))
    else:
        access_gaps.append("整套委外")

    if "query_manufacturing_quality_context" in allowed_tools:
        from domain_packs.mold.erp.core.contracts import ProjectPlanContextInput
        from domain_packs.mold.tools.erp.manufacturing.manufacturing_quality_tools import query as manufacturing_query

        contexts["manufacturing"] = _first_row(manufacturing_query(
            db, user, ProjectPlanContextInput(project_id=project_id), allowed_tools
        ))
    else:
        access_gaps.append("制造/质检")

    if "query_assembly_trial_context" in allowed_tools:
        from domain_packs.mold.erp.core.contracts import ProjectPlanContextInput
        from domain_packs.mold.tools.erp.manufacturing.assembly_trial_tools import query as assembly_query

        contexts["assembly"] = _first_row(assembly_query(
            db, user, ProjectPlanContextInput(project_id=project_id), allowed_tools
        ))
    else:
        access_gaps.append("装配/试模")

    if "query_delivery_logistics_context" in allowed_tools:
        from domain_packs.mold.erp.core.contracts import ProjectPlanContextInput
        from domain_packs.mold.erp.procurement.delivery_logistics import query as delivery_query

        contexts["delivery"] = _first_row(delivery_query(
            db, user, ProjectPlanContextInput(project_id=project_id), allowed_tools
        ))
    else:
        access_gaps.append("交付/签收/验收")

    execution_mode, mode_conflict = _execution_mode(contexts)
    stages = [
        _plan_stage(contexts.get("plan")),
        _design_stage(contexts.get("design"), execution_mode),
        _procurement_stage(contexts.get("procurement"), contexts.get("design")),
        _full_outsource_stage(contexts.get("full_outsource"), execution_mode),
        _manufacturing_stage(contexts.get("manufacturing"), execution_mode),
        _assembly_stage(contexts.get("assembly"), execution_mode),
        _delivery_stage(contexts.get("delivery")),
    ]
    focus = _current_focus(stages)
    lifecycle = {
        "kind": "project_execution_lifecycle_v1",
        "phase": _phase(focus),
        "execution_mode": execution_mode,
        "execution_mode_conflict": mode_conflict,
        "current_focus": focus,
        "stages": stages,
        "recommended_next_steps": _recommendations(focus, stages, allowed_tools),
        "access_gaps": access_gaps,
        "guardrails": [
            "阶段能力缺失时显示 UNAVAILABLE，不根据相邻阶段、历史对话或自然语言推断结果。",
            "NOT_APPLICABLE 只在已读取加工方式或设计路线足以证明分支不适用时使用。",
            "协调器只读；涉及写入、审批、ERP 执行或人工确认时必须进入对应业务能力并取得正式回执。",
        ],
    }
    if mode_conflict:
        limitations.append("不同已授权阶段返回的加工方式不一致，已标记资料冲突；未据此裁剪执行分支。")
    if access_gaps:
        limitations.append("未读取以下未分配阶段能力：" + "、".join(access_gaps) + "。")
    return {
        "resolution": "RESOLVED",
        "data": [{
            "project": _project_card(db, user, project, alternatives or ("项目定位",)),
            "analysis": {"execution_lifecycle": lifecycle},
        }],
        "source": "agent_db",
        "as_of": now().isoformat(),
        "limitations": limitations,
    }
