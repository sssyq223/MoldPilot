"""Call management-system HTTP write APIs with the current user's ERP token."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.config import settings
from domain_packs.mold.erp_adapter import ERPClient, decrypt
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.errors import DomainError


def call_erp(
    db,
    user,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not settings().erp_base_url:
        raise DomainError("ERP_NOT_CONFIGURED", "管理员尚未配置 ERP 服务地址", 503)
    identity = db.get(m.ERPIdentity, user.id)
    if not identity or not identity.token_ciphertext:
        raise DomainError("ERP_LOGIN_REQUIRED", "请先验证本人的 ERP 账号后再确认办理", 401)
    client = ERPClient(decrypt(identity.token_ciphertext))
    try:
        kwargs: dict[str, Any] = {"json": body or {}}
        if params:
            kwargs["params"] = params
        return client.request(method.upper(), path.lstrip("/"), **kwargs)
    finally:
        close = getattr(client, "close", None)
        if close:
            close()


def post_erp(
    db,
    user,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    *,
    intent_id: str | None = None,
    action: str | None = None,
    native_id: str | None = None,
) -> dict[str, Any]:
    if intent_id or action or native_id:
        # A confirmed business write must never fall back to a bare call:
        # dispatch_erp fails closed when the confirm flow forgot the intent id.
        return dispatch_erp(
            db, user, intent_id=intent_id or "", action=action or path,
            native_id=native_id or path, method="POST", path=path, body=body, params=params,
        )
    return call_erp(db, user, "POST", path, body, params)


def put_erp(
    db,
    user,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    *,
    intent_id: str | None = None,
    action: str | None = None,
    native_id: str | None = None,
) -> dict[str, Any]:
    if intent_id or action or native_id:
        return dispatch_erp(
            db, user, intent_id=intent_id or "", action=action or path,
            native_id=native_id or path, method="PUT", path=path, body=body, params=params,
        )
    return call_erp(db, user, "PUT", path, body, params)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def dispatch_erp(
    db,
    user,
    *,
    intent_id: str,
    action: str,
    native_id: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist an idempotent dispatch receipt around one confirmed ERP write."""
    if not intent_id:
        raise DomainError("CONFIRMATION_INVALID", "确认操作缺少幂等标识，请重新准备", 409)
    identity = db.get(m.ERPIdentity, user.id)
    if not identity or not identity.token_ciphertext:
        raise DomainError("ERP_LOGIN_REQUIRED", "请先验证本人的 ERP 账号后再确认办理", 401)
    request_hash = content_hash({
        "method": method.upper(),
        "path": path.lstrip("/"),
        "body": body or {},
        "params": params or {},
    })
    operation = db.scalar(
        select(m.ERPOperation).where(m.ERPOperation.intent_id == intent_id).with_for_update()
    )
    if operation is not None:
        if operation.request_hash != request_hash:
            raise DomainError("IDEMPOTENCY_CONFLICT", "同一次确认的 ERP 请求内容已变化", 409)
        if operation.state == "SUCCEEDED":
            return operation.response or {}
        if operation.state == "DISPATCHING":
            raise DomainError(
                "ERP_OUTCOME_UNKNOWN",
                "同一次确认正在提交 ERP 或上次提交中断；请稍后重新查询 ERP 状态，不会自动重复正式操作",
                502,
            )
        if operation.state == "UNKNOWN":
            raise DomainError(
                "ERP_OUTCOME_UNKNOWN",
                "上次 ERP 提交结果仍未知；请先重新查询 ERP 状态，不会自动重复正式操作",
                502,
            )
        raise DomainError(
            operation.error_code or "ERP_BUSINESS_REJECTED",
            "ERP 已拒绝同一次确认请求，请重新查询业务状态后准备",
            409,
        )
    unresolved = db.scalar(
        select(m.ERPOperation)
        .where(
            m.ERPOperation.action == action,
            m.ERPOperation.native_id == str(native_id),
            m.ERPOperation.state.in_(("DISPATCHING", "UNKNOWN")),
        )
        .order_by(m.ERPOperation.created_at.desc())
        .limit(1)
    )
    if unresolved is not None:
        raise DomainError(
            "ERP_OUTCOME_UNKNOWN",
            "该 ERP 对象存在结果未知的同类操作；请先查询 ERP 状态或由管理员核销，不能重新提交",
            502,
        )

    operation = m.ERPOperation(
        user_id=user.id,
        intent_id=intent_id,
        action=action,
        native_id=str(native_id),
        state="DISPATCHING",
        request_hash=request_hash,
        erp_user_id=str(identity.erp_user_id),
    )
    db.add(operation)
    db.commit()
    try:
        response = call_erp(db, user, method, path, body, params)
    except DomainError as error:
        operation.state = "UNKNOWN" if error.code == "ERP_OUTCOME_UNKNOWN" else "REJECTED"
        operation.error_code = error.code
        db.commit()
        raise
    operation.state = "SUCCEEDED"
    operation.response = _json_value(response)
    operation.error_code = None
    db.commit()
    return operation.response
