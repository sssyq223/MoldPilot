"""Read-only pending outsource-order approval tasks from ERP workflow tables."""
from __future__ import annotations

import re
from typing import Any

from domain_packs.mold.erp.procurement.erp_outsource_db import fetch_all, fetch_one
from domain_packs.mold.tools.erp.procurement.outsource_queries.buyer_todo import mold_labels

MOLD_FAMILY = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,})(?!-P\d+)(?![A-Z0-9])")
MOLD_BATCH = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,}-P\d+)(?![A-Z0-9])")
ROW_LIMIT = 100

SQL = """
SELECT
    task.id AS task_id,
    task.node_name,
    task.assignee_id,
    task.assignee_name,
    task.business_id AS order_id,
    task.business_no,
    task.title,
    instance.id AS instance_id,
    instance.current_node_name,
    instance.process_code,
    order_row.order_no,
    order_row.total_amount,
    order_row.stage,
    supplier.partner_name AS supplier_name,
    lower(coalesce(project.outsource_type, '')) AS outsource_type,
    molds.mold_no
FROM wf_todo_task task
JOIN wf_process_instance instance ON instance.id = task.instance_id
LEFT JOIN entrust_outsource_orders order_row ON order_row.id = task.business_id
LEFT JOIN entrust_projects project ON project.id = order_row.project_id
LEFT JOIN partner supplier ON supplier.id = order_row.supplier_id
LEFT JOIN LATERAL (
    SELECT string_agg(DISTINCT code, '、' ORDER BY code) AS mold_no
    FROM (
        SELECT upper(btrim(mold.name)) AS code
        FROM entrust_molds mold
        WHERE mold.project_id = project.id
        UNION
        SELECT upper(btrim(part.mold_code)) AS code
        FROM entrust_order_parts part
        WHERE part.order_id = order_row.id
          AND coalesce(btrim(part.mold_code), '') <> ''
    ) codes
    WHERE coalesce(code, '') <> ''
) molds ON TRUE
WHERE coalesce(task.is_deleted, 0) = 0
  AND lower(coalesce(task.status, '')) = 'pending'
  AND lower(coalesce(task.business_type, '')) = 'outsource_order'
  AND coalesce(instance.is_deleted, 0) = 0
  AND lower(coalesce(instance.status, '')) = 'pending'
  AND lower(coalesce(instance.process_code, '')) = 'outsource_order_approval'
  AND (
        %(node_filter)s = ''
     OR task.node_name LIKE '%%' || %(node_filter)s || '%%'
  )
  AND (
        %(mold_batch)s = '' AND %(mold_family)s = ''
     OR (%(mold_batch)s <> '' AND upper(coalesce(molds.mold_no, '')) LIKE '%%' || %(mold_batch)s || '%%')
     OR (%(mold_family)s <> '' AND (
            upper(coalesce(molds.mold_no, '')) = %(mold_family)s
         OR upper(coalesce(molds.mold_no, '')) LIKE %(mold_family)s || '%%'
        ))
  )
ORDER BY task.created_at DESC
LIMIT 200
"""


def parse_question(question: str) -> dict[str, str]:
    text = question or ""
    batch = MOLD_BATCH.search(text)
    family = None if batch else MOLD_FAMILY.search(text)
    return {
        "mold_family": "" if batch else (family.group(1).upper() if family else ""),
        "mold_batch": batch.group(1).upper() if batch else "",
    }


def item_from_row(row: dict[str, Any]) -> dict[str, Any]:
    task_id = row.get("task_id")
    mold_no = row.get("mold_no") or ""
    if not mold_no:
        text = " ".join(str(piece or "") for piece in (row.get("title"), row.get("business_no"), row.get("order_no")))
        batch = MOLD_BATCH.search(text)
        family = MOLD_FAMILY.search(text)
        mold_no = (batch.group(1) if batch else family.group(1) if family else "") or ""
    mold_family, mold_batch = mold_labels(mold_no)
    return {
        "taskId": task_id,
        "instanceId": row.get("instance_id"),
        "orderId": row.get("order_id"),
        "orderNo": row.get("order_no") or row.get("business_no") or "",
        "title": row.get("title") or "",
        "nodeName": row.get("node_name") or row.get("current_node_name") or "",
        "assigneeName": row.get("assignee_name") or "",
        "moldNo": mold_no,
        "moldFamily": mold_family,
        "moldBatch": mold_batch,
        "outsourceType": row.get("outsource_type") or "",
        "supplierName": row.get("supplier_name") or "",
        "amount": float(row["total_amount"]) if row.get("total_amount") is not None else None,
        "stage": row.get("stage") or "",
        "nextAction": {
            "action": "pass_or_reject",
            "orderNo": row.get("order_no") or row.get("business_no") or "",
            "mold": mold_family,
            "batch": mold_batch,
            "hint": "只批自己节点。用订单号定位，必要时加模具号和批次号。禁止使用内部数字 id。",
        },
    }


def query_items(*, node_tokens: list[str] | None = None, mold_family: str = "", mold_batch: str = "") -> list[dict[str, Any]]:
    if node_tokens == []:
        return []
    if node_tokens:
        items: list[dict[str, Any]] = []
        seen: set[int] = set()
        for token in node_tokens:
            for row in fetch_all(SQL, {
                "node_filter": token,
                "mold_family": mold_family,
                "mold_batch": mold_batch,
            }):
                item = item_from_row(row)
                task_id = item.get("taskId")
                if task_id in seen:
                    continue
                seen.add(task_id)
                items.append(item)
        return items
    return [
        item_from_row(row)
        for row in fetch_all(SQL, {
            "node_filter": "",
            "mold_family": mold_family,
            "mold_batch": mold_batch,
        })
    ]


FIND_SQL = """
SELECT
    task.id AS task_id,
    task.node_name,
    task.assignee_id,
    task.assignee_name,
    task.business_id AS order_id,
    task.business_no,
    task.title,
    instance.id AS instance_id,
    instance.current_node_name,
    instance.process_code,
    order_row.order_no,
    order_row.total_amount,
    order_row.stage,
    supplier.partner_name AS supplier_name,
    lower(coalesce(project.outsource_type, '')) AS outsource_type,
    molds.mold_no
FROM wf_todo_task task
JOIN wf_process_instance instance ON instance.id = task.instance_id
LEFT JOIN entrust_outsource_orders order_row ON order_row.id = task.business_id
LEFT JOIN entrust_projects project ON project.id = order_row.project_id
LEFT JOIN partner supplier ON supplier.id = order_row.supplier_id
LEFT JOIN LATERAL (
    SELECT string_agg(DISTINCT code, '、' ORDER BY code) AS mold_no
    FROM (
        SELECT upper(btrim(mold.name)) AS code
        FROM entrust_molds mold
        WHERE mold.project_id = project.id
        UNION
        SELECT upper(btrim(part.mold_code)) AS code
        FROM entrust_order_parts part
        WHERE part.order_id = order_row.id
          AND coalesce(btrim(part.mold_code), '') <> ''
    ) codes
    WHERE coalesce(code, '') <> ''
) molds ON TRUE
WHERE task.id = %(task_id)s
  AND coalesce(task.is_deleted, 0) = 0
  AND lower(coalesce(task.status, '')) = 'pending'
  AND lower(coalesce(instance.process_code, '')) = 'outsource_order_approval'
"""


def find_task(task_id: int) -> dict[str, Any] | None:
    row = fetch_one(FIND_SQL, {"task_id": task_id})
    return item_from_row(row) if row else None


def find_task_by_identity(
    *,
    order_no: str | None = None,
    mold: str | None = None,
    batch: str | None = None,
    node_tokens: list[str] | None = None,
) -> dict[str, Any] | None:
    from domain_packs.mold.ports.errors import DomainError
    from domain_packs.mold.tools.erp.procurement.outsource_queries.buyer_todo import item_matches_identity

    order_no = str(order_no or "").strip()
    mold = str(mold or "").strip()
    batch = str(batch or "").strip()
    if not order_no and not mold and not batch:
        raise DomainError("INVALID_TOOL_INPUT", "请用订单号、模具号和批次号定位，不要使用内部数字编号")
    hits = [
        item
        for item in query_items(node_tokens=node_tokens)
        if item_matches_identity(item, order_no=order_no, mold=mold, batch=batch)
    ]
    if not hits:
        return None
    if len(hits) > 1:
        raise DomainError("AMBIGUOUS", "同一条件命中多张审批待办，请同时提供订单号、模具号和批次号", 409)
    return hits[0]


def present(items: list[dict[str, Any]], *, node_tokens: list[str] | None) -> dict[str, Any]:
    visible = items[:ROW_LIMIT]
    lens = "全部节点" if node_tokens is None else ("、".join(node_tokens) or "未分配审批节点")
    summary = f"委外下单审批待办（{lens}）共 {len(visible)} 条。"
    if not visible:
        summary += "\n本次查询结果是 0 条。"
    else:
        lines = [
            f"{item['nodeName']} {item['moldNo']} {item['orderNo']} {item['supplierName']} {item['amount'] or ''}".strip()
            for item in visible[:30]
        ]
        summary += "\n" + "\n".join(f"- {line}" for line in lines if line)
    return {
        "nodeLens": ["all"] if node_tokens is None else node_tokens,
        "summary": summary,
        "truncated": len(items) > ROW_LIMIT,
        "items": visible,
    }


def run(*, node_tokens: list[str] | None = None, question: str = "") -> dict[str, Any]:
    parsed = parse_question(question)
    items = query_items(
        node_tokens=node_tokens,
        mold_family=parsed["mold_family"],
        mold_batch=parsed["mold_batch"],
    )
    return present(items, node_tokens=node_tokens)
