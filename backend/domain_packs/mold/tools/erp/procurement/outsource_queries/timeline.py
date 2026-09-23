"""Read-only outsource timeline for one mold batch."""
from __future__ import annotations

import re
from typing import Any

from domain_packs.mold.erp.procurement.erp_outsource_db import connect

MOLD_FAMILY = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,})(?!-P\d+)(?![A-Z0-9])")
MOLD_BATCH = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,}-P\d+)(?![A-Z0-9])")
PROJECT_NO = re.compile(r"(?i)(?<![A-Z0-9])(E\d+-\d+|ENT-BATCH-[A-Z0-9]+)(?![A-Z0-9])")


def parse_question(question: str) -> dict[str, str]:
    text = question or ""
    batch = MOLD_BATCH.search(text)
    family = None if batch else MOLD_FAMILY.search(text)
    project = PROJECT_NO.search(text)
    return {
        "mold_family": "" if batch else (family.group(1).upper() if family else ""),
        "mold_batch": batch.group(1).upper() if batch else "",
        "project_no": project.group(1).upper() if project else "",
    }


def _one(cursor: Any, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return dict(row) if row else {}


def _rows(cursor: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    cursor.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]


def load_timeline(parsed: dict[str, str]) -> dict[str, Any]:
    mold_batch = parsed["mold_batch"]
    mold_family = parsed["mold_family"]
    project_no = parsed["project_no"]
    if not (mold_batch or mold_family or project_no):
        return {"error": "请指定模具、模具批次或委外项目号。"}
    with connect() as cursor:
        if not mold_batch and (mold_family or project_no):
            batches = _rows(
                cursor,
                """
                SELECT DISTINCT mold.name AS mold_no
                FROM entrust_molds mold
                LEFT JOIN entrust_projects project ON project.id = mold.project_id
                WHERE (%(project_no)s <> '' AND upper(project.project_no) = %(project_no)s)
                   OR (%(mold_family)s <> '' AND (
                        upper(mold.name) = %(mold_family)s
                     OR upper(mold.name) LIKE %(mold_family)s || '-P%%'
                   ))
                ORDER BY mold.name
                """,
                {"mold_family": mold_family, "project_no": project_no},
            )
            names = [str(row["mold_no"]) for row in batches if row.get("mold_no")]
            if len(names) != 1:
                return {"batches": names, "error": "这个范围有多个批次，请指定其中一个，例如 M260063-P4。"}
            mold_batch = names[0]
        work = _one(
            cursor,
            """
            SELECT
                count(*) AS work_order_count,
                count(*) FILTER (WHERE coalesce(status, 0) NOT IN (3, 4)) AS open_count
            FROM work_order
            WHERE coalesce(is_deleted, 0) = 0
              AND upper(mold_no) = %(mold_batch)s
            """,
            {"mold_batch": mold_batch},
        )
        intent = _one(
            cursor,
            """
            SELECT
                count(*) AS intent_count,
                count(*) FILTER (WHERE lower(coalesce(status, '')) NOT IN ('cancelled', 'canceled', 'failed')) AS active_count
            FROM scheduling_outsource_intent
            WHERE upper(mold_code) = %(mold_batch)s
            """,
            {"mold_batch": mold_batch},
        )
        projects = _rows(
            cursor,
            """
            SELECT project.project_no, project.name, project.status, lower(coalesce(project.outsource_type, '')) AS outsource_type
            FROM entrust_projects project
            JOIN entrust_molds mold ON mold.project_id = project.id
            WHERE upper(mold.name) = %(mold_batch)s
            ORDER BY project.id DESC
            """,
            {"mold_batch": mold_batch},
        )
        inquiry = _one(
            cursor,
            """
            SELECT inquiry.status, inquiry.our_quote_amount, inquiry.auto_accept_max_amount,
                   inquiry.reference_total_amount, inquiry.final_deal_amount, inquiry.delivery_date,
                   project.project_no
            FROM entrust_outsource_requests inquiry
            JOIN entrust_projects project ON project.id = inquiry.project_id
            JOIN entrust_molds mold ON mold.project_id = project.id
            WHERE upper(mold.name) = %(mold_batch)s
              AND coalesce(inquiry.inquiry_type, 'normal') <> 'reflow'
            ORDER BY inquiry.id DESC
            LIMIT 1
            """,
            {"mold_batch": mold_batch},
        )
        quotes = _one(
            cursor,
            """
            SELECT
                count(*) AS invitation_count,
                count(*) FILTER (WHERE invitation.status = 'quoted') AS quoted_count
            FROM entrust_invitations invitation
            JOIN entrust_outsource_requests inquiry ON inquiry.id = invitation.request_id
            JOIN entrust_molds mold ON mold.project_id = inquiry.project_id
            WHERE upper(mold.name) = %(mold_batch)s
              AND coalesce(inquiry.inquiry_type, 'normal') <> 'reflow'
              AND inquiry.id = (
                    SELECT picked.id
                    FROM entrust_outsource_requests picked
                    JOIN entrust_molds picked_mold ON picked_mold.project_id = picked.project_id
                    WHERE upper(picked_mold.name) = %(mold_batch)s
                      AND coalesce(picked.inquiry_type, 'normal') <> 'reflow'
                    ORDER BY picked.id DESC
                    LIMIT 1
              )
            """,
            {"mold_batch": mold_batch},
        )
        orders = _rows(
            cursor,
            """
            SELECT order_row.order_no, order_row.stage, order_row.status,
                   order_row.plan_delivery_date, supplier.partner_name
            FROM entrust_outsource_orders order_row
            JOIN entrust_molds mold ON mold.project_id = order_row.project_id
            LEFT JOIN partner supplier ON supplier.id = order_row.supplier_id
            WHERE upper(mold.name) = %(mold_batch)s
              AND lower(coalesce(order_row.stage, '')) <> 'pending_match'
            ORDER BY order_row.id DESC
            LIMIT 8
            """,
            {"mold_batch": mold_batch},
        )
        material = _one(
            cursor,
            """
            SELECT
                count(*) FILTER (WHERE lower(coalesce(task.status, '')) = 'pending') AS pending_count,
                count(*) FILTER (WHERE lower(coalesce(task.status, '')) = 'shipped') AS shipped_count
            FROM entrust_material_supply_tasks task
            JOIN entrust_outsource_orders order_row ON order_row.id = task.order_id
            JOIN entrust_molds mold ON mold.project_id = order_row.project_id
            WHERE upper(mold.name) = %(mold_batch)s
            """,
            {"mold_batch": mold_batch},
        )
        product = _one(
            cursor,
            """
            SELECT
                coalesce(sum(line.qty), 0) AS shipped_qty,
                coalesce(sum(line.inbound_received_qty), 0) AS received_qty
            FROM entrust_product_shipment_lines line
            JOIN entrust_product_shipments shipment ON shipment.id = line.shipment_id
            JOIN entrust_outsource_orders order_row ON order_row.id = shipment.order_id
            JOIN entrust_molds mold ON mold.project_id = order_row.project_id
            WHERE upper(mold.name) = %(mold_batch)s
            """,
            {"mold_batch": mold_batch},
        )
    return {
        "moldBatch": mold_batch,
        "work": work,
        "intent": intent,
        "projects": projects,
        "inquiry": inquiry,
        "quotes": quotes,
        "orders": orders,
        "material": material,
        "product": product,
    }


STEP_STATES = {"done": "已完成", "current": "进行中", "pending": "未开始"}
STATUS_LABELS = {
    "confirmed": "已确认",
    "in_progress": "进行中",
    "draft": "草稿",
    "sent": "已发询价",
    "quoted": "已报价",
    "awarded": "已定标",
    "partial_awarded": "部分定标",
    "closed": "已关闭",
    "pending_approval": "审批中",
    "pending_accept": "待接单",
    "accepted": "已接单",
    "material_receiving": "原料收货",
    "producing": "生产中",
    "shipping": "成品在途",
    "delivered": "已交付",
    "open": "进行中",
}


def _label(value: Any) -> str:
    text = str(value or "").strip()
    return STATUS_LABELS.get(text.lower(), text) if text else "-"


def _step(name: str, state: str, detail: str) -> dict[str, str]:
    return {"step": name, "state": STEP_STATES.get(state, state), "detail": detail}


def build_steps(data: dict[str, Any]) -> list[dict[str, str]]:
    work_count = int((data.get("work") or {}).get("work_order_count") or 0)
    intent_count = int((data.get("intent") or {}).get("active_count") or 0)
    projects = data.get("projects") or []
    inquiry = data.get("inquiry") or {}
    quotes = data.get("quotes") or {}
    orders = data.get("orders") or []
    material = data.get("material") or {}
    product = data.get("product") or {}
    live_order = next((row for row in orders if str(row.get("status") or "").lower() == "open"), None)
    delivered = any(str(row.get("stage") or "").lower() == "delivered" for row in orders)
    steps = [
        _step("排产工单", "done" if work_count else "pending",
              f"{data['moldBatch']} 有 {work_count} 张工单。" if work_count else "还没有排产工单。"),
        _step("委外意图", "done" if intent_count else "pending",
              f"有效委外意图 {intent_count} 条。" if intent_count else "还没有有效委外意图。"),
        _step("委外项目", "done" if projects else "pending",
              "、".join(f"{row.get('project_no')}（{_label(row.get('status'))}）" for row in projects[:4]) or "还没有委外项目。"),
        _step(
            "询价定价",
            "done" if inquiry.get("our_quote_amount") is not None else ("current" if inquiry else "pending"),
            (
                f"项目 {inquiry.get('project_no')} 询价状态 {_label(inquiry.get('status'))}，"
                f"我方报价 {inquiry.get('our_quote_amount') or '未填'}，上限 {inquiry.get('auto_accept_max_amount') or '未填'}。"
            ) if inquiry else "还没有询价单。",
        ),
        _step(
            "加工商报价",
            "done" if int(quotes.get("quoted_count") or 0) else ("current" if int(quotes.get("invitation_count") or 0) else "pending"),
            f"已邀请 {int(quotes.get('invitation_count') or 0)} 家，已报价 {int(quotes.get('quoted_count') or 0)} 家。",
        ),
        _step(
            "委外工单",
            "done" if delivered else ("current" if live_order else "pending"),
            (
                f"{live_order.get('order_no')} 阶段 {_label(live_order.get('stage'))}，加工商 {live_order.get('partner_name') or '-'}。"
                if live_order else ("已有交付记录。" if delivered else "还没有正式委外工单。")
            ),
        ),
        _step(
            "原料发货",
            "done" if int(material.get("shipped_count") or 0) and not int(material.get("pending_count") or 0) else (
                "current" if int(material.get("pending_count") or 0) or int(material.get("shipped_count") or 0) else "pending"
            ),
            f"已发 {int(material.get('shipped_count') or 0)} 条，待发 {int(material.get('pending_count') or 0)} 条。",
        ),
        _step(
            "成品回货",
            "done" if delivered else ("current" if int(product.get("shipped_qty") or 0) else "pending"),
            f"成品已发 {int(product.get('shipped_qty') or 0)}，已入库 {int(product.get('received_qty') or 0)}。",
        ),
    ]
    if next((step for step in steps if step["state"] == STEP_STATES["current"]), None) is None:
        pending = next((step for step in steps if step["state"] == STEP_STATES["pending"]), None)
        if pending is not None:
            pending["state"] = STEP_STATES["current"]
    return steps


def present(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("error"):
        batches = data.get("batches") or []
        summary = data["error"]
        if batches:
            summary += "\n" + "\n".join(f"- {name}" for name in batches[:20])
        return {"database": "erp", "summary": summary, "batches": batches, "steps": []}
    steps = build_steps(data)
    current = next((step["step"] for step in steps if step["state"] == "current"), "已走完已记录节点")
    summary = f"{data['moldBatch']} 当前停在「{current}」。"
    summary += "\n" + "\n".join(f"- {step['step']}：{step['detail']}" for step in steps)
    return {
        "moldBatch": data["moldBatch"],
        "database": "erp",
        "currentStep": current,
        "summary": summary,
        "steps": steps,
    }


def run(parsed: dict[str, str]) -> dict[str, Any]:
    return present(load_timeline(parsed))
