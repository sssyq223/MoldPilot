"""Read-only unoutsourced work-order / part query."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from domain_packs.mold.erp.procurement.erp_outsource_db import fetch_all

MOLD_FAMILY = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,})(?!-P\d+)(?![A-Z0-9])")
MOLD_BATCH = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,}-P\d+)(?![A-Z0-9])")
WORK_ORDER = re.compile(r"(?i)(?<![A-Z0-9])(WO\d+)(?![A-Z0-9])")
PROJECT_NO = re.compile(r"(?i)(?<![A-Z0-9])(E\d+-\d+|ENT-BATCH-[A-Z0-9]+)(?![A-Z0-9])")
PART_NO = re.compile(r"(?i)(?<![A-Z0-9])([A-Z]{1,6}\d*[A-Z]?-\d+[A-Z]?)(?![A-Z0-9])")
ROW_LIMIT = 200

SQL = """
SELECT
    coalesce(wo.order_no, '') AS order_no,
    project.project_no,
    em.name AS mold_no,
    ep.part_no,
    ep.part_name,
    ep.qty,
    ep.spec,
    ep.unit_price,
    ep.total_amount,
    ep.accounting_process,
    inquiry.our_quote_amount
FROM entrust_projects project
JOIN entrust_molds em ON em.project_id = project.id
JOIN entrust_parts ep ON ep.mold_id = em.id
JOIN LATERAL (
    SELECT request_row.our_quote_amount, request_row.auto_accept_max_amount
    FROM entrust_outsource_requests request_row
    WHERE request_row.project_id = project.id
      AND coalesce(request_row.inquiry_type, 'normal') <> 'reflow'
      AND coalesce(request_row.status, 'draft') IN
          ('draft', 'sent', 'quoted', 'awarded', 'partial_awarded', 'closed')
    ORDER BY
        CASE coalesce(request_row.status, 'draft')
            WHEN 'awarded' THEN 0
            WHEN 'partial_awarded' THEN 0
            WHEN 'quoted' THEN 1
            WHEN 'sent' THEN 2
            ELSE 3
        END,
        request_row.id
    LIMIT 1
) inquiry ON TRUE
LEFT JOIN work_order wo
  ON coalesce(wo.is_deleted, 0) = 0
 AND upper(trim(wo.mold_no)) = upper(trim(em.name))
 AND upper(trim(wo.part_no)) = upper(trim(ep.part_no))
WHERE lower(coalesce(project.status, '')) IN ('confirmed', 'in_progress')
  AND (
        %(part_no)s <> ''
     OR lower(coalesce(project.outsource_type, '')) IN ('part', 'mold')
  )
  AND (
        %(part_no)s <> ''
     OR inquiry.our_quote_amount IS NULL
     OR inquiry.auto_accept_max_amount IS NULL
  )
  AND (%(order_no)s = '' OR upper(trim(coalesce(wo.order_no, ''))) = %(order_no)s)
  AND (%(mold_batch)s = '' OR upper(trim(em.name)) = %(mold_batch)s)
  AND (%(mold_family)s = '' OR upper(trim(em.name)) = %(mold_family)s
       OR upper(trim(em.name)) LIKE %(mold_family_like)s)
  AND (%(part_no)s = '' OR upper(trim(ep.part_no)) = %(part_no)s)
ORDER BY em.name, ep.part_no, project.project_no
LIMIT %(limit)s
"""


def _part_no(text: str) -> str:
    leftover = PROJECT_NO.sub(" ", MOLD_BATCH.sub(" ", MOLD_FAMILY.sub(" ", WORK_ORDER.sub(" ", text or ""))))
    match = PART_NO.search(leftover)
    return match.group(1).upper() if match else ""


def parse_question(question: str) -> dict[str, str]:
    text = question or ""
    batch = MOLD_BATCH.search(text)
    family = None if batch else MOLD_FAMILY.search(text)
    order = WORK_ORDER.search(text)
    part_no = _part_no(text)
    asks_detail = any(word in text for word in ("详细", "明细信息", "零件信息"))
    asks_parts = "零件" in text or bool(part_no) or asks_detail
    asks_orders = "订单" in text or "工单" in text
    if asks_parts:
        mode = "parts"
    elif asks_orders or not (batch or family or order):
        mode = "orders"
    else:
        mode = "parts"
    return {
        "mode": mode,
        "mold_family": "" if batch else (family.group(1).upper() if family else ""),
        "mold_batch": batch.group(1).upper() if batch else "",
        "order_no": order.group(1).upper() if order else "",
        "part_no": part_no,
    }


def query_rows(*, mold_family: str = "", mold_batch: str = "", order_no: str = "", part_no: str = "") -> list[dict[str, Any]]:
    family = mold_family.strip().upper()
    scoped = bool(family or mold_batch.strip() or order_no.strip() or part_no.strip())
    return fetch_all(
        SQL,
        {
            "mold_family": family,
            "mold_family_like": f"{family}-P%" if family else "",
            "mold_batch": mold_batch.strip().upper(),
            "order_no": order_no.strip().upper(),
            "part_no": part_no.strip().upper(),
            "limit": 2000 if scoped else ROW_LIMIT + 1,
        },
    )


def _scope_label(parsed: dict[str, str]) -> str:
    part = parsed.get("part_no") or ""
    if parsed["order_no"]:
        return f"工单 {parsed['order_no']}"
    if parsed["mold_batch"] and part:
        return f"模具批次 {parsed['mold_batch']} 的零件 {part}"
    if parsed["mold_family"] and part:
        return f"模具 {parsed['mold_family']} 的零件 {part}"
    if part:
        return f"零件 {part}"
    if parsed["mold_batch"]:
        return f"模具批次 {parsed['mold_batch']}"
    if parsed["mold_family"]:
        return f"模具 {parsed['mold_family']} 的全部批次"
    return "采购待办中未填价格上下限的项目"


def _json_number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        number = float(value)
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
    return int(number) if number.is_integer() else round(number, 2)


def _part_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "moldNo": row.get("mold_no") or "",
        "partNo": row.get("part_no") or "",
        "partName": row.get("part_name") or "",
        "orderNo": row.get("order_no") or "",
        "projectNo": row.get("project_no") or "",
        "qty": _json_number(row.get("qty")),
        "spec": row.get("spec") or "",
        "process": row.get("accounting_process") or "",
        "unitPrice": _json_number(row.get("unit_price")),
        "totalAmount": _json_number(row.get("total_amount")),
        "ourQuoteAmount": _json_number(row.get("our_quote_amount")),
    }


def present(parsed: dict[str, str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    truncated = len(rows) > ROW_LIMIT
    visible = rows[:ROW_LIMIT]
    if parsed["mode"] == "parts":
        seen: set[tuple[str, str]] = set()
        parts = []
        batches: dict[str, int] = {}
        for row in rows:
            key = (str(row.get("mold_no") or ""), str(row.get("part_no") or ""))
            if key in seen:
                continue
            seen.add(key)
            batches[key[0]] = batches.get(key[0], 0) + 1
            parts.append(_part_item(row))
        total = len(parts)
        listed = parts[:ROW_LIMIT]
        truncated = total > ROW_LIMIT
        if parsed.get("part_no"):
            if listed:
                item = listed[0]
                amount = item.get("totalAmount")
                summary = (
                    f"{_scope_label(parsed)}：{item['partName'] or item['partNo']}"
                    f"{'，工艺 ' + item['process'] if item.get('process') else ''}"
                    f"{'，规格 ' + item['spec'] if item.get('spec') else ''}"
                    f"{'，核算价 ' + str(amount) if amount is not None else ''}"
                    f"{'，项目 ' + item['projectNo'] if item.get('projectNo') else ''}。"
                )
            else:
                summary = f"{_scope_label(parsed)} 没有查到待填价明细。"
        else:
            lines = [f"{item['moldNo']} / {item['partNo']} {item['partName']}".strip() for item in listed[:40]]
            summary = f"{_scope_label(parsed)} 还有 {total} 个零件未委外"
            if batches:
                summary += f"，与 ERP 采购待办待填价一致，分布在 {len(batches)} 个模具批次。"
                summary += "\n" + "\n".join(f"- {mold}：{count} 个" for mold, count in sorted(batches.items()))
            else:
                summary += "。"
            if lines:
                summary += "\n" + "\n".join(f"- {line}" for line in lines)
            else:
                summary += "没有查到仍待填价的未委外零件。"
        payload: dict[str, Any] = {"parts": listed, "batchCounts": batches, "total": total, "partNo": parsed.get("part_no") or ""}
    else:
        orders = [_part_item(row) for row in visible]
        batches: dict[str, int] = {}
        for item in orders:
            batches[item["moldNo"]] = batches.get(item["moldNo"], 0) + 1
        summary = f"{_scope_label(parsed)} 还有 {len(orders)} 条未委外明细，分布在 {len(batches)} 个模具批次。"
        summary += (
            "\n" + "\n".join(f"- {mold}：{count} 条" for mold, count in sorted(batches.items()))
            if batches else "没有查到仍待填价的未委外明细。"
        )
        payload = {"orders": orders, "batchCounts": batches}
    if truncated:
        summary += f"\n结果超过 {ROW_LIMIT} 条，只返回前 {ROW_LIMIT} 条。请缩小到模具或批次后再查。"
    return {
        "mode": parsed["mode"],
        "scope": _scope_label(parsed),
        "database": "erp",
        "definition": "与 ERP 采购人员待办·待填价一致：已建零件/模具委外项目，且我方报价或接单上限未填。零件台账即使已标 outsourcing，未填价格上下限仍算未委外。",
        "truncated": truncated,
        "summary": summary,
        **payload,
    }


def run(parsed: dict[str, str]) -> dict[str, Any]:
    return present(
        parsed,
        query_rows(
            mold_family=parsed["mold_family"],
            mold_batch=parsed["mold_batch"],
            order_no=parsed["order_no"],
            part_no=parsed.get("part_no") or "",
        ),
    )
