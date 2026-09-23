"""Read-only warehouse product arrival / inbound todos from ERP."""
from __future__ import annotations

import re
from typing import Any

from domain_packs.mold.erp.procurement.erp_outsource_db import fetch_all, fetch_one
from domain_packs.mold.tools.erp.procurement.outsource_queries.processor_fulfillment import (
    PROCESSOR_INBOUND_RULE_VERSION,
    flag_tf,
    inbound_target,
)

MOLD_FAMILY = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,})(?!-P\d+)(?![A-Z0-9])")
MOLD_BATCH = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,}-P\d+)(?![A-Z0-9])")
ROW_LIMIT = 100
OUTSOURCE_LABELS = {"part": "零件委外", "mold": "模具委外", "operation": "工序委外"}
ARRIVAL_LABELS = {
    "pending_arrival_confirm": "待仓库收货",
    "partial_arrival_confirmed": "部分收货",
    "arrival_confirmed": "已收货",
    "arrival_rejected": "已拒收",
}

SQL = """
SELECT
    shipment.id AS shipment_id,
    shipment.shipment_no,
    shipment.order_id,
    order_row.order_no,
    lower(coalesce(shipment.outsource_type, order_row.outsource_type, project.outsource_type, 'part')) AS outsource_type,
    lower(coalesce(shipment.arrival_status, '')) AS arrival_status,
    lower(coalesce(shipment.status, '')) AS shipment_status,
    supplier.partner_name AS supplier_name,
    molds.mold_no,
    json_agg(
        json_build_object(
            'lineId', line.id,
            'partNo', line.part_no,
            'partName', line.part_name,
            'moldNo', line.mold_no,
            'isEndOperation', line.is_end_operation,
            'qty', coalesce(line.qty, 0),
            'arrivalConfirmedQty', coalesce(line.arrival_confirmed_qty, 0),
            'arrivalExceptionQty', coalesce(line.arrival_exception_qty, 0),
            'inboundReceivedQty', coalesce(line.inbound_received_qty, 0),
            'returnQty', coalesce(line.return_qty, 0),
            'pendingArrivalQty', greatest(
                0,
                coalesce(line.qty, 0)
                - coalesce(line.arrival_confirmed_qty, 0)
                - coalesce(line.arrival_exception_qty, 0)
            ),
            'pendingInboundQty', greatest(
                0,
                coalesce(line.arrival_confirmed_qty, 0)
                - coalesce(line.inbound_received_qty, 0)
                - coalesce(line.return_qty, 0)
            )
        )
        ORDER BY line.id
    ) AS lines
FROM entrust_product_shipments shipment
JOIN entrust_outsource_orders order_row ON order_row.id = shipment.order_id
LEFT JOIN entrust_projects project ON project.id = order_row.project_id
LEFT JOIN partner supplier ON supplier.id = order_row.supplier_id
LEFT JOIN entrust_product_shipment_lines line ON line.shipment_id = shipment.id
LEFT JOIN LATERAL (
    SELECT string_agg(DISTINCT nullif(btrim(line.mold_no), ''), '、') AS mold_no
    FROM entrust_product_shipment_lines line
    WHERE line.shipment_id = shipment.id
) molds ON TRUE
WHERE lower(coalesce(shipment.status, '')) <> 'cancelled'
  AND (
        %(mold_batch)s = '' AND %(mold_family)s = ''
     OR (%(mold_batch)s <> '' AND (
            upper(coalesce(molds.mold_no, '')) LIKE '%%' || %(mold_batch)s || '%%'
         OR upper(coalesce(order_row.order_no, '')) LIKE '%%' || %(mold_batch)s || '%%'
        ))
     OR (%(mold_family)s <> '' AND (
            upper(coalesce(molds.mold_no, '')) = %(mold_family)s
         OR upper(coalesce(molds.mold_no, '')) LIKE %(mold_family)s || '%%'
        ))
  )
GROUP BY
    shipment.id, shipment.shipment_no, shipment.order_id, order_row.order_no,
    shipment.outsource_type, order_row.outsource_type, project.outsource_type,
    shipment.arrival_status, shipment.status, supplier.partner_name, molds.mold_no
HAVING
    coalesce(sum(greatest(
        0,
        coalesce(line.qty, 0) - coalesce(line.arrival_confirmed_qty, 0) - coalesce(line.arrival_exception_qty, 0)
    )), 0) > 0
    OR coalesce(sum(greatest(
        0,
        coalesce(line.arrival_confirmed_qty, 0) - coalesce(line.inbound_received_qty, 0)
        - coalesce(line.return_qty, 0)
    )), 0) > 0
ORDER BY shipment.id DESC
LIMIT 200
"""

FIND_SQL = """
SELECT
    shipment.id AS shipment_id,
    shipment.shipment_no,
    shipment.order_id,
    order_row.order_no,
    lower(coalesce(shipment.outsource_type, order_row.outsource_type, project.outsource_type, 'part')) AS outsource_type,
    lower(coalesce(shipment.arrival_status, '')) AS arrival_status,
    lower(coalesce(shipment.status, '')) AS shipment_status,
    supplier.partner_name AS supplier_name,
    molds.mold_no,
    json_agg(
        json_build_object(
            'lineId', line.id,
            'partNo', line.part_no,
            'partName', line.part_name,
            'moldNo', line.mold_no,
            'isEndOperation', line.is_end_operation,
            'qty', coalesce(line.qty, 0),
            'arrivalConfirmedQty', coalesce(line.arrival_confirmed_qty, 0),
            'arrivalExceptionQty', coalesce(line.arrival_exception_qty, 0),
            'inboundReceivedQty', coalesce(line.inbound_received_qty, 0),
            'returnQty', coalesce(line.return_qty, 0),
            'pendingArrivalQty', greatest(
                0,
                coalesce(line.qty, 0)
                - coalesce(line.arrival_confirmed_qty, 0)
                - coalesce(line.arrival_exception_qty, 0)
            ),
            'pendingInboundQty', greatest(
                0,
                coalesce(line.arrival_confirmed_qty, 0)
                - coalesce(line.inbound_received_qty, 0)
                - coalesce(line.return_qty, 0)
            )
        )
        ORDER BY line.id
    ) AS lines
FROM entrust_product_shipments shipment
JOIN entrust_outsource_orders order_row ON order_row.id = shipment.order_id
LEFT JOIN entrust_projects project ON project.id = order_row.project_id
LEFT JOIN partner supplier ON supplier.id = order_row.supplier_id
LEFT JOIN entrust_product_shipment_lines line ON line.shipment_id = shipment.id
LEFT JOIN LATERAL (
    SELECT string_agg(DISTINCT nullif(btrim(line.mold_no), ''), '、') AS mold_no
    FROM entrust_product_shipment_lines line
    WHERE line.shipment_id = shipment.id
) molds ON TRUE
WHERE shipment.id = %(shipment_id)s
  AND lower(coalesce(shipment.status, '')) <> 'cancelled'
GROUP BY
    shipment.id, shipment.shipment_no, shipment.order_id, order_row.order_no,
    shipment.outsource_type, order_row.outsource_type, project.outsource_type,
    shipment.arrival_status, shipment.status, supplier.partner_name, molds.mold_no
"""


def parse_question(question: str) -> dict[str, str]:
    text = question or ""
    batch = MOLD_BATCH.search(text)
    family = None if batch else MOLD_FAMILY.search(text)
    tab = ""
    if any(word in text for word in ("入库",)):
        tab = "inbound"
    elif any(word in text for word in ("到货", "收货", "确认收货")):
        tab = "arrival"
    return {
        "mold_family": "" if batch else (family.group(1).upper() if family else ""),
        "mold_batch": batch.group(1).upper() if batch else "",
        "tab": tab,
    }


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _enrich_line(outsource_type: str, line: dict[str, Any]) -> dict[str, Any]:
    pending_arrival = int(line.get("pendingArrivalQty") or 0)
    pending_inbound = int(line.get("pendingInboundQty") or 0)
    target = inbound_target(outsource_type=outsource_type, is_end_operation=line.get("isEndOperation"))
    enriched = dict(line)
    enriched["pendingArrivalQty"] = pending_arrival
    enriched["pendingInboundQty"] = pending_inbound
    enriched["isEndOperationLabel"] = flag_tf(line.get("isEndOperation"))
    enriched["inboundTarget"] = target["target"]
    enriched["inboundTargetLabel"] = target["label"]
    enriched["inboundRuleVersion"] = target["ruleVersion"]
    enriched["inboundTargetWarning"] = target["warning"]
    return enriched


def item_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    outsource_type = str(row.get("outsource_type") or "").strip().lower()
    lines = [_enrich_line(outsource_type, line) for line in _as_list(row.get("lines"))]
    pending_arrival = sum(int(line.get("pendingArrivalQty") or 0) for line in lines)
    pending_inbound = sum(int(line.get("pendingInboundQty") or 0) for line in lines)
    if pending_arrival <= 0 and pending_inbound <= 0:
        return None
    if pending_arrival > 0:
        action, label = "arrival", "仓库收货"
        hint = "先做到货确认。确认后同一发货单再办理仓储入库。"
    else:
        action, label = "inbound", "仓储入库"
        hint = "到货已确认。入库目标按 ERP processor-inbound-v1 行级结果，一张发货单可同时含成品库和半成品库。入库后质检领取任务。"
    targets = list(dict.fromkeys(line["inboundTargetLabel"] for line in lines if line.get("inboundTarget")))
    warnings = [line["inboundTargetWarning"] for line in lines if line.get("inboundTargetWarning")]
    if warnings:
        hint = f"{hint} {'；'.join(dict.fromkeys(warnings))}"
    return {
        "action": action,
        "actionLabel": label,
        "shipmentId": row.get("shipment_id"),
        "shipmentNo": row.get("shipment_no") or "",
        "orderId": row.get("order_id"),
        "orderNo": row.get("order_no") or "",
        "moldNo": row.get("mold_no") or "",
        "outsourceType": outsource_type,
        "outsourceTypeLabel": OUTSOURCE_LABELS.get(outsource_type, outsource_type),
        "arrivalStatus": row.get("arrival_status") or "",
        "arrivalStatusLabel": ARRIVAL_LABELS.get(str(row.get("arrival_status") or ""), row.get("arrival_status") or ""),
        "supplierName": row.get("supplier_name") or "",
        "pendingArrivalQty": pending_arrival,
        "pendingInboundQty": pending_inbound,
        "inboundTargets": targets,
        "inboundTargetWarnings": list(dict.fromkeys(warnings)),
        "inboundRuleVersion": PROCESSOR_INBOUND_RULE_VERSION,
        "lines": lines,
        "hint": hint,
        "nextAction": {
            "action": action,
            "shipmentId": row.get("shipment_id"),
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
        if tab == "arrival" and int(item.get("pendingArrivalQty") or 0) <= 0:
            continue
        if tab == "inbound" and int(item.get("pendingInboundQty") or 0) <= 0:
            continue
        items.append(item)
    return items


def erp_inbound_confirm_lines(payload: Any) -> list[dict[str, Any]]:
    """Read per-line warehouse results from ERP confirm-inbound (processor-inbound-v1)."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    raw = data.get("inboundDetails") or data.get("inbound_details") or []
    lines = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        target = item.get("processorInboundTarget") or item.get("processor_inbound_target")
        label = item.get("targetWarehouseName") or item.get("target_warehouse_name")
        if target and not label:
            label = "成品库" if target == "finished" else "半成品库" if target == "semi_finished" else None
        lines.append({
            "shipmentLineId": item.get("shipmentLineId") or item.get("shipment_line_id"),
            "inboundTarget": target,
            "inboundTargetLabel": label,
            "inboundRuleVersion": item.get("targetRuleVersion") or item.get("target_rule_version"),
            "inboundTargetWarning": item.get("targetWarning") or item.get("target_warning"),
            "isEndOperation": item.get("isEndOperation") if "isEndOperation" in item else item.get("is_end_operation"),
            "inboundQty": item.get("inboundQty") or item.get("inbound_qty"),
        })
    return lines


def find_shipment(shipment_id: int) -> dict[str, Any] | None:
    row = fetch_one(FIND_SQL, {"shipment_id": int(shipment_id)})
    return item_from_row(row) if row else None


def present(parsed: dict[str, str], items: list[dict[str, Any]]) -> dict[str, Any]:
    truncated = len(items) > ROW_LIMIT
    visible = items[:ROW_LIMIT]
    counts = {"仓库收货": 0, "仓储入库": 0}
    for item in visible:
        label = item.get("actionLabel") or ""
        if label in counts:
            counts[label] += 1
    scope = parsed.get("mold_batch") or parsed.get("mold_family") or "仓库回厂待办"
    summary = f"{scope} 共 {len(visible)} 条。"
    summary += "".join(f"\n- {label}：{count} 条" for label, count in counts.items() if count)
    if not visible:
        summary += "\n没有查到待仓库收货或待入库的成品发货单。"
    else:
        lines = []
        for item in visible[:30]:
            lines.append(
                f"{item['actionLabel']} {item['outsourceTypeLabel']} {item.get('moldNo') or ''} "
                f"{item.get('shipmentNo') or ''} 待收{item.get('pendingArrivalQty')} 待入{item.get('pendingInboundQty')}"
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
