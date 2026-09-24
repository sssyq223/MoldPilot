"""Read-only outsource quality inspection todos from ERP."""
from __future__ import annotations

import re
from typing import Any

from domain_packs.mold.erp.procurement.erp_outsource_db import fetch_all, fetch_one

MOLD_FAMILY = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,})(?!-P\d+)(?![A-Z0-9])")
MOLD_BATCH = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,}-P\d+)(?![A-Z0-9])")
ROW_LIMIT = 100
STATUS_LABELS = {
    "pending": "待领取",
    "inspecting": "质检中",
    "completed": "已完成",
    "partial": "部分合格",
    "reject_return": "拒收退货",
}


SQL = """
SELECT
    task.id AS task_id,
    task.inspection_no,
    task.inbound_id,
    task.inbound_no,
    task.source_type,
    task.order_id,
    task.order_no,
    task.partner_name,
    task.warehouse,
    lower(coalesce(task.status, '')) AS status,
    task.inspector_id,
    task.inspector_name,
    inbound.processor_inbound_target,
    json_agg(
        json_build_object(
            'detailId', detail.id,
            'inboundDetailId', detail.inbound_detail_id,
            'partNo', detail.material_no,
            'partName', detail.material_name,
            'inboundQty', detail.inbound_qty
        )
        ORDER BY detail.id
    ) FILTER (WHERE detail.id IS NOT NULL) AS details
FROM quality_inspection_task task
LEFT JOIN material_inbound inbound ON inbound.id = task.inbound_id
LEFT JOIN quality_inspection_detail detail ON detail.task_id = task.id
WHERE coalesce(task.is_deleted, 0) = 0
  AND lower(coalesce(task.source_type, '')) = 'processor_inbound'
  AND lower(coalesce(task.status, '')) IN ('pending', 'inspecting')
  AND (
        %(mold_batch)s = '' AND %(mold_family)s = ''
     OR (%(mold_batch)s <> '' AND (
            upper(coalesce(task.order_no, '')) LIKE '%%' || %(mold_batch)s || '%%'
         OR upper(coalesce(task.inbound_no, '')) LIKE '%%' || %(mold_batch)s || '%%'
         OR upper(coalesce(task.inspection_no, '')) LIKE '%%' || %(mold_batch)s || '%%'
        ))
     OR (%(mold_family)s <> '' AND (
            upper(coalesce(task.order_no, '')) LIKE '%%' || %(mold_family)s || '%%'
         OR upper(coalesce(task.inbound_no, '')) LIKE '%%' || %(mold_family)s || '%%'
        ))
  )
GROUP BY
    task.id, task.inspection_no, task.inbound_id, task.inbound_no, task.source_type,
    task.order_id, task.order_no, task.partner_name, task.warehouse, task.status,
    task.inspector_id, task.inspector_name, inbound.processor_inbound_target
ORDER BY task.id DESC
LIMIT 200
"""

FIND_SQL = """
SELECT
    task.id AS task_id,
    task.inspection_no,
    task.inbound_id,
    task.inbound_no,
    task.source_type,
    task.order_id,
    task.order_no,
    task.partner_name,
    task.warehouse,
    lower(coalesce(task.status, '')) AS status,
    task.inspector_id,
    task.inspector_name,
    inbound.processor_inbound_target,
    json_agg(
        json_build_object(
            'detailId', detail.id,
            'inboundDetailId', detail.inbound_detail_id,
            'partNo', detail.material_no,
            'partName', detail.material_name,
            'inboundQty', detail.inbound_qty
        )
        ORDER BY detail.id
    ) FILTER (WHERE detail.id IS NOT NULL) AS details
FROM quality_inspection_task task
LEFT JOIN material_inbound inbound ON inbound.id = task.inbound_id
LEFT JOIN quality_inspection_detail detail ON detail.task_id = task.id
WHERE task.id = %(task_id)s
  AND coalesce(task.is_deleted, 0) = 0
GROUP BY
    task.id, task.inspection_no, task.inbound_id, task.inbound_no, task.source_type,
    task.order_id, task.order_no, task.partner_name, task.warehouse, task.status,
    task.inspector_id, task.inspector_name, inbound.processor_inbound_target
"""


def parse_question(question: str) -> dict[str, str]:
    text = question or ""
    batch = MOLD_BATCH.search(text)
    family = None if batch else MOLD_FAMILY.search(text)
    tab = ""
    if any(word in text for word in ("领取",)):
        tab = "claim"
    elif any(word in text for word in ("合格", "检验", "质检")):
        tab = "pass"
    return {
        "mold_family": "" if batch else (family.group(1).upper() if family else ""),
        "mold_batch": batch.group(1).upper() if batch else "",
        "tab": tab,
    }


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def target_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    if key == "finished":
        return "成品库"
    if key == "semi_finished":
        return "半成品库"
    return ""


def item_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    status = str(row.get("status") or "")
    if status not in {"pending", "inspecting"}:
        return None
    details = _as_list(row.get("details"))
    action = "claim" if status == "pending" else "pass"
    hint = (
        "待领取。领取后由同一质检员提交合格。"
        if action == "claim"
        else "已领取，可提交全检合格。不合格/退货本期不办。"
    )
    return {
        "action": action,
        "actionLabel": "领取质检" if action == "claim" else "提交合格",
        "taskId": row.get("task_id"),
        "inspectionNo": row.get("inspection_no") or "",
        "inboundId": row.get("inbound_id"),
        "inboundNo": row.get("inbound_no") or "",
        "orderId": row.get("order_id"),
        "orderNo": row.get("order_no") or "",
        "partnerName": row.get("partner_name") or "",
        "warehouse": row.get("warehouse") or "",
        "inboundTargetLabel": target_label(row.get("processor_inbound_target")),
        "status": status,
        "statusLabel": STATUS_LABELS.get(status, status),
        "inspectorName": row.get("inspector_name") or "",
        "details": details,
        "hint": hint,
        "nextAction": {
            "action": action,
            "taskId": row.get("task_id"),
            "hint": hint,
        },
    }


def query_items(parsed: dict[str, str]) -> list[dict[str, Any]]:
    items = []
    for row in fetch_all(SQL, {
        "mold_family": parsed.get("mold_family") or "",
        "mold_batch": parsed.get("mold_batch") or "",
    }):
        item = item_from_row(row)
        if not item:
            continue
        tab = parsed.get("tab") or ""
        if tab == "claim" and item.get("action") != "claim":
            continue
        if tab == "pass" and item.get("status") not in {"pending", "inspecting"}:
            continue
        items.append(item)
    return items


def find_task(task_id: int) -> dict[str, Any] | None:
    row = fetch_one(FIND_SQL, {"task_id": int(task_id)})
    return item_from_row(row) if row else None


def present(parsed: dict[str, str], items: list[dict[str, Any]]) -> dict[str, Any]:
    truncated = len(items) > ROW_LIMIT
    visible = items[:ROW_LIMIT]
    counts = {"领取质检": 0, "提交合格": 0}
    for item in visible:
        label = item.get("actionLabel") or ""
        if label in counts:
            counts[label] += 1
    scope = parsed.get("mold_batch") or parsed.get("mold_family") or "委外质检待办"
    summary = f"{scope} 共 {len(visible)} 条。"
    summary += "".join(f"\n- {label}：{count} 条" for label, count in counts.items() if count)
    if not visible:
        summary += "\n本次查询结果是 0 条。"
    else:
        lines = []
        for item in visible[:30]:
            lines.append(
                f"{item['actionLabel']} {item.get('inspectionNo') or ''} "
                f"{item.get('orderNo') or item.get('inboundNo') or ''} {item.get('statusLabel')}"
            )
        summary += "\n" + "\n".join(f"- {line}".rstrip() for line in lines)
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
    return present(parsed, query_items(parsed))
