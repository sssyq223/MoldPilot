from domain_packs.mold.erp.core.project_locator import ProjectId
from pydantic import Field, ValidationError, model_validator

from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.tools.erp.project.kickoff_lifecycle_tools import (
    _first_row,
    _project_card,
    _resolve,
)


COMPLETION_PROPOSAL_TOOLS = frozenset(
    {
        "prepare_customer_delivery_signature",
        "prepare_customer_acceptance",
        "prepare_customer_receivable_schedule",
        "prepare_customer_receipt_confirmation",
        "prepare_supplier_payment_confirmation",
        "prepare_supplier_deduction_settlement",
        "prepare_mold_transfer_receipt",
        "prepare_project_closure_checklist",
        "prepare_project_termination",
        "prepare_project_closure_item",
        "prepare_project_normal_close",
        "prepare_project_settlement_close",
    }
)


class ProjectCompletionContextInput(StrictModel):
    project_id: ProjectId | None = Field(default=None)
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
        return ProjectCompletionContextInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError(
            "INVALID_TOOL_INPUT",
            "项目收尾链路参数无效：" + error.errors()[0]["msg"],
        ) from None


def _analysis(row):
    return row.get("analysis") if isinstance(row, dict) and isinstance(row.get("analysis"), dict) else {}


def _derived(row):
    analysis = _analysis(row)
    return analysis.get("derived_status") if isinstance(analysis.get("derived_status"), dict) else {}


def _closure_case(row):
    case = row.get("closure_case") if isinstance(row, dict) else None
    return case if isinstance(case, dict) else None


def _closure_items(case):
    return {
        item.get("item_key"): item
        for item in (case or {}).get("items") or []
        if isinstance(item, dict) and item.get("item_key")
    }


def _item_status(items, key):
    item = items.get(key)
    return item.get("status") if isinstance(item, dict) else None


def _done(status):
    return status in {"DONE", "NOT_APPLICABLE"}


def _unavailable_stage(key, name, query_tool):
    return {
        "key": key,
        "name": name,
        "state": "UNAVAILABLE",
        "query_tool": query_tool,
        "action_tool": None,
        "facts": {},
        "blockers": ["当前会话未分配本阶段查询能力，未读取也未推断该阶段业务事实。"],
    }


def _delivery_stage(row, closure_row, mode):
    if row is None:
        return _unavailable_stage(
            "delivery_acceptance", "交付 / 签收 / 客户验收", "query_delivery_logistics_context"
        )
    derived = _derived(row)
    delivery_handoffs = (
        _analysis(row).get("delivery_handoffs")
        if isinstance(_analysis(row).get("delivery_handoffs"), list)
        else []
    )
    items = _closure_items(_closure_case(closure_row))
    if mode == "TERMINATION":
        delivery_status = _item_status(items, "DELIVERY_DISPOSITION")
        acceptance_status = _item_status(items, "ACCEPTANCE_DISPOSITION")
    else:
        delivery_status = _item_status(items, "DELIVERY")
        acceptance_status = _item_status(items, "CUSTOMER_ACCEPTANCE")
    blockers = []
    unresolved_acceptance = derived.get("has_unresolved_customer_acceptance_failure")
    if unresolved_acceptance is None:
        unresolved_acceptance = (
            derived.get("has_failed_customer_acceptance")
            and not derived.get("has_customer_recheck_passed")
        )
    rejected = bool(
        derived.get("has_rejected_receipt")
        or derived.get("has_trial_failed")
        or derived.get("has_outbound_release_failure")
        or derived.get("has_open_delivery_or_quality_issue")
        or unresolved_acceptance
    )
    if delivery_status == "NOT_APPLICABLE" and acceptance_status == "NOT_APPLICABLE":
        state = "NOT_APPLICABLE"
    elif rejected:
        state = "NEEDS_ATTENTION"
    elif delivery_status and acceptance_status and _done(delivery_status) and _done(acceptance_status):
        state = "COMPLETED"
    elif (
        derived.get("has_trial_passed")
        and derived.get("has_outbound_release_evidence") is not None
        and not derived.get("has_outbound_self_inspection_passed")
    ):
        state = "BLOCKED"
    elif derived.get("has_customer_signature") and derived.get("has_customer_acceptance"):
        state = "COMPLETED"
    elif (
        derived.get("has_customer_signature")
        or derived.get("has_stock_out_movement")
        or derived.get("has_supplier_shipment")
        or delivery_status == "DONE"
        or acceptance_status == "DONE"
    ):
        state = "ACTIVE"
    elif mode == "TERMINATION" and (delivery_status == "PENDING" or acceptance_status == "PENDING"):
        state = "BLOCKED"
    else:
        state = "NOT_STARTED"
    if derived.get("has_open_delivery_or_quality_issue"):
        blockers.append("仍有未关闭的质量、交付、物流或验收问题。")
    if unresolved_acceptance:
        blockers.append("当前客户验收链仍有未解决的失败结果，不能以历史通过或结项清单完成代替复验通过。")
    if (
        derived.get("has_trial_passed")
        and derived.get("has_outbound_release_evidence") is not None
        and not derived.get("has_outbound_self_inspection_passed")
    ):
        blockers.append("试模已通过但未见出厂自检/放行合格依据，不能推进出库发运。")
    if derived.get("has_outbound_release_failure"):
        blockers.append("最新出厂自检/放行未通过，需完成整改复验。")
    if mode == "TERMINATION" and state == "BLOCKED":
        blockers.append("终止清单中的交付和验收处置尚未完成或说明不适用。")
    return {
        "key": "delivery_acceptance",
        "name": "交付 / 签收 / 客户验收" if mode != "TERMINATION" else "交付与验收处置",
        "state": state,
        "query_tool": "query_delivery_logistics_context",
        "action_tool": None,
        "conditional": mode == "TERMINATION",
        "facts": {
            "has_customer_signature": bool(derived.get("has_customer_signature")),
            "has_customer_acceptance": bool(derived.get("has_customer_acceptance")),
            "has_customer_recheck_passed": bool(derived.get("has_customer_recheck_passed")),
            "has_outbound_release_evidence": derived.get("has_outbound_release_evidence"),
            "has_outbound_self_inspection_passed": derived.get("has_outbound_self_inspection_passed"),
            "has_outbound_release_failure": derived.get("has_outbound_release_failure"),
            "has_unresolved_customer_acceptance_failure": bool(unresolved_acceptance),
            "delivery_item_status": delivery_status,
            "acceptance_item_status": acceptance_status,
            "handoff_states": {
                item.get("key"): item.get("state")
                for item in delivery_handoffs
                if isinstance(item, dict) and item.get("key")
            },
        },
        "handoffs": delivery_handoffs,
        "blockers": blockers,
    }


def _customer_finance_stage(row, closure_row, mode):
    if row is None:
        return _unavailable_stage("customer_finance", "发票 / 客户回款 / 结算", "query_finance_context")
    derived = _derived(row)
    items = _closure_items(_closure_case(closure_row))
    if mode == "TERMINATION":
        required = ("CUSTOMER_SETTLEMENT", "RECEIVABLE_PAYABLE")
    else:
        required = ("INVOICE", "CUSTOMER_RECEIPT")
    statuses = {key: _item_status(items, key) for key in required}
    blockers = []
    if statuses and all(status is not None and _done(status) for status in statuses.values()):
        state = "COMPLETED"
    elif derived.get("has_finance_correction") or derived.get("has_cost_or_deduction_signal"):
        state = "NEEDS_ATTENTION"
    elif (
        derived.get("has_customer_actual_receipt_ledger")
        or derived.get("has_erp_fulfillment_record")
        or derived.get("has_erp_customer_payment_plan")
        or derived.get("has_erp_customer_payment_record")
        or any(status == "DONE" for status in statuses.values())
    ):
        state = "ACTIVE"
    elif any(status == "PENDING" for status in statuses.values()):
        state = "BLOCKED"
    else:
        state = "NOT_STARTED"
    labels = {
        "INVOICE": "发票核对",
        "CUSTOMER_RECEIPT": "客户回款核对",
        "CUSTOMER_SETTLEMENT": "客户终止结算",
        "RECEIVABLE_PAYABLE": "终止收付款核对",
    }
    for key, status in statuses.items():
        if status is not None and not _done(status):
            blockers.append(labels[key] + "尚未完成。")
    if derived.get("has_finance_correction"):
        blockers.append("存在财务冲正或更正记录，须按有符号金额和原始依据复核。")
    if derived.get("has_cost_or_deduction_signal"):
        blockers.append("存在设变、验收影响或扣款记录；须结合各自责任、结算与收付款依据核对，记录存在不等于尚未结算。")
    return {
        "key": "customer_finance",
        "name": "客户终止结算 / 收付款" if mode == "TERMINATION" else "发票 / 客户回款",
        "state": state,
        "query_tool": "query_finance_context",
        "action_tool": None,
        "facts": {
            "has_customer_actual_receipt_ledger": bool(derived.get("has_customer_actual_receipt_ledger")),
            "has_erp_fulfillment_record": bool(derived.get("has_erp_fulfillment_record")),
            "has_erp_customer_payment_plan": bool(derived.get("has_erp_customer_payment_plan")),
            "has_erp_customer_payment_record": bool(derived.get("has_erp_customer_payment_record")),
            "has_finance_correction": bool(derived.get("has_finance_correction")),
            "has_cost_or_deduction_signal": bool(derived.get("has_cost_or_deduction_signal")),
            "has_customer_acceptance_financial_impact": derived.get("has_customer_acceptance_financial_impact"),
            "has_supplier_deduction_record": derived.get("has_supplier_deduction_record"),
            "has_unsettled_supplier_deduction": derived.get("has_unsettled_supplier_deduction"),
            **{key.lower() + "_status": value for key, value in statuses.items()},
        },
        "blockers": blockers,
    }


def _supplier_settlement_stage(row, closure_row):
    if row is None:
        return _unavailable_stage("supplier_settlement", "供应商付款 / 结算", "query_finance_context")
    derived = _derived(row)
    items = _closure_items(_closure_case(closure_row))
    status = _item_status(items, "SUPPLIER_SETTLEMENT")
    blockers = []
    if status is not None and _done(status):
        state = "COMPLETED"
    elif derived.get("has_open_supplier_payment_reservation"):
        state = "NEEDS_ATTENTION"
        blockers.append("仍有未释放的供应商付款授权占用。")
    elif (
        derived.get("has_confirmed_supplier_payment")
        or derived.get("has_supplier_payment_request")
        or derived.get("has_erp_supplier_payment_plan")
        or derived.get("has_erp_supplier_payment_record")
    ):
        state = "ACTIVE"
    elif status == "PENDING":
        state = "BLOCKED"
        blockers.append("供应商结算清单尚未完成或说明不适用。")
    else:
        state = "NOT_STARTED"
    return {
        "key": "supplier_settlement",
        "name": "供应商付款 / 结算",
        "state": state,
        "query_tool": "query_finance_context",
        "action_tool": None,
        "facts": {
            "has_supplier_payment_request": bool(derived.get("has_supplier_payment_request")),
            "has_confirmed_supplier_payment": bool(derived.get("has_confirmed_supplier_payment")),
            "has_erp_supplier_payment_plan": bool(derived.get("has_erp_supplier_payment_plan")),
            "has_erp_supplier_payment_record": bool(derived.get("has_erp_supplier_payment_record")),
            "has_open_supplier_payment_reservation": bool(derived.get("has_open_supplier_payment_reservation")),
            "supplier_settlement_item_status": status,
        },
        "blockers": blockers,
    }


def _issue_stage(closure_row):
    if closure_row is None:
        return _unavailable_stage("issue_resolution", "异常与工程联络关闭", "query_project_closure_context")
    case = _closure_case(closure_row)
    items = _closure_items(case)
    status = _item_status(items, "OPEN_ISSUES")
    facts = closure_row.get("system_facts") if isinstance(closure_row.get("system_facts"), dict) else {}
    open_count = int(facts.get("open_contact_cases") or 0)
    if open_count:
        state = "NEEDS_ATTENTION"
    elif status == "DONE" or (case is None and open_count == 0):
        state = "COMPLETED"
    elif status == "PENDING":
        state = "BLOCKED"
    else:
        state = "NOT_STARTED"
    blockers = [f"仍有 {open_count} 项线上工程联络事项未关闭。"] if open_count else []
    return {
        "key": "issue_resolution",
        "name": "异常与工程联络关闭",
        "state": state,
        "query_tool": "query_project_closure_context",
        "action_tool": None,
        "facts": {"open_contact_cases": open_count, "open_issues_item_status": status},
        "blockers": blockers,
    }


def _archive_stage(closure_row, mode):
    if closure_row is None:
        return _unavailable_stage("archive", "全过程资料归档", "query_project_closure_context")
    case = _closure_case(closure_row)
    items = _closure_items(case)
    keys = (
        ("ARCHIVE",)
        if mode == "TERMINATION"
        else (
            "ARCHIVE_PROCESS",
            "ARCHIVE_DESIGN",
            "ARCHIVE_PROCUREMENT",
            "ARCHIVE_QUALITY_DELIVERY",
            "ARCHIVE_CHANGE",
            "ARCHIVE_FINANCE",
        )
    )
    statuses = {key: _item_status(items, key) for key in keys}
    completed = [key for key, status in statuses.items() if status is not None and _done(status)]
    pending = [key for key, status in statuses.items() if status is not None and not _done(status)]
    if not case:
        state = "NOT_STARTED"
    elif len(completed) == len(keys):
        state = "COMPLETED"
    elif completed:
        state = "ACTIVE"
    else:
        state = "BLOCKED"
    blockers = ["仍有未完成的项目、设计、采购、质量交付、设变或财务归档事项。"] if pending else []
    return {
        "key": "archive",
        "name": "全过程资料归档",
        "state": state,
        "query_tool": "query_project_closure_context",
        "action_tool": None,
        "facts": {
            "archive_required_count": len(keys),
            "archive_completed_count": len(completed),
            "archive_pending_keys": pending,
        },
        "blockers": blockers,
    }


def _final_close_stage(project, closure_row, mode, allowed_tools):
    if closure_row is None:
        return _unavailable_stage("final_close", "项目最终关闭", "query_project_closure_context")
    case = _closure_case(closure_row)
    project_status = closure_row.get("project_status") or project.status
    blockers = list((case or {}).get("blockers") or [])
    if project_status == "CLOSED":
        state, action_tool = "COMPLETED", None
    elif case and case.get("status") == "CLOSED":
        state, action_tool = "DATA_CONFLICT", None
        blockers.append("结项清单已关闭但项目状态不是已关闭，需要核对业务应用回执。")
    elif case and case.get("status") == "OPEN" and not blockers:
        state = "READY"
        action_tool = "prepare_project_settlement_close" if mode == "TERMINATION" else "prepare_project_normal_close"
        if action_tool not in allowed_tools:
            action_tool = None
    elif case and case.get("status") == "OPEN":
        state, action_tool = "BLOCKED", None
    elif mode == "NORMAL" and project_status == "ACTIVE":
        state = "NOT_STARTED"
        action_tool = (
            "prepare_project_closure_checklist"
            if "prepare_project_closure_checklist" in allowed_tools
            else None
        )
        blockers.append("尚未建立正常结项清单；准备清单只是核对起点，不代表项目已经可以关闭。")
    else:
        state, action_tool = "BLOCKED", None
        blockers.append("终止项目未见可核对的终止结算清单。")
    return {
        "key": "final_close",
        "name": "终止结算关闭" if mode == "TERMINATION" else "正常结项清单 / 最终关闭",
        "state": state,
        "query_tool": "query_project_closure_context",
        "action_tool": action_tool,
        "facts": {
            "project_status": project_status,
            "closure_mode": mode,
            "closure_case_status": (case or {}).get("status"),
            "closure_case_version": (case or {}).get("version"),
            "closure_blocker_count": len(blockers),
        },
        "blockers": blockers,
    }


def _current_focus(stages):
    applicable = [stage for stage in stages if stage["state"] not in {"UNAVAILABLE", "NOT_APPLICABLE"}]
    for stage in applicable:
        if stage["state"] != "COMPLETED":
            return {"key": stage["key"], "name": stage["name"], "state": stage["state"]}
    if applicable and all(stage["state"] == "COMPLETED" for stage in applicable):
        return {"key": "completed", "name": "项目收尾链路", "state": "COMPLETED"}
    return {"key": "visibility", "name": "收尾链路可见性", "state": "UNAVAILABLE"}


def _phase(focus):
    return {
        "delivery_acceptance": "DELIVERY_ACCEPTANCE",
        "customer_finance": "CUSTOMER_SETTLEMENT",
        "supplier_settlement": "SUPPLIER_SETTLEMENT",
        "issue_resolution": "ISSUE_RESOLUTION",
        "archive": "ARCHIVE_READINESS",
        "final_close": "FINAL_CLOSE",
        "completed": "PROJECT_CLOSED",
        "visibility": "COMPLETION_VISIBILITY_GAP",
    }.get(focus["key"], "PROJECT_COMPLETION")


def _recommendations(focus, stages, allowed_tools):
    if focus["key"] in {"completed", "visibility"}:
        return []
    stage = next((item for item in stages if item["key"] == focus["key"]), None)
    if not stage:
        return []
    tool = stage.get("action_tool") or stage.get("query_tool")
    if not tool or tool not in allowed_tools:
        return []
    return [{
        "kind": "PRIMARY",
        "stage": stage["key"],
        "tool": tool,
        "reason": "先核对当前收尾焦点的完整事实、来源和适用条件；正式登记、审批或关闭仍须本人确认。",
        "requires_user_confirmation": tool.startswith("prepare_"),
    }]


def _completion_handoffs(stages):
    """Show which completion responsibilities have evidence of handoff.

    Completion stages are intentionally parallel in the domain model.  This
    projection therefore only describes evidence boundaries; it never turns a
    completed delivery or payment into a close decision by itself.
    """
    by_key = {stage["key"]: stage for stage in stages}

    def row(key, source, target, state, reason, next_tool):
        return {
            "key": key,
            "from": source,
            "to": target,
            "state": state,
            "reason": reason,
            "next_query_tool": next_tool,
        }

    def unavailable(*keys):
        return any(by_key[key]["state"] == "UNAVAILABLE" for key in keys)

    delivery = by_key["delivery_acceptance"]
    customer = by_key["customer_finance"]
    supplier = by_key["supplier_settlement"]
    issues = by_key["issue_resolution"]
    archive = by_key["archive"]
    final_close = by_key["final_close"]
    result = []

    if unavailable("delivery_acceptance", "customer_finance"):
        result.append(row(
            "delivery_to_customer_finance",
            "delivery_acceptance",
            "customer_finance",
            "UNAVAILABLE",
            "交付或财务能力不可见，不能判断客户验收结果是否已交给回款/结算核对。",
            "query_finance_context",
        ))
    elif delivery["state"] == "NOT_APPLICABLE":
        result.append(row(
            "delivery_to_customer_finance",
            "delivery_acceptance",
            "customer_finance",
            "NOT_APPLICABLE",
            "当前收尾分支已明确交付/验收不适用，仍需独立完成结算依据。",
            "query_finance_context",
        ))
    elif delivery["state"] == "COMPLETED" and customer["state"] == "COMPLETED":
        result.append(row(
            "delivery_to_customer_finance",
            "delivery_acceptance",
            "customer_finance",
            "CONNECTED",
            "交付/验收与客户发票回款或终止收付款均已有完成事实。",
            "query_finance_context",
        ))
    elif delivery["state"] == "COMPLETED":
        result.append(row(
            "delivery_to_customer_finance",
            "delivery_acceptance",
            "customer_finance",
            "READY",
            "交付/验收已完成，但客户财务核对尚未完成。",
            "query_finance_context",
        ))
    else:
        result.append(row(
            "delivery_to_customer_finance",
            "delivery_acceptance",
            "customer_finance",
            "BLOCKED",
            "交付/验收尚未完成，不能把发货或签收直接当作财务结算依据。",
            "query_delivery_logistics_context",
        ))

    if unavailable("customer_finance", "supplier_settlement"):
        settlement_state = "UNAVAILABLE"
        settlement_reason = "财务或供应商结算能力不可见，不能判断两类收付款是否都已核对。"
    elif customer["state"] in {"NEEDS_ATTENTION", "BLOCKED"} or supplier["state"] in {"NEEDS_ATTENTION", "BLOCKED"}:
        settlement_state = "BLOCKED"
        settlement_reason = "客户财务或供应商结算存在未完成、冲正、扣款或付款占用问题。"
    elif customer["state"] == "COMPLETED" and supplier["state"] in {"COMPLETED", "NOT_STARTED", "NOT_APPLICABLE"}:
        settlement_state = "READY"
        settlement_reason = "客户财务已完成；供应商结算需确认不适用或继续完成后才能进入归档。"
    else:
        settlement_state = "WAITING"
        settlement_reason = "客户与供应商两条结算责任线尚未同时形成可归档依据。"
    result.append(row(
        "finance_to_settlement_archive",
        "customer_finance",
        "archive",
        settlement_state,
        settlement_reason,
        "query_finance_context",
    ))

    required = (delivery, customer, supplier, issues)
    if unavailable("archive", "final_close"):
        archive_state = "UNAVAILABLE"
        archive_reason = "归档或最终关闭能力不可见，不能判断收尾资料是否已接入关闭。"
    elif all(stage["state"] in {"COMPLETED", "NOT_APPLICABLE"} for stage in required) and archive["state"] == "COMPLETED":
        archive_state = "CONNECTED"
        archive_reason = "交付、财务、供应商结算、异常及归档均已形成完成事实，具备进入最终关闭核对的证据链。"
    elif all(stage["state"] in {"COMPLETED", "NOT_APPLICABLE"} for stage in required):
        archive_state = "READY"
        archive_reason = "业务收尾阶段已完成，但归档尚未完成。"
    else:
        archive_state = "BLOCKED"
        archive_reason = "仍有交付、财务、供应商结算或异常事项未完成，不能把局部收尾结果当作归档完成。"
    result.append(row(
        "completion_to_archive",
        "completion_stages",
        "archive",
        archive_state,
        archive_reason,
        "query_project_closure_context",
    ))

    if final_close["state"] == "COMPLETED" and archive["state"] == "COMPLETED":
        close_state = "CONNECTED"
        close_reason = "归档完成且项目最终关闭事实已存在。"
    elif archive["state"] == "COMPLETED" and final_close["state"] == "READY":
        close_state = "READY"
        close_reason = "归档已完成，最终关闭仍需对应关闭准备和正式回执。"
    elif final_close["state"] == "UNAVAILABLE":
        close_state = "UNAVAILABLE"
        close_reason = "最终关闭能力不可见，不能判断项目是否已关闭。"
    else:
        close_state = "BLOCKED"
        close_reason = "归档或最终关闭仍有阻塞，不能声称项目已完成。"
    result.append(row(
        "archive_to_final_close",
        "archive",
        "final_close",
        close_state,
        close_reason,
        "query_project_closure_context",
    ))
    return result


def query(db, user, data: ProjectCompletionContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data)
    limitations = [
        "只读取当前用户具备项目读取权限的项目；交付、财务和结项阶段仍分别校验对应能力与数据权限。",
        "本工具只生成项目收尾链路投影，不登记签收验收、不确认收付款、不更新结项清单、不关闭项目。",
        "交付、客户签收、客户验收、发票、客户回款、供应商付款、结项事项、归档和最终关闭是独立事实，不能相互替代。",
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

    if "query_delivery_logistics_context" in allowed_tools:
        from domain_packs.mold.erp.core.contracts import ProjectPlanContextInput
        from domain_packs.mold.erp.procurement.delivery_logistics import query as delivery_query

        contexts["delivery"] = _first_row(delivery_query(
            db, user, ProjectPlanContextInput(project_id=project_id), allowed_tools
        ))
    else:
        access_gaps.append("交付/签收/客户验收")

    if "query_finance_context" in allowed_tools:
        from domain_packs.mold.erp.project.project_dossier import ProjectDossierInput
        from domain_packs.mold.tools.erp.finance.finance_context_tools import query as finance_query

        contexts["finance"] = _first_row(finance_query(
            db, user, ProjectDossierInput(project_id=project_id), allowed_tools
        ))
    else:
        access_gaps.append("发票/收付款/供应商结算")

    if "query_project_closure_context" in allowed_tools:
        from domain_packs.mold.erp.project.project_closure import context as closure_context

        contexts["closure"] = closure_context(db, user, project_id)
    else:
        access_gaps.append("结项清单/归档/最终关闭")

    case = _closure_case(contexts.get("closure"))
    mode = (case or {}).get("mode") or ("TERMINATION" if project.status == "TERMINATED" else "NORMAL")
    stages = [
        _delivery_stage(contexts.get("delivery"), contexts.get("closure"), mode),
        _customer_finance_stage(contexts.get("finance"), contexts.get("closure"), mode),
        _supplier_settlement_stage(contexts.get("finance"), contexts.get("closure")),
        _issue_stage(contexts.get("closure")),
        _archive_stage(contexts.get("closure"), mode),
        _final_close_stage(project, contexts.get("closure"), mode, allowed_tools),
    ]
    handoffs = _completion_handoffs(stages)
    focus = _current_focus(stages)
    lifecycle = {
        "kind": "project_completion_lifecycle_v1",
        "phase": _phase(focus),
        "closure_mode": mode,
        "current_focus": focus,
        "stages": stages,
        "handoffs": handoffs,
        "recommended_next_steps": _recommendations(focus, stages, allowed_tools),
        "access_gaps": access_gaps,
        "guardrails": [
            "阶段能力缺失时显示 UNAVAILABLE，不根据项目状态、相邻阶段或历史对话推断隐藏事实。",
            "正常关闭要求交付、适用验收、财务、供应商结算、异常和归档分别满足；局部完成不能替代最终关闭。",
            "handoffs 只表示并行收尾责任之间的当前证据边界；READY、WAITING 和 BLOCKED 都不是结算或关闭生效。",
            "终止结算按终止清单核对，可说明交付或验收不适用，但必须保留原因、处置、结算和归档依据。",
            "协调器只读；prepare_* 仍只生成待确认建议，审批通过并成功应用领域命令后项目状态才生效。",
        ],
    }
    if access_gaps:
        limitations.append("未读取以下未分配阶段能力：" + "、".join(access_gaps) + "。")
    return {
        "resolution": "RESOLVED",
        "data": [{
            "project": _project_card(db, user, project, alternatives or ("项目定位",)),
            "analysis": {"completion_lifecycle": lifecycle},
        }],
        "scope_boundary": {
            "complete": True,
            "scope_key": "project_completion",
            "write_tools": sorted(
                tool for tool in COMPLETION_PROPOSAL_TOOLS if tool in allowed_tools
            ),
        },
        "source": "agent_db",
        "as_of": now().isoformat(),
        "limitations": limitations,
    }
