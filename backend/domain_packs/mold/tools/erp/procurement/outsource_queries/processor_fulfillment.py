"""Read-only processor fulfillment: pending receipts and product-ship ready orders."""
from __future__ import annotations

import re
from typing import Any

from domain_packs.mold.erp.procurement.erp_outsource_db import fetch_all, fetch_one
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement.outsource_queries.buyer_todo import (
    item_matches_identity,
    mold_labels,
)

MOLD_FAMILY = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,})(?!-P\d+)(?![A-Z0-9])")
MOLD_BATCH = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,}-P\d+)(?![A-Z0-9])")
ROW_LIMIT = 100
OUTSOURCE_LABELS = {"part": "零件委外", "mold": "模具委外", "operation": "工序委外"}

RECEIPT_SQL = """
SELECT
    shipment.id AS shipment_id,
    shipment.shipment_no,
    shipment.order_id,
    order_row.order_no,
    lower(coalesce(order_row.outsource_type, project.outsource_type, 'part')) AS outsource_type,
    lower(coalesce(order_row.stage, '')) AS stage,
    supplier.id AS supplier_id,
    supplier.partner_name AS supplier_name,
    supplier.partner_code AS supplier_code,
    molds.mold_no,
    json_agg(
        json_build_object(
            'lineId', line.id,
            'orderPartId', line.order_part_id,
            'qty', line.qty,
            'receiptStatus', line.receipt_status,
            'partNo', part.part_no,
            'partName', part.part_name
        )
        ORDER BY line.id
    ) FILTER (WHERE lower(coalesce(line.receipt_status, '')) = 'pending') AS pending_lines
FROM entrust_material_shipments shipment
JOIN entrust_outsource_orders order_row ON order_row.id = shipment.order_id
LEFT JOIN entrust_projects project ON project.id = order_row.project_id
LEFT JOIN partner supplier ON supplier.id = order_row.supplier_id
LEFT JOIN entrust_material_shipment_lines line ON line.shipment_id = shipment.id
LEFT JOIN entrust_order_parts part ON part.id = line.order_part_id
LEFT JOIN LATERAL (
    SELECT string_agg(DISTINCT mold.name, '、' ORDER BY mold.name) AS mold_no
    FROM entrust_molds mold
    WHERE mold.project_id = order_row.project_id
) molds ON TRUE
WHERE lower(coalesce(order_row.status, '')) = 'open'
  AND lower(coalesce(shipment.delivery_status, '')) = 'shipped'
  AND lower(coalesce(order_row.outsource_type, project.outsource_type, 'part')) <> 'operation'
  AND (
        %(mold_batch)s = '' AND %(mold_family)s = ''
     OR (%(mold_batch)s <> '' AND upper(coalesce(molds.mold_no, '')) LIKE '%%' || %(mold_batch)s || '%%')
     OR (%(mold_family)s <> '' AND (
            upper(coalesce(molds.mold_no, '')) = %(mold_family)s
         OR upper(coalesce(molds.mold_no, '')) LIKE %(mold_family)s || '%%'
        ))
  )
GROUP BY
    shipment.id, shipment.shipment_no, shipment.order_id, order_row.order_no,
    order_row.outsource_type, project.outsource_type, order_row.stage,
    supplier.id, supplier.partner_name, supplier.partner_code, molds.mold_no
HAVING count(*) FILTER (WHERE lower(coalesce(line.receipt_status, '')) = 'pending') > 0
ORDER BY shipment.id DESC
LIMIT 200
"""

PRODUCT_SQL = """
SELECT
    order_row.id AS order_id,
    order_row.order_no,
    lower(coalesce(order_row.outsource_type, project.outsource_type, 'part')) AS outsource_type,
    lower(coalesce(order_row.stage, '')) AS stage,
    supplier.id AS supplier_id,
    supplier.partner_name AS supplier_name,
    supplier.partner_code AS supplier_code,
    molds.mold_no,
    json_agg(
        json_build_object(
            'orderPartId', part.id,
            'partNo', part.part_no,
            'partName', part.part_name,
            'moldCode', part.mold_code,
            'orderQty', part.order_qty,
            'materialRequired', part.material_required,
            'receivedQty', coalesce(received.qty, 0),
            'shippedQty', coalesce(shipped.qty, 0),
            'isFirstOperation', upload.is_first_operation,
            'isEndOperation', upload.is_end_operation,
            'remainQty', greatest(
                0,
                least(
                    coalesce(part.order_qty, 0) - coalesce(shipped.qty, 0),
                    CASE
                        WHEN lower(coalesce(order_row.outsource_type, project.outsource_type, 'part')) = 'operation'
                         AND NOT EXISTS (
                            SELECT 1
                            FROM entrust_material_supply_tasks supply
                            WHERE supply.order_id = order_row.id
                              AND lower(coalesce(supply.responsible_type, '')) = 'warehouse'
                              AND lower(coalesce(supply.source_type, '')) IN ('material_stock', 'semi_finished_stock')
                              AND lower(coalesce(supply.status, '')) = 'pending'
                         )
                        THEN coalesce(part.order_qty, 0) - coalesce(shipped.qty, 0)
                        WHEN coalesce(part.material_required::int, 0) <> 0
                        THEN coalesce(received.qty, 0) - coalesce(shipped.qty, 0)
                        ELSE coalesce(part.order_qty, 0) - coalesce(shipped.qty, 0)
                    END
                )
            )
        )
        ORDER BY part.id
    ) AS parts
FROM entrust_outsource_orders order_row
LEFT JOIN entrust_projects project ON project.id = order_row.project_id
LEFT JOIN partner supplier ON supplier.id = order_row.supplier_id
JOIN entrust_order_parts part ON part.order_id = order_row.id
LEFT JOIN LATERAL (
    SELECT coalesce(sum(
        CASE
            WHEN lower(coalesce(line.receipt_status, '')) IN ('received', 'partial')
            THEN coalesce(nullif(line.received_qty, 0), line.qty)
            ELSE 0
        END
    ), 0) AS qty
    FROM entrust_material_shipment_lines line
    JOIN entrust_material_shipments shipment ON shipment.id = line.shipment_id
    WHERE line.order_part_id = part.id
) received ON TRUE
LEFT JOIN LATERAL (
    SELECT coalesce(sum(greatest(
        coalesce(line.qty, 0)
        - coalesce(line.arrival_exception_qty, 0)
        - coalesce(line.return_qty, 0),
        0
    )), 0) AS qty
    FROM entrust_product_shipment_lines line
    WHERE line.order_part_id = part.id
) shipped ON TRUE
LEFT JOIN LATERAL (
    SELECT flags.is_first_operation, flags.is_end_operation
    FROM entrust_upload_lines flags
    JOIN entrust_upload_batches batch ON batch.id = flags.batch_id
    WHERE batch.project_id = order_row.project_id
      AND upper(btrim(flags.part_no)) = upper(btrim(part.part_no))
      AND (
            coalesce(btrim(part.mold_code), '') = ''
         OR upper(btrim(coalesce(flags.mold_code_norm, flags.mold_no, ''))) = upper(btrim(part.mold_code))
      )
    ORDER BY flags.id DESC
    LIMIT 1
) upload ON TRUE
LEFT JOIN LATERAL (
    SELECT string_agg(DISTINCT mold.name, '、' ORDER BY mold.name) AS mold_no
    FROM entrust_molds mold
    WHERE mold.project_id = order_row.project_id
) molds ON TRUE
WHERE lower(coalesce(order_row.status, '')) = 'open'
  AND (
        lower(coalesce(order_row.stage, '')) IN ('producing', 'shipping')
     OR (
            lower(coalesce(order_row.outsource_type, project.outsource_type, 'part')) = 'operation'
        AND lower(coalesce(order_row.stage, '')) IN ('accepted', 'material_receiving')
        AND NOT EXISTS (
            SELECT 1
            FROM entrust_material_supply_tasks supply
            WHERE supply.order_id = order_row.id
              AND lower(coalesce(supply.responsible_type, '')) = 'warehouse'
              AND lower(coalesce(supply.source_type, '')) IN ('material_stock', 'semi_finished_stock')
              AND lower(coalesce(supply.status, '')) = 'pending'
        )
     )
  )
  AND (
        %(mold_batch)s = '' AND %(mold_family)s = ''
     OR (%(mold_batch)s <> '' AND upper(coalesce(molds.mold_no, '')) LIKE '%%' || %(mold_batch)s || '%%')
     OR (%(mold_family)s <> '' AND (
            upper(coalesce(molds.mold_no, '')) = %(mold_family)s
         OR upper(coalesce(molds.mold_no, '')) LIKE %(mold_family)s || '%%'
        ))
  )
GROUP BY
    order_row.id, order_row.order_no, order_row.outsource_type, project.outsource_type,
    order_row.stage, supplier.id, supplier.partner_name, supplier.partner_code, molds.mold_no
ORDER BY order_row.id DESC
LIMIT 200
"""

WAIT_SQL = """
SELECT
    order_row.id AS order_id,
    order_row.order_no,
    lower(coalesce(order_row.outsource_type, project.outsource_type, 'part')) AS outsource_type,
    lower(coalesce(order_row.stage, '')) AS stage,
    supplier.id AS supplier_id,
    supplier.partner_name AS supplier_name,
    supplier.partner_code AS supplier_code,
    molds.mold_no
FROM entrust_outsource_orders order_row
LEFT JOIN entrust_projects project ON project.id = order_row.project_id
LEFT JOIN partner supplier ON supplier.id = order_row.supplier_id
LEFT JOIN LATERAL (
    SELECT string_agg(DISTINCT mold.name, '、' ORDER BY mold.name) AS mold_no
    FROM entrust_molds mold
    WHERE mold.project_id = order_row.project_id
) molds ON TRUE
WHERE lower(coalesce(order_row.status, '')) = 'open'
  AND lower(coalesce(order_row.stage, '')) IN ('accepted', 'material_receiving')
  AND EXISTS (
    SELECT 1
    FROM entrust_material_supply_tasks supply
    WHERE supply.order_id = order_row.id
      AND lower(coalesce(supply.responsible_type, '')) = 'warehouse'
      AND lower(coalesce(supply.source_type, '')) IN ('material_stock', 'semi_finished_stock')
      AND lower(coalesce(supply.status, '')) = 'pending'
  )
  AND (
        %(mold_batch)s = '' AND %(mold_family)s = ''
     OR (%(mold_batch)s <> '' AND upper(coalesce(molds.mold_no, '')) LIKE '%%' || %(mold_batch)s || '%%')
     OR (%(mold_family)s <> '' AND (
            upper(coalesce(molds.mold_no, '')) = %(mold_family)s
         OR upper(coalesce(molds.mold_no, '')) LIKE %(mold_family)s || '%%'
        ))
  )
ORDER BY order_row.id DESC
LIMIT 200
"""

FIND_SHIPMENT_SQL = """
SELECT
    shipment.id AS shipment_id,
    shipment.shipment_no,
    shipment.order_id,
    order_row.order_no,
    lower(coalesce(order_row.outsource_type, project.outsource_type, 'part')) AS outsource_type,
    lower(coalesce(order_row.stage, '')) AS stage,
    supplier.id AS supplier_id,
    supplier.partner_name AS supplier_name,
    supplier.partner_code AS supplier_code,
    molds.mold_no,
    json_agg(
        json_build_object(
            'lineId', line.id,
            'orderPartId', line.order_part_id,
            'qty', line.qty,
            'receiptStatus', line.receipt_status,
            'partNo', part.part_no,
            'partName', part.part_name
        )
        ORDER BY line.id
    ) FILTER (WHERE lower(coalesce(line.receipt_status, '')) = 'pending') AS pending_lines
FROM entrust_material_shipments shipment
JOIN entrust_outsource_orders order_row ON order_row.id = shipment.order_id
LEFT JOIN entrust_projects project ON project.id = order_row.project_id
LEFT JOIN partner supplier ON supplier.id = order_row.supplier_id
LEFT JOIN entrust_material_shipment_lines line ON line.shipment_id = shipment.id
LEFT JOIN entrust_order_parts part ON part.id = line.order_part_id
LEFT JOIN LATERAL (
    SELECT string_agg(DISTINCT mold.name, '、' ORDER BY mold.name) AS mold_no
    FROM entrust_molds mold
    WHERE mold.project_id = order_row.project_id
) molds ON TRUE
WHERE shipment.id = %(shipment_id)s
GROUP BY
    shipment.id, shipment.shipment_no, shipment.order_id, order_row.order_no,
    order_row.outsource_type, project.outsource_type, order_row.stage,
    supplier.id, supplier.partner_name, supplier.partner_code, molds.mold_no
"""


def parse_question(question: str) -> dict[str, str]:
    text = question or ""
    batch = MOLD_BATCH.search(text)
    family = None if batch else MOLD_FAMILY.search(text)
    tab = ""
    if any(word in text for word in ("收料", "收货", "来料")):
        tab = "receipt"
    elif any(word in text for word in ("成品发货", "发成品", "发货")):
        tab = "product_ship"
    return {
        "mold_family": "" if batch else (family.group(1).upper() if family else ""),
        "mold_batch": batch.group(1).upper() if batch else "",
        "tab": tab,
    }


def flag_tf(value: Any) -> str:
    if value is True:
        return "T"
    if value is False:
        return "F"
    return "未知"


PROCESSOR_INBOUND_RULE_VERSION = "processor-inbound-v1"
PROCESSOR_INBOUND_MISSING_END_OPERATION_WARNING = (
    "工序委外缺少末道工序标志，已按半成品库处理，请核对排产数据"
)


def inbound_target(*, outsource_type: str | None, is_end_operation: Any) -> dict[str, str | None]:
    """Same matrix as ERP describe_processor_inbound_target (processor-inbound-v1).

    part / mold → finished. Operation → finished only when is_end_operation is
    True. Null is not treated as False: a missing end flag still lands in the
    semi-finished store and carries a data-missing warning. Unknown types are
    never guessed as finished.
    """
    kind = str(outsource_type or "").strip().lower()
    if kind in {"part", "mold"} or (kind == "operation" and is_end_operation is True):
        target, label = "finished", "成品库"
    else:
        target, label = "semi_finished", "半成品库"
    warning = (
        PROCESSOR_INBOUND_MISSING_END_OPERATION_WARNING
        if kind == "operation" and is_end_operation is None
        else None
    )
    return {
        "target": target,
        "label": label,
        "ruleVersion": PROCESSOR_INBOUND_RULE_VERSION,
        "warning": warning,
    }


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _mentions(item: dict[str, Any], tokens: list[str] | None) -> bool:
    if tokens is None:
        return True
    if not tokens:
        return False
    values = {
        str(item.get("supplierId") or "").strip().casefold(),
        str(item.get("supplierCode") or "").strip().casefold(),
    }
    allowed = {str(token).strip().casefold() for token in tokens if str(token).strip()}
    return bool((values - {""}) & allowed)


def _stamp_identity(item: dict[str, Any]) -> dict[str, Any]:
    family, batch = mold_labels(item.get("moldNo"), item.get("parts") or item.get("pendingLines"))
    item["moldFamily"] = family
    item["moldBatch"] = batch
    nxt = dict(item.get("nextAction") or {})
    nxt.pop("orderId", None)
    if item.get("orderNo"):
        nxt["orderNo"] = item.get("orderNo")
        nxt["mold"] = family
        nxt["batch"] = batch
    item["nextAction"] = nxt
    return item


def receipt_item(row: dict[str, Any]) -> dict[str, Any]:
    outsource_type = str(row.get("outsource_type") or "part")
    lines = _as_list(row.get("pending_lines"))
    hint = "零件/模具委外：确认来料后才进入生产并成品发货。"
    item = {
        "action": "receipt",
        "actionLabel": "确认原料收货",
        "shipmentId": row.get("shipment_id"),
        "shipmentNo": row.get("shipment_no") or "",
        "orderId": row.get("order_id"),
        "orderNo": row.get("order_no") or "",
        "moldNo": row.get("mold_no") or "",
        "outsourceType": outsource_type,
        "outsourceTypeLabel": OUTSOURCE_LABELS.get(outsource_type, outsource_type),
        "stage": row.get("stage") or "",
        "supplierId": row.get("supplier_id"),
        "supplierName": row.get("supplier_name") or "",
        "supplierCode": row.get("supplier_code") or "",
        "pendingLines": lines,
        "hint": hint,
        "nextAction": {
            "action": "receipt",
            "shipmentNo": row.get("shipment_no") or "",
            "hint": hint,
        },
    }
    return _stamp_identity(item)


def product_item(row: dict[str, Any]) -> dict[str, Any] | None:
    outsource_type = str(row.get("outsource_type") or "part")
    parts = []
    for part in _as_list(row.get("parts")):
        remain = int(part.get("remainQty") or 0)
        if remain <= 0:
            continue
        target = inbound_target(outsource_type=outsource_type, is_end_operation=part.get("isEndOperation"))
        enriched = dict(part)
        enriched["remainQty"] = remain
        enriched["isFirstOperationLabel"] = flag_tf(part.get("isFirstOperation"))
        enriched["isEndOperationLabel"] = flag_tf(part.get("isEndOperation"))
        enriched["inboundTarget"] = target["target"]
        enriched["inboundTargetLabel"] = target["label"]
        enriched["inboundRuleVersion"] = target["ruleVersion"]
        enriched["inboundTargetWarning"] = target["warning"]
        parts.append(enriched)
    if not parts:
        return None
    targets = list(dict.fromkeys(part["inboundTargetLabel"] for part in parts if part.get("inboundTarget")))
    warnings = [part["inboundTargetWarning"] for part in parts if part.get("inboundTargetWarning")]
    hint = (
        "可发数量不超过已收原料。入库目标按 ERP processor-inbound-v1："
        "零件/模具进成品库；工序仅明确末道进成品库，非末道或末道缺失进半成品库。"
        "下一步由仓库按发货行快照入库确认。"
    )
    if warnings:
        hint = f"{hint} {'；'.join(dict.fromkeys(warnings))}"
    item = {
        "action": "product_ship",
        "actionLabel": "成品发货",
        "orderId": row.get("order_id"),
        "orderNo": row.get("order_no") or "",
        "moldNo": row.get("mold_no") or "",
        "outsourceType": outsource_type,
        "outsourceTypeLabel": OUTSOURCE_LABELS.get(outsource_type, outsource_type),
        "stage": row.get("stage") or "",
        "supplierId": row.get("supplier_id"),
        "supplierName": row.get("supplier_name") or "",
        "supplierCode": row.get("supplier_code") or "",
        "inboundTargets": targets,
        "inboundTargetWarnings": list(dict.fromkeys(warnings)),
        "inboundRuleVersion": PROCESSOR_INBOUND_RULE_VERSION,
        "parts": parts,
        "hint": hint,
        "nextAction": {
            "action": "product_ship",
            "hint": hint,
        },
    }
    return _stamp_identity(item)


def wait_item(row: dict[str, Any]) -> dict[str, Any]:
    outsource_type = str(row.get("outsource_type") or "part")
    hint = (
        "工序委外：等仓库备料完成。备料完成后不用确认收货，直接成品发货。"
        if outsource_type == "operation"
        else "零件/模具委外：等仓库原料发货后，再确认来料。"
    )
    item = {
        "action": "wait",
        "actionLabel": "等待仓库",
        "orderId": row.get("order_id"),
        "orderNo": row.get("order_no") or "",
        "moldNo": row.get("mold_no") or "",
        "outsourceType": outsource_type,
        "outsourceTypeLabel": OUTSOURCE_LABELS.get(outsource_type, outsource_type),
        "stage": row.get("stage") or "",
        "supplierId": row.get("supplier_id"),
        "supplierName": row.get("supplier_name") or "",
        "supplierCode": row.get("supplier_code") or "",
        "hint": hint,
        "nextAction": {"action": "wait", "hint": hint},
    }
    return _stamp_identity(item)


def query_items(parsed: dict[str, str], *, processor_tokens: list[str] | None = None) -> list[dict[str, Any]]:
    scoped = {
        "mold_family": parsed.get("mold_family") or "",
        "mold_batch": parsed.get("mold_batch") or "",
    }
    tab = parsed.get("tab") or ""
    items: list[dict[str, Any]] = []
    if tab in {"", "receipt"}:
        for row in fetch_all(RECEIPT_SQL, scoped):
            item = receipt_item(row)
            if _mentions(item, processor_tokens):
                items.append(item)
    if tab in {"", "product_ship"}:
        for row in fetch_all(PRODUCT_SQL, scoped):
            item = product_item(row)
            if item and _mentions(item, processor_tokens):
                items.append(item)
    if tab == "":
        for row in fetch_all(WAIT_SQL, scoped):
            item = wait_item(row)
            if _mentions(item, processor_tokens):
                items.append(item)
    return items


def find_shipment(shipment_id: int) -> dict[str, Any] | None:
    row = fetch_one(FIND_SHIPMENT_SQL, {"shipment_id": int(shipment_id)})
    if not row:
        return None
    return receipt_item(row)


def query_product_items(parsed: dict[str, str], *, processor_tokens: list[str] | None = None) -> list[dict[str, Any]]:
    scoped = {
        "mold_family": parsed.get("mold_family") or "",
        "mold_batch": parsed.get("mold_batch") or "",
    }
    items: list[dict[str, Any]] = []
    for row in fetch_all(PRODUCT_SQL, scoped):
        item = product_item(row)
        if item and _mentions(item, processor_tokens):
            items.append(item)
    return items


def find_product_order(order_id: int, *, mold: str | None = None) -> dict[str, Any] | None:
    parsed = parse_question(mold or "")
    for item in query_product_items(parsed):
        if item.get("orderId") == order_id:
            return item
    return None


def find_product_order_by_identity(
    *,
    order_no: str | None = None,
    mold: str | None = None,
    batch: str | None = None,
) -> dict[str, Any] | None:
    order_no = str(order_no or "").strip()
    mold = str(mold or "").strip()
    batch = str(batch or "").strip()
    if not order_no and not mold and not batch:
        raise DomainError("INVALID_TOOL_INPUT", "请用订单号、模具号和批次号定位，不要使用内部数字编号")
    parsed = parse_question(" ".join(part for part in (batch, mold) if part))
    hits = [
        item
        for item in query_product_items(parsed)
        if item_matches_identity(item, order_no=order_no, mold=mold, batch=batch)
    ]
    if order_no and not hits and (batch or mold):
        hits = [
            item
            for item in query_product_items(parse_question(""))
            if item_matches_identity(item, order_no=order_no, mold=mold, batch=batch)
        ]
    if not hits:
        return None
    if len(hits) > 1:
        raise DomainError("AMBIGUOUS", "同一条件命中多张可发货工单，请同时提供订单号、模具号和批次号", 409)
    return hits[0]


def present(parsed: dict[str, str], items: list[dict[str, Any]]) -> dict[str, Any]:
    truncated = len(items) > ROW_LIMIT
    visible = items[:ROW_LIMIT]
    counts = {"确认原料收货": 0, "成品发货": 0, "等待仓库": 0}
    for item in visible:
        label = item.get("actionLabel") or ""
        if label in counts:
            counts[label] += 1
    scope = parsed.get("mold_batch") or parsed.get("mold_family") or "加工商履约待办"
    summary = f"{scope} 共 {len(visible)} 条。"
    summary += "".join(f"\n- {label}：{count} 条" for label, count in counts.items() if count)
    if not visible:
        summary += "\n本次查询结果是 0 条。"
    else:
        lines = []
        for item in visible[:30]:
            lines.append(
                f"{item['actionLabel']} {item['outsourceTypeLabel']} {item.get('moldNo') or ''} "
                f"{item.get('orderNo') or item.get('shipmentNo') or ''}"
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


def run(parsed: dict[str, str], *, processor_tokens: list[str] | None = None) -> dict[str, Any]:
    return present(parsed, query_items(parsed, processor_tokens=processor_tokens))


def run_product(parsed: dict[str, str], *, processor_tokens: list[str] | None = None) -> dict[str, Any]:
    scoped = dict(parsed)
    scoped["tab"] = "product_ship"
    return present(scoped, query_items(scoped, processor_tokens=processor_tokens))
