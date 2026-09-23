"""Read-only accepted-order fulfillment gap query."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from domain_packs.mold.erp.procurement.erp_outsource_db import fetch_all

MOLD_FAMILY = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,})(?!-P\d+)(?![A-Z0-9])")
MOLD_BATCH = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,}-P\d+)(?![A-Z0-9])")
PROJECT_NO = re.compile(r"(?i)(?<![A-Z0-9])(E\d+-\d+|ENT-BATCH-[A-Z0-9]+)(?![A-Z0-9])")
ROW_LIMIT = 200
GAPS = {"material": "未发料", "product": "未回货", "overdue": "超期"}

SQL = """
SELECT
    order_row.order_no,
    order_row.stage,
    order_row.plan_delivery_date,
    order_row.quantity,
    project.project_no,
    molds.mold_no,
    supplier.partner_name AS supplier_name,
    coalesce(parts.part_count, 0) AS part_count,
    coalesce(parts.part_qty, 0) AS part_qty,
    coalesce(supply.pending_count, 0) AS pending_material_count,
    coalesce(material.shipped_qty, 0) AS material_shipped_qty,
    coalesce(product.shipped_qty, 0) AS product_shipped_qty,
    coalesce(product.received_qty, 0) AS product_received_qty
FROM entrust_outsource_orders order_row
LEFT JOIN entrust_projects project ON project.id = order_row.project_id
LEFT JOIN partner supplier ON supplier.id = order_row.supplier_id
LEFT JOIN LATERAL (
    SELECT string_agg(DISTINCT mold.name, '、' ORDER BY mold.name) AS mold_no
    FROM entrust_molds mold
    WHERE mold.project_id = order_row.project_id
) molds ON TRUE
LEFT JOIN LATERAL (
    SELECT count(*) AS part_count, coalesce(sum(order_qty), 0) AS part_qty
    FROM entrust_order_parts
    WHERE order_id = order_row.id
) parts ON TRUE
LEFT JOIN LATERAL (
    SELECT count(*) AS pending_count
    FROM entrust_material_supply_tasks task
    WHERE task.order_id = order_row.id
      AND lower(coalesce(task.status, '')) = 'pending'
) supply ON TRUE
LEFT JOIN LATERAL (
    SELECT coalesce(sum(line.qty), 0) AS shipped_qty
    FROM entrust_material_shipments shipment
    JOIN entrust_material_shipment_lines line ON line.shipment_id = shipment.id
    WHERE shipment.order_id = order_row.id
) material ON TRUE
LEFT JOIN LATERAL (
    SELECT
        coalesce(sum(line.qty), 0) AS shipped_qty,
        coalesce(sum(line.inbound_received_qty), 0) AS received_qty
    FROM entrust_product_shipments shipment
    JOIN entrust_product_shipment_lines line ON line.shipment_id = shipment.id
    WHERE shipment.order_id = order_row.id
) product ON TRUE
WHERE lower(coalesce(order_row.status, '')) = 'open'
  AND lower(coalesce(order_row.stage, '')) NOT IN ('pending_match', 'pending_approval', 'pending_accept', 'delivered')
  AND (
        (%(mold_batch)s = '' AND %(mold_family)s = '' AND %(project_no)s = '')
        OR (%(project_no)s <> '' AND upper(project.project_no) = %(project_no)s)
        OR EXISTS (
            SELECT 1 FROM entrust_molds mold
            WHERE mold.project_id = order_row.project_id
              AND (
                    (%(mold_batch)s <> '' AND upper(mold.name) = %(mold_batch)s)
                 OR (%(mold_family)s <> '' AND (
                        upper(mold.name) = %(mold_family)s
                     OR upper(mold.name) LIKE %(mold_family)s || '-P%%'
                    ))
              )
        )
        OR (%(mold_batch)s <> '' AND EXISTS (
            SELECT 1 FROM entrust_order_parts part_row
            WHERE part_row.order_id = order_row.id AND upper(part_row.mold_code) = %(mold_batch)s
        ))
  )
ORDER BY order_row.plan_delivery_date NULLS LAST, order_row.order_no
"""


def parse_question(question: str) -> dict[str, str]:
    text = question or ""
    batch = MOLD_BATCH.search(text)
    family = None if batch else MOLD_FAMILY.search(text)
    project = PROJECT_NO.search(text)
    mentioned = []
    if any(word in text for word in ("超期", "逾期", "过了交期")):
        mentioned.append("overdue")
    if any(word in text for word in ("没发料", "未发料", "原料")):
        mentioned.append("material")
    if any(word in text for word in ("没回来", "未回货", "还没交", "成品")):
        mentioned.append("product")
    return {
        "gap": mentioned[0] if len(mentioned) == 1 else "",
        "mold_family": "" if batch else (family.group(1).upper() if family else ""),
        "mold_batch": batch.group(1).upper() if batch else "",
        "project_no": project.group(1).upper() if project else "",
    }


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def gaps_of(row: dict[str, Any]) -> list[str]:
    stage = str(row.get("stage") or "").lower()
    found = []
    pending_material = int(row.get("pending_material_count") or 0)
    material_shipped = int(row.get("material_shipped_qty") or 0)
    part_qty = int(row.get("part_qty") or 0) or int(row.get("quantity") or 0)
    if pending_material > 0 or (stage in {"accepted", "material_receiving"} and material_shipped <= 0):
        found.append("material")
    received = int(row.get("product_received_qty") or 0)
    if stage in {"producing", "shipping", "accepted", "material_receiving"} and received < max(part_qty, 1):
        found.append("product")
    due = _as_date(row.get("plan_delivery_date"))
    if due is not None and due < date.today() and stage != "delivered":
        found.append("overdue")
    return found


def query_items(parsed: dict[str, str]) -> list[dict[str, Any]]:
    items = []
    for row in fetch_all(SQL, parsed):
        labels = gaps_of(row)
        if parsed["gap"] and parsed["gap"] not in labels:
            continue
        if not labels:
            continue
        due = row.get("plan_delivery_date")
        items.append(
            {
                "orderNo": row.get("order_no") or "",
                "projectNo": row.get("project_no") or "",
                "moldNo": row.get("mold_no") or "",
                "supplierName": row.get("supplier_name") or "",
                "stage": row.get("stage") or "",
                "planDeliveryDate": due.isoformat() if hasattr(due, "isoformat") else due,
                "gaps": [GAPS[key] for key in labels],
                "pendingMaterialCount": int(row.get("pending_material_count") or 0),
                "materialShippedQty": int(row.get("material_shipped_qty") or 0),
                "productShippedQty": int(row.get("product_shipped_qty") or 0),
                "productReceivedQty": int(row.get("product_received_qty") or 0),
            }
        )
    return items


def present(parsed: dict[str, str], items: list[dict[str, Any]]) -> dict[str, Any]:
    truncated = len(items) > ROW_LIMIT
    visible = items[:ROW_LIMIT]
    counts = {label: 0 for label in GAPS.values()}
    for item in visible:
        for label in item["gaps"]:
            counts[label] += 1
    scope = parsed["project_no"] or parsed["mold_batch"] or parsed["mold_family"] or "全部已接单未交付的委外单"
    summary = f"{scope} 有 {len(visible)} 张委外单存在履约缺口。"
    summary += "\n" + "\n".join(f"- {label}：{count} 张" for label, count in counts.items())
    if visible:
        lines = [
            f"{item['orderNo']} {item['moldNo']} {'、'.join(item['gaps'])} 交期 {item['planDeliveryDate'] or '-'}"
            for item in visible[:30]
        ]
        summary += "\n" + "\n".join(f"- {line}" for line in lines)
    else:
        summary += "\n没有查到未发料、未回货或超期的委外单。"
    if truncated:
        summary += f"\n结果超过 {ROW_LIMIT} 条，只返回前 {ROW_LIMIT} 条。"
    return {
        "gap": parsed["gap"] or "all",
        "scope": scope,
        "database": "erp",
        "counts": counts,
        "truncated": truncated,
        "summary": summary,
        "items": visible,
    }


def run(parsed: dict[str, str]) -> dict[str, Any]:
    return present(parsed, query_items(parsed))
