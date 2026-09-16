from collections import Counter, defaultdict
from decimal import Decimal

from sqlalchemy import or_, select

from . import models as m
from .authorization import access, predicate, select_fields
from .db import now
from .errors import DomainError
from .plan_tools import ProjectPlanContextInput, _strength


DELIVERY_KEYWORDS = ("交付", "出库", "发货", "物流", "签收", "验收", "delivery", "shipment", "acceptance", "logistics")
QUALITY_SOURCES = {"QUALITY_ISSUE", "TRIAL_ISSUE", "ASSEMBLY_ISSUE", "SUPPLIER_QUALITY"}


def _project_card(db, user, project, matched_by=()):
    fields = access(db, user, "project.read", {"project_id": project.id}).fields
    card = select_fields(
        {"id": project.id, "code": project.code, "name": project.name, "status": project.status, "row_version": project.row_version},
        fields,
    )
    card["matched_by"] = sorted(set(matched_by))
    return card


def _visible_projects(db, user):
    rows = list(
        db.scalars(
            select(m.Project)
            .where(predicate(db, user, "project.read", {"project_id": m.Project.id}))
            .order_by(m.Project.code)
            .limit(501)
        )
    )
    return rows[:500], len(rows) > 500


def _resolve(db, user, data: ProjectPlanContextInput, allowed_tools: set[str]):
    visible, truncated = _visible_projects(db, user)
    by_id = {project.id: project for project in visible}
    if data.project_id:
        project = by_id.get(data.project_id)
        return project, ([] if project else None), truncated

    scores = defaultdict(int)
    reasons = defaultdict(list)

    def add(project_id, value, label):
        if project_id not in by_id:
            return
        score = _strength(value, data.identifier)
        if score:
            scores[project_id] = max(scores[project_id], score)
            reasons[project_id].append(label)

    for project in visible:
        add(project.id, project.id, "项目ID")
        add(project.id, project.code, "项目编号")
        add(project.id, project.name, "项目名称")
    if "query_purchase_orders" in allowed_tools or "query_orders" in allowed_tools:
        for order in db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.project_id.in_(list(by_id))).limit(501)):
            add(order.project_id, order.number, "采购/发货订单号")
            for line in db.scalars(select(m.OrderLine).where(m.OrderLine.order_id == order.id).limit(100)):
                for shipment in db.scalars(select(m.SupplierShipment).where(m.SupplierShipment.order_line_id == line.id).limit(100)):
                    add(order.project_id, shipment.reference, "供应商发货单号")
    if "query_contact_cases" in allowed_tools:
        for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id.in_(list(by_id))).limit(501)):
            add(case.project_id, case.title, "工程联络标题")
            add(case.project_id, case.mold_number, "工程联络模具号")
            add(case.project_id, case.product_ref, "工程联络产品")
    if not scores:
        return None, [], truncated
    best = max(scores.values())
    ids = [pid for pid, score in scores.items() if score == best]
    if len(ids) != 1:
        return None, [_project_card(db, user, by_id[pid], reasons[pid]) for pid in ids[:20]], truncated
    return by_id[ids[0]], reasons[ids[0]], truncated


def _profile(db, user, project_id):
    profile = db.get(m.ProjectProfile, project_id)
    if not profile:
        return None
    fields = access(db, user, "project.read", {"project_id": project_id}).fields
    return select_fields(
        {
            "execution_mode": profile.execution_mode,
            "customer_due_date": profile.customer_due_date.isoformat() if profile.customer_due_date else None,
            "settlement_status": profile.settlement_status,
        },
        fields | {"execution_mode", "customer_due_date", "settlement_status"},
    )


def _visible_subjects(db, user, project_id, kind, allowed_tools):
    tool = "query_" + kind
    context_tools = {
        "project_plan": {"query_project_plan_context", "query_delivery_logistics_context"},
        "plan_change": {"query_project_plan_context", "query_delivery_logistics_context"},
        "trial_request": {"query_trial_request", "query_assembly_trial_context"},
        "project_close": {"query_project_closure_context"},
    }
    if tool not in allowed_tools and not (context_tools.get(kind, set()) & allowed_tools):
        return []
    from .domains import data as subject_data

    result = []
    for subject in db.scalars(
        select(m.BusinessSubject)
        .where(m.BusinessSubject.project_id == project_id, m.BusinessSubject.kind == kind)
        .order_by(m.BusinessSubject.created_at.desc(), m.BusinessSubject.id)
        .limit(100)
    ):
        try:
            result.append(subject_data(db, user, subject))
        except DomainError:
            continue
    return result


def _plan_tasks(records):
    plans = records["project_plan"] + records["plan_change"]
    effective = [row for row in plans if row.get("status") == "EFFECTIVE"]
    active = max(effective, key=lambda row: row.get("created_at", "")) if effective else None
    detail = active.get("detail") if active and isinstance(active.get("detail"), dict) else {}
    result = []
    for task in detail.get("tasks") or []:
        text = (str(task.get("key") or "") + " " + str(task.get("name") or "")).casefold()
        if any(keyword.casefold() in text for keyword in DELIVERY_KEYWORDS):
            result.append(
                {
                    "id": task.get("id"),
                    "key": task.get("key"),
                    "name": task.get("name"),
                    "status": task.get("status"),
                    "planned_start": task.get("planned_start"),
                    "planned_end": task.get("planned_end"),
                    "actual_start": task.get("actual_start"),
                    "actual_end": task.get("actual_end"),
                    "prerequisites": task.get("prerequisites") or [],
                }
            )
    return active, result


def _plan_headers(records):
    return {
        key: [
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "created_at": row.get("created_at"),
            }
            for row in rows
        ]
        for key, rows in records.items()
    }


def _order_rows(db, user, project_id, allowed_tools):
    if "query_purchase_orders" not in allowed_tools and "query_orders" not in allowed_tools:
        return []
    from .procurement import order_data

    result = []
    q = select(m.PurchaseOrder).where(m.PurchaseOrder.project_id == project_id).order_by(m.PurchaseOrder.created_at.desc()).limit(100)
    for order in db.scalars(q):
        try:
            result.append(order_data(db, user, order))
        except DomainError:
            continue
    return result


def _order_headers(orders):
    return [
        {
            "id": order.get("id"),
            "number": order.get("number"),
            "status": order.get("status"),
            "currency": order.get("currency"),
            "line_count": len(order.get("lines") or []),
        }
        for order in orders
    ]


def _warehouse_name(db, warehouse_id):
    warehouse = db.get(m.Warehouse, warehouse_id)
    return warehouse.name if warehouse else None


def _augment_order_receipts(db, user, orders):
    for order in orders:
        for line in order.get("lines") or []:
            for shipment in line.get("shipments") or []:
                receipts = []
                for receipt in db.scalars(select(m.GoodsReceipt).where(m.GoodsReceipt.shipment_id == shipment.get("id")).limit(100)):
                    if not access(db, user, "warehouse.read", {"warehouse_id": receipt.warehouse_id}).allowed:
                        continue
                    inspection = db.scalar(select(m.ReceiptInspection).where(m.ReceiptInspection.receipt_id == receipt.id))
                    receipts.append(
                        {
                            "id": receipt.id,
                            "quantity": str(receipt.quantity),
                            "warehouse_id": receipt.warehouse_id,
                            "warehouse_name": _warehouse_name(db, receipt.warehouse_id),
                            "reference": receipt.reference,
                            "evidence": receipt.evidence,
                            "inspection": (
                                {
                                    "id": inspection.id,
                                    "accepted_quantity": str(inspection.accepted_quantity),
                                    "rejected_quantity": str(inspection.rejected_quantity),
                                    "evidence": inspection.evidence,
                                }
                                if inspection
                                else None
                            ),
                        }
                    )
                shipment["receipts"] = receipts


def _shipment_tracking(orders):
    totals = Counter()
    lines = []
    for order in orders:
        for line in order.get("lines") or []:
            quantity = Decimal(str(line.get("quantity") or "0"))
            shipped = Decimal(str(line.get("shipped_quantity") or "0"))
            receipts = [
                receipt
                for shipment in line.get("shipments") or []
                for receipt in shipment.get("receipts") or []
            ]
            inspections = [receipt.get("inspection") for receipt in receipts if receipt.get("inspection")]
            accepted = sum((Decimal(str(item.get("accepted_quantity") or "0")) for item in inspections), Decimal(0))
            rejected = sum((Decimal(str(item.get("rejected_quantity") or "0")) for item in inspections), Decimal(0))
            totals["order_lines"] += 1
            totals["supplier_shipments"] += len(line.get("shipments") or [])
            totals["goods_receipts"] += len(receipts)
            totals["receipt_inspections"] += len(inspections)
            if shipped < quantity:
                totals["unshipped_lines"] += 1
            if receipts and accepted + rejected < shipped:
                totals["uninspected_received_lines"] += 1
            if rejected > 0:
                totals["rejected_receipt_lines"] += 1
            if line.get("exceptions"):
                totals["exception_lines"] += 1
            lines.append(
                {
                    "order_id": order.get("id"),
                    "order_number": order.get("number"),
                    "order_status": order.get("status"),
                    "line_id": line.get("id"),
                    "material_id": line.get("material_id"),
                    "material_name": line.get("material_name"),
                    "category": line.get("category"),
                    "quantity": line.get("quantity"),
                    "unit": line.get("unit"),
                    "agreed_ship_date": line.get("agreed_ship_date"),
                    "shipped_quantity": str(shipped),
                    "accepted_quantity": str(accepted),
                    "rejected_quantity": str(rejected),
                    "shipments": line.get("shipments") or [],
                    "exceptions": line.get("exceptions") or [],
                }
            )
    return {"totals": dict(totals), "lines": lines[:100]}


def _stock_movements(db, user, project_id):
    rows = []
    q = (
        select(m.StockMovement, m.StockBalance, m.Warehouse, m.Material)
        .join(m.StockBalance, m.StockMovement.balance_id == m.StockBalance.id)
        .join(m.Warehouse, m.StockBalance.warehouse_id == m.Warehouse.id)
        .join(m.Material, m.StockBalance.material_id == m.Material.id)
        .where(m.StockBalance.project_id == project_id)
        .order_by(m.StockMovement.created_at.desc())
        .limit(100)
    )
    for movement, balance, warehouse, material in db.execute(q):
        if not access(db, user, "warehouse.read", {"warehouse_id": warehouse.id}).allowed:
            continue
        rows.append(
            {
                "id": movement.id,
                "warehouse_id": warehouse.id,
                "warehouse_name": warehouse.name,
                "material_id": material.id,
                "material_code": material.code,
                "material_name": material.name,
                "quantity": str(movement.quantity),
                "kind": movement.kind,
                "source_key": movement.source_key,
                "evidence": movement.evidence,
                "created_at": movement.created_at.isoformat() if movement.created_at else None,
            }
        )
    return rows


def _trial_results(trials):
    rows = []
    for trial in trials:
        detail = trial.get("detail") if isinstance(trial.get("detail"), dict) else {}
        for result in detail.get("results") or []:
            rows.append(
                {
                    "trial_id": trial.get("id"),
                    "trial_number": trial.get("number"),
                    "passed": result.get("passed"),
                    "actual_date": result.get("actual_date"),
                    "evidence": result.get("evidence"),
                    "findings": result.get("findings"),
                }
            )
    return rows


def _closure_items(db, user, project_id, allowed_tools):
    if "query_project_closure_context" not in allowed_tools:
        return []
    if not access(db, user, "project_close.read", {"project_id": project_id}).allowed:
        return []
    cases = list(
        db.scalars(
            select(m.ProjectClosureCase)
            .where(m.ProjectClosureCase.project_id == project_id)
            .order_by(m.ProjectClosureCase.created_at.desc(), m.ProjectClosureCase.id)
            .limit(20)
        )
    )
    rows = []
    for case in cases:
        for item in db.scalars(select(m.ProjectClosureItem).where(m.ProjectClosureItem.case_id == case.id).limit(100)):
            if any(token in (item.item_key + item.label) for token in ("DELIVERY", "ACCEPTANCE", "QUALITY", "发货", "交付", "验收", "质量")):
                rows.append(
                    {
                        "case_id": case.id,
                        "mode": case.mode,
                        "case_status": case.status,
                        "item_key": item.item_key,
                        "label": item.label,
                        "status": item.status,
                        "result": item.result,
                        "evidence": item.evidence,
                        "source_system": item.source_system,
                        "source_ref": item.source_ref,
                    }
                )
    return rows[:100]


def _contact_issues(db, user, project_id, allowed_tools):
    if "query_contact_cases" not in allowed_tools:
        return []
    from .contacts import permitted

    issues = []
    q = (
        select(m.ContactCase)
        .where(m.ContactCase.project_id == project_id, predicate(db, user, "contact.read", {"project_id": m.ContactCase.project_id, "category": m.ContactCase.category}))
        .order_by(m.ContactCase.created_at.desc(), m.ContactCase.id)
        .limit(100)
    )
    for case in db.scalars(q):
        if not permitted(db, user, "read", case):
            continue
        text = (case.title or "") + (case.current_stage or "") + (case.problem_source or "")
        tasks = []
        for task in db.scalars(select(m.ContactTask).where(m.ContactTask.case_id == case.id, m.ContactTask.status != "CANCELLED").limit(50)):
            task_text = (task.title or "") + (task.affected_type or "") + (task.affected_ref or "") + (task.impact_description or "")
            if any(keyword in task_text for keyword in ("发货", "物流", "签收", "验收", "出库", "质量")):
                tasks.append(
                    {
                        "id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "affected_type": task.affected_type,
                        "affected_ref": task.affected_ref,
                        "planned_action": task.planned_action,
                        "actual_completed_at": task.actual_completed_at.isoformat() if task.actual_completed_at else None,
                        "delivery_impact_days": task.delivery_impact_days,
                    }
                )
        if case.problem_source in QUALITY_SOURCES or any(keyword in text for keyword in ("发货", "物流", "签收", "验收", "出库", "质量")) or tasks:
            issues.append(
                {
                    "id": case.id,
                    "title": case.title,
                    "collaboration_status": "CLOSED" if case.closed_at else "HISTORY_RECORD" if case.mode == "HISTORY" else "OPEN",
                    "problem_source": case.problem_source,
                    "current_stage": case.current_stage,
                    "change_type": case.change_type,
                    "urgency": case.urgency,
                    "tasks": tasks[:20],
                }
            )
    return issues[:20]


def _logistics_pricing(db, project_id):
    today = now().date()
    rows = []
    q = (
        select(m.LogisticsRoute, m.LogisticsQuote)
        .join(m.LogisticsQuote, m.LogisticsQuote.route_id == m.LogisticsRoute.id)
        .where(
            m.LogisticsRoute.active.is_(True),
            or_(m.LogisticsRoute.project_id.is_(None), m.LogisticsRoute.project_id == project_id),
        )
        .order_by(m.LogisticsQuote.valid_from.desc(), m.LogisticsQuote.created_at.desc(), m.LogisticsQuote.id)
        .limit(100)
    )
    for route, quote in db.execute(q):
        is_valid_now = quote.status == "EFFECTIVE" and quote.valid_from <= today <= quote.valid_to
        is_settlement = quote.settlement_for_project_id == project_id
        rows.append(
            {
                "route": {
                    "id": route.id,
                    "project_id": route.project_id,
                    "route_code": route.route_code,
                    "origin": route.origin,
                    "destination": route.destination,
                    "carrier_name": route.carrier_name,
                    "vehicle_type": route.vehicle_type,
                    "transport_mode": route.transport_mode,
                    "price_unit": route.price_unit,
                    "tax_mode": route.tax_mode,
                    "evidence": route.evidence,
                },
                "quote": {
                    "id": quote.id,
                    "supplier_id": quote.supplier_id,
                    "unit_price": str(quote.unit_price),
                    "currency": quote.currency,
                    "valid_from": quote.valid_from.isoformat(),
                    "valid_to": quote.valid_to.isoformat(),
                    "status": quote.status,
                    "settlement_for_project_id": quote.settlement_for_project_id,
                    "quote_evidence": quote.quote_evidence,
                    "approved_by": quote.approved_by,
                    "approved_at": quote.approved_at.isoformat() if quote.approved_at else None,
                    "is_valid_now": is_valid_now,
                    "is_settlement_price_for_project": is_settlement,
                },
            }
        )
    effective = [row for row in rows if row["quote"]["is_valid_now"]]
    settlement = [row for row in effective if row["quote"]["is_settlement_price_for_project"]]
    open_reviews = [row for row in rows if row["quote"]["status"] in {"DRAFT", "SUBMITTED"}]
    expired = [row for row in rows if row["quote"]["status"] == "EXPIRED" or row["quote"]["valid_to"] < today.isoformat()]
    return {
        "effective_quotes": effective[:20],
        "settlement_price_candidates": settlement[:20],
        "open_price_reviews": open_reviews[:20],
        "expired_quotes": expired[:20],
        "derived_status": {
            "has_route": bool(rows),
            "has_effective_quote": bool(effective),
            "has_project_settlement_price": bool(settlement),
            "has_open_price_review": bool(open_reviews),
        },
    }


def _analysis(project, profile, active_plan, delivery_tasks, shipment_tracking, stock_movements, trials, closure_items, contacts, logistics_pricing):
    totals = shipment_tracking["totals"]
    trial_passed = [row for row in trials if row.get("passed") is True]
    trial_failed = [row for row in trials if row.get("passed") is False]
    customer_acceptance_done = [row for row in closure_items if "ACCEPTANCE" in row.get("item_key", "") and row.get("status") == "DONE"]
    delivery_done = [row for row in closure_items if "DELIVERY" in row.get("item_key", "") and row.get("status") == "DONE"]
    open_contacts = [row for row in contacts if row.get("collaboration_status") != "CLOSED"]
    stock_out = [row for row in stock_movements if Decimal(str(row.get("quantity") or "0")) < 0]

    gaps = []
    warnings = []
    if not active_plan:
        warnings.append("当前可见范围未见有效项目计划，无法核对交付/出库/客户验收节点。")
    if not delivery_tasks:
        gaps.append("未见明确的交付、出库、发货、客户签收或验收计划节点。")
    if not totals.get("supplier_shipments"):
        gaps.append("未见当前可见正式订单的供应商发货记录；不能据此判断实物已交付。")
    if totals.get("supplier_shipments") and not totals.get("goods_receipts"):
        gaps.append("已有供应商发货记录，但未见仓库签收/收货回执。")
    if totals.get("goods_receipts") and not totals.get("receipt_inspections"):
        gaps.append("已有收货记录，但未见入库检验或出厂质量检验结论。")
    if totals.get("rejected_receipt_lines"):
        warnings.append("存在收货检验不合格数量，需关联退换货、扣款、整改或工程联络处理。")
    if trial_failed:
        warnings.append("存在试模未通过结果，不能进入出厂验收或客户验收结论。")
    if trial_passed:
        warnings.append("试模通过只代表试模结论，不等于客户签收、客户验收或项目关闭。")
    if stock_out:
        warnings.append("存在库存出库类移动记录；仍需核对对应发货物流单、客户签收和客户验收材料。")
    if open_contacts:
        warnings.append("存在未关闭质量、交付、物流或验收相关工程联络事项，不能认定整改闭环完成。")
    if not delivery_done:
        gaps.append("未见结项/归档清单中的交付或发货完成依据。")
    if not customer_acceptance_done:
        gaps.append("未见客户签收与客户质量验收分别确认的正式依据。")
    pricing_status = logistics_pricing["derived_status"]
    if not pricing_status["has_route"]:
        gaps.append("未见结构化固定物流路线、承运商车型、计价单位、含税方式和有效期。")
    elif not pricing_status["has_effective_quote"]:
        gaps.append("已登记物流路线，但未见当前有效的物流报价。")
    if pricing_status["has_effective_quote"] and not pricing_status["has_project_settlement_price"]:
        warnings.append("存在有效物流报价，但尚未明确本项目本次结算价格；费用对账仍需按项目确认。")
    if pricing_status["has_open_price_review"]:
        warnings.append("存在未完成的物流报价审批，正式结算价格可能即将变化。")

    return {
        "active_plan": (
            {
                "id": active_plan.get("id"),
                "number": active_plan.get("number"),
                "status": active_plan.get("status"),
                "created_at": active_plan.get("created_at"),
            }
            if active_plan
            else None
        ),
        "delivery_plan_tasks": delivery_tasks[:50],
        "shipment_tracking": shipment_tracking,
        "stock_movements": stock_movements[:100],
        "trial_results": trials[:50],
        "closure_delivery_acceptance_items": closure_items[:100],
        "delivery_quality_contacts": contacts,
        "logistics_pricing": logistics_pricing,
        "gaps": gaps,
        "warnings": warnings,
        "derived_status": {
            "project_status": project.status,
            "execution_mode": (profile or {}).get("execution_mode"),
            "has_effective_plan": bool(active_plan),
            "has_delivery_plan_node": bool(delivery_tasks),
            "has_supplier_shipment": bool(totals.get("supplier_shipments")),
            "has_goods_receipt": bool(totals.get("goods_receipts")),
            "has_receipt_inspection": bool(totals.get("receipt_inspections")),
            "has_rejected_receipt": bool(totals.get("rejected_receipt_lines")),
            "has_stock_out_movement": bool(stock_out),
            "has_trial_passed": bool(trial_passed),
            "has_trial_failed": bool(trial_failed),
            "has_customer_signature": False,
            "has_customer_acceptance": bool(customer_acceptance_done),
            "has_open_delivery_or_quality_issue": bool(open_contacts),
            "has_structured_logistics_price": pricing_status["has_effective_quote"],
            "has_project_logistics_settlement_price": pricing_status["has_project_settlement_price"],
        },
    }


def query(db, user, data: ProjectPlanContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data, allowed_tools)
    limitations = [
        "只读取当前用户可见项目；订单、仓库、试模、结项和工程联络材料分别受对应工具与权限约束。",
        "本工具只核对交付、物流、质量、签收和验收上下文，不创建物流路线、不维护报价、不确认发货、不确认客户签收或验收。",
        "采购发货、仓库收货、入库检验、试模通过、客户签收和客户验收是不同事实，不能相互替代。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project:
        records = {
            "project_plan": _visible_subjects(db, user, project.id, "project_plan", allowed_tools)[:20],
            "plan_change": _visible_subjects(db, user, project.id, "plan_change", allowed_tools)[:20],
        }
        active_plan, delivery_tasks = _plan_tasks(records)
        orders = _order_rows(db, user, project.id, allowed_tools)
        _augment_order_receipts(db, user, orders)
        shipment_tracking = _shipment_tracking(orders)
        stock_movements = _stock_movements(db, user, project.id)
        trials = _trial_results(_visible_subjects(db, user, project.id, "trial_request", allowed_tools))
        closure_items = _closure_items(db, user, project.id, allowed_tools)
        contacts = _contact_issues(db, user, project.id, allowed_tools)
        logistics_pricing = _logistics_pricing(db, project.id)
        profile = _profile(db, user, project.id)
        skipped = []
        if not records["project_plan"] and not records["plan_change"]:
            skipped.append("项目计划/计划变更")
        if "query_purchase_orders" not in allowed_tools and "query_orders" not in allowed_tools:
            skipped.append("正式采购订单/供应商发货/收货跟踪")
        if "query_trial_request" not in allowed_tools and "query_assembly_trial_context" not in allowed_tools:
            skipped.append("试模结果")
        if "query_project_closure_context" not in allowed_tools:
            skipped.append("项目结项/交付验收清单")
        if "query_contact_cases" not in allowed_tools:
            skipped.append("工程联络异常与整改")
        if skipped:
            limitations.append("未分配对应查询工具或权限，未返回：" + "、".join(skipped))
        return {
            "resolution": "RESOLVED",
            "data": [
                {
                    "project": _project_card(db, user, project, alternatives or ("项目定位",)),
                    "profile": profile,
                    "project_plans": _plan_headers(records)["project_plan"],
                    "plan_changes": _plan_headers(records)["plan_change"],
                    "purchase_orders": _order_headers(orders),
                    "analysis": _analysis(project, profile, active_plan, delivery_tasks, shipment_tracking, stock_movements, trials, closure_items, contacts, logistics_pricing),
                }
            ],
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations,
        }
    if alternatives is None:
        return {"resolution": "NOT_FOUND_OR_FORBIDDEN", "data": [], "source": "agent_db", "as_of": now().isoformat(), "limitations": limitations}
    if alternatives:
        return {
            "resolution": "MULTIPLE_CANDIDATES",
            "data": alternatives,
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations + ["线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。"],
        }
    return {"resolution": "NOT_FOUND", "data": [], "source": "agent_db", "as_of": now().isoformat(), "limitations": limitations}
