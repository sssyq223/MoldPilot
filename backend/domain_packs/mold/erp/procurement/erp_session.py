"""Workbench-bound ERP HTTP session: captcha + login, then store encrypted token."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import Field, field_validator
from sqlalchemy import select
from urllib.parse import urlsplit

from agent_core.host_ports import host_ports
from agent_core.schemas import StrictModel
from domain_packs.mold import models as m
from domain_packs.mold.config import settings
from domain_packs.mold.erp_adapter import (
    ERPClient,
    decrypt,
    discard_unreadable_erp_token,
    encrypt,
    erp_token_readable,
)
from domain_packs.mold.ports.errors import DomainError

router = APIRouter(prefix="/api/erp-session", tags=["ERP session"])
_host = host_ports()


class ErpLoginInput(StrictModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=80)
    code: str | None = Field(default=None, max_length=16)
    uuid: str | None = Field(default=None, max_length=80)

    @field_validator("username", "password", "code", "uuid")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


def _erp_http() -> httpx.Client:
    config = settings()
    parts = urlsplit(config.erp_base_url)
    environment = _host.settings().environment
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise DomainError("ERP_NOT_CONFIGURED", "管理员尚未配置有效 ERP 服务地址", 503)
    if parts.scheme != "https" and not (
        parts.scheme == "http" and config.erp_allow_insecure_local and environment != "production"
    ):
        raise DomainError("ERP_HTTPS_REQUIRED", "ERP 连接需要 HTTPS；本地测试例外必须显式配置", 503)
    return httpx.Client(
        base_url=config.erp_base_url.rstrip("/") + "/",
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(20, connect=5),
    )


def _login_token(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    token = payload.get("token")
    data = payload.get("data")
    if not token and isinstance(data, dict):
        token = data.get("token") or data.get("access_token")
    if not token:
        token = payload.get("access_token")
    return str(token or "").strip()


def _login_reject_message(payload: dict[str, Any] | None) -> str:
    if isinstance(payload, dict):
        detail = str(payload.get("msg") or "").strip()
        if detail:
            return detail
        errors = payload.get("detail")
        if isinstance(errors, list) and errors:
            return "ERP 登录接口未接受本次请求格式，请稍后重试或联系管理员。"
    return "ERP 未接受本次登录。请用 ERP 登录名（如 xuguili）和 ERP 密码，验证码填算式得数。"


def _erp_user_id(payload: dict[str, Any]) -> str:
    user = payload.get("user") if isinstance(payload.get("user"), dict) else None
    if user is None and isinstance(payload.get("data"), dict):
        nested = payload["data"]
        user = nested.get("user") if isinstance(nested.get("user"), dict) else nested
    source = user or payload
    for key in ("userId", "user_id", "userID"):
        value = source.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    raise DomainError("ERP_PROTOCOL_ERROR", "ERP 未返回有效用户编号", 502)


def _identity_status(identity: m.ERPIdentity | None) -> dict[str, Any]:
    readable = bool(identity and erp_token_readable(identity.token_ciphertext))
    return {
        "bound": bool(identity and identity.erp_user_id),
        "authenticated": readable,
        "authenticated_at": identity.authenticated_at.isoformat() if identity and identity.authenticated_at and readable else None,
        "configured": bool(settings().erp_base_url),
    }


@router.get("/status")
def erp_session_status(user=Depends(_host.current_user), db=Depends(_host.get_db)):
    identity = db.get(m.ERPIdentity, user.id)
    if discard_unreadable_erp_token(identity):
        db.commit()
    return _identity_status(identity)


@router.get("/captcha")
def erp_captcha(user=Depends(_host.current_user)):
    del user
    client = _erp_http()
    try:
        response = client.get("captchaImage")
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise DomainError("ERP_OUTCOME_UNKNOWN", "无法获取 ERP 验证码", 502) from error
    finally:
        client.close()
    if not isinstance(payload, dict) or not payload.get("uuid"):
        raise DomainError("ERP_PROTOCOL_ERROR", "ERP 验证码响应无效", 502)
    return {
        "uuid": payload.get("uuid"),
        "img": payload.get("img"),
        "captcha_enabled": bool(payload.get("captchaEnabled", True)),
    }


@router.post("/login")
def erp_login(data: ErpLoginInput, user=Depends(_host.current_user), db=Depends(_host.get_db)):
    client = _erp_http()
    try:
        # management-system login is OAuth2 form, not JSON. JSON yields HTTP 422
        # without msg, which the workbench used to show as a blank rejection.
        response = client.post(
            "login",
            data={
                "username": data.username,
                "password": data.password,
                "code": data.code or "",
                "uuid": data.uuid or "",
            },
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise DomainError("ERP_OUTCOME_UNKNOWN", "ERP 登录失败，请稍后重试", 502) from error
    finally:
        client.close()
    token = _login_token(payload)
    if not isinstance(payload, dict) or not token:
        raise DomainError("ERP_LOGIN_REJECTED", _login_reject_message(payload), 401)
    erp_client = ERPClient(token)
    try:
        info = erp_client.info()
    finally:
        erp_client.close()
    erp_user_id = _erp_user_id(info if isinstance(info, dict) else {})
    identity = db.get(m.ERPIdentity, user.id)
    taken = db.scalar(select(m.ERPIdentity).where(m.ERPIdentity.erp_user_id == erp_user_id))
    if taken is not None and taken.user_id != user.id:
        raise DomainError("ERP_IDENTITY_CONFLICT", "该 ERP 账号已绑定其他工作台用户", 409)
    if identity is None:
        identity = m.ERPIdentity(user_id=user.id, erp_user_id=erp_user_id, version=1)
        db.add(identity)
    elif identity.erp_user_id and identity.erp_user_id != erp_user_id:
        raise DomainError("ERP_IDENTITY_MISMATCH", "登录的 ERP 账号与已绑定身份不一致", 409)
    else:
        identity.version = (identity.version or 1) + 1
    identity.erp_user_id = erp_user_id
    identity.token_ciphertext = encrypt(token)
    identity.authenticated_at = datetime.now(timezone.utc)
    if decrypt(identity.token_ciphertext) != token:
        raise DomainError("ERP_KEY_REQUIRED", "ERP 凭据加密自检失败，请检查 MOLD_CREDENTIAL_ENCRYPTION_KEY", 503)
    db.commit()
    return _identity_status(identity)
