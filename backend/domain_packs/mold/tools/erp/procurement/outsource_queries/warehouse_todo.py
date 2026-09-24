"""Read-only warehouse outsource supply tasks from ERP."""
from __future__ import annotations

import re
from typing import Any

from domain_packs.mold.erp.procurement.erp_outsource_db import fetch_all, fetch_one
from domain_packs.mold.tools.erp.procurement.outsource_queries.buyer_todo import mold_labels

MOLD_FAMILY = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,})(?!-P\d+)(?![A-Z0-9])")
MOLD_BATCH = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,}-P\d+)(?![A-Z0-9])")
ROW_LIMIT = 100

SOURCE_LABELS = {
    "material_stock": "物料库",
    "semi_finished_stock": "半成品库",
    "purchase_direct": "采购直发",
}
OUTSOURCE_LABELS = {"part": "零件委外", "mold": "模具委外", "operation": "工序委外"}

SQL = """
SELECT
    task.id AS task_id,
    task.order_id,
    task.order_no,
    task.order_part_id,
    task.part_no,
    task.part_name,
    task.mold_code,
    task.qty,
    task.status,
    lower(coalesce(task.source_type, '')) AS source_type,
    lower(coalesce(task.responsible_type, '')) AS responsible_type,
    lower(coalesce(order_row.outsource_type, project.outsource_type, 'part')) AS outsource_type,
    coalesce(order_row.process_name, '') AS process_name,
    lower(coalesce(order_row.stage, '')) AS stage,
    supplier.partner_name AS processor_name
FROM entrust_material_supply_tasks task
LEFT JOIN entrust_outsource_orders order_row ON order_row.id = task.order_id
LEFT JOIN entrust_projects project ON project.id = order_row.project_id
LEFT JOIN partner supplier ON supplier.id = order_row.supplier_id
WHERE lower(coalesce(task.responsible_type, '')) = 'warehouse'
  AND lower(coalesce(task.source_type, '')) IN ('material_stock', 'semi_finished_stock')
  AND lower(coalesce(task.status, '')) = 'pending'
  AND (
        %(mold_batch)s = '' AND %(mold_family)s = ''
     OR (%(mold_batch)s <> '' AND upper(coalesce(task.mold_code, '')) = %(mold_batch)s)
     OR (%(mold_family)s <> '' AND (
            upper(coalesce(task.mold_code, '')) = %(mold_family)s
         OR upper(coalesce(task.mold_code, '')) LIKE %(mold_family)s || '-P%%'
        ))
  )
ORDER BY task.created_at DESC, task.id DESC
LIMIT 200
"""

FIND_SQL = """
SELECT
    task.id AS task_id,
    task.order_id,
    task.order_no,
    task.order_part_id,
    task.part_no,
    task.part_name,
    task.mold_code,
    task.qty,
    task.status,
    lower(coalesce(task.source_type, '')) AS source_type,
    lower(coalesce(task.responsible_type, '')) AS responsible_type,
    lower(coalesce(order_row.outsource_type, project.outsource_type, 'part')) AS outsource_type,
    coalesce(order_row.process_name, '') AS process_name,
    lower(coalesce(order_row.stage, '')) AS stage,
    supplier.partner_name AS processor_name
FROM entrust_material_supply_tasks task
LEFT JOIN entrust_outsource_orders order_row ON order_row.id = task.order_id
LEFT JOIN entrust_projects project ON project.id = order_row.project_id
LEFT JOIN partner supplier ON supplier.id = order_row.supplier_id
WHERE task.id = %(task_id)s
"""


def parse_question(question: str) -> dict[str, str]:
    text = question or ""
    batch = MOLD_BATCH.search(text)
    family = None if batch else MOLD_FAMILY.search(text)
    return {
        "mold_family": "" if batch else (family.group(1).upper() if family else ""),
        "mold_batch": batch.group(1).upper() if batch else "",
    }


def needs_weight(item: dict[str, Any]) -> bool:
    return item.get("outsourceType") == "operation" and "热处理" in str(item.get("processName") or "")


def item_from_row(row: dict[str, Any]) -> dict[str, Any]:
    outsource_type = str(row.get("outsource_type") or "part")
    source_type = str(row.get("source_type") or "")
    operation = outsource_type == "operation"
    item = {
        "taskId": row.get("task_id"),
        "orderId": row.get("order_id"),
        "orderNo": row.get("order_no") or "",
        "orderPartId": row.get("order_part_id"),
        "moldNo": row.get("mold_code") or "",
        "partNo": row.get("part_no") or "",
        "partName": row.get("part_name") or "",
        "qty": int(row["qty"]) if row.get("qty") is not None else 0,
        "status": row.get("status") or "",
        "sourceType": source_type,
        "sourceTypeLabel": SOURCE_LABELS.get(source_type, source_type),
        "outsourceType": outsource_type,
        "outsourceTypeLabel": OUTSOURCE_LABELS.get(outsource_type, outsource_type),
        "processName": row.get("process_name") or "",
        "stage": row.get("stage") or "",
        "processorName": row.get("processor_name") or "",
        "action": "prepare" if operation else "ship",
        "actionLabel": "备料完成" if operation else "原料发货",
    }
    item["needsWeight"] = needs_weight(item)
    item["nextHint"] = (
        "确认备料完成后 ERP 视为发货=收货，加工商不用再确认来料，可直接成品发货。"
        if operation
        else "确认发货后由加工商确认原料收货，再进入生产。"
    )
    family, batch = mold_labels(item.get("moldNo"))
    item["moldFamily"] = family
    item["moldBatch"] = batch
    item["nextAction"] = {
        "action": item["action"],
        "orderNo": item.get("orderNo") or "",
        "mold": family,
        "batch": batch,
        "hint": item["nextHint"],
    }
    return item


def query_items(*, mold_family: str = "", mold_batch: str = "") -> list[dict[str, Any]]:
    return [
        item_from_row(row)
        for row in fetch_all(SQL, {"mold_family": mold_family, "mold_batch": mold_batch})
    ]


def find_task(task_id: int) -> dict[str, Any] | None:
    row = fetch_one(FIND_SQL, {"task_id": int(task_id)})
    return item_from_row(row) if row else None


def find_tasks(task_ids: list[int]) -> list[dict[str, Any]]:
    items = []
    for task_id in task_ids:
        item = find_task(task_id)
        if item:
            items.append(item)
    return items


def find_pending_by_identity(*, order_no: str | None = None, mold: str | None = None, batch: str | None = None) -> list[dict[str, Any]]:
    from domain_packs.mold.tools.erp.procurement.outsource_queries.buyer_todo import item_matches_identity

    matched = [
        item for item in query_items()
        if item_matches_identity(item, order_no=order_no or "", mold=mold or "", batch=batch or "")
    ]
    return matched


def present(items: list[dict[str, Any]], *, mold_family: str = "", mold_batch: str = "") -> dict[str, Any]:
    truncated = len(items) > ROW_LIMIT
    visible = items[:ROW_LIMIT]
    part_count = sum(1 for item in visible if item.get("outsourceType") != "operation")
    operation_count = sum(1 for item in visible if item.get("outsourceType") == "operation")
    scope = mold_batch or mold_family or "仓库委外待办"
    summary = f"{scope} 待仓库办理共 {len(visible)} 条。"
    if part_count:
        summary += f"\n- 零件/模具原料发货：{part_count} 条"
    if operation_count:
        summary += f"\n- 工序备料完成：{operation_count} 条"
    if not visible:
        summary += "\n本次查询结果是 0 条。采购直发不在本待办。"
    else:
        lines = []
        for item in visible[:30]:
            lines.append(
                f"{item['actionLabel']} {item['outsourceTypeLabel']} {item['moldNo']} "
                f"{item['partNo']} {item['partName']} ×{item['qty']} {item['orderNo']}"
            )
        summary += "\n" + "\n".join(f"- {line}".rstrip() for line in lines)
        if len(visible) > 30:
            summary += "\n明细只展开前 30 条，完整列表在 items。"
    if truncated:
        summary += f"\n结果超过 {ROW_LIMIT} 条，只返回前 {ROW_LIMIT} 条。"
    return {
        "scope": scope,
        "database": "erp",
        "truncated": truncated,
        "summary": summary,
        "items": visible,
    }


def run(parsed: dict[str, str]) -> dict[str, Any]:
    return present(
        query_items(mold_family=parsed.get("mold_family") or "", mold_batch=parsed.get("mold_batch") or ""),
        mold_family=parsed.get("mold_family") or "",
        mold_batch=parsed.get("mold_batch") or "",
    )
