from types import SimpleNamespace

from domain_packs.mold.erp.procurement.erp_session import (
    _erp_user_id,
    _identity_status,
    _login_reject_message,
    _login_token,
    router,
)
from domain_packs.mold.ports.errors import DomainError


def test_erp_session_routes_are_registered():
    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/api/erp-session/status" in paths
    assert "/api/erp-session/captcha" in paths
    assert "/api/erp-session/login" in paths


def test_login_token_reads_nested_ruoyi_payload():
    assert _login_token({"code": 200, "data": {"token": "abc"}}) == "abc"
    assert _login_token({"token": "top"}) == "top"


def test_login_reject_message_uses_erp_msg():
    assert "密码" in _login_reject_message({"msg": "用户不存在/密码错误"})
    assert "登录名" in _login_reject_message({})


def test_erp_user_id_reads_nested_user():
    assert _erp_user_id({"user": {"userId": 88}}) == "88"
    assert _erp_user_id({"data": {"user": {"user_id": "12"}}}) == "12"


def test_erp_session_status_ignores_unreadable_token(monkeypatch):
    monkeypatch.setattr(
        "domain_packs.mold.erp.procurement.erp_session.erp_token_readable",
        lambda ciphertext: False,
    )
    monkeypatch.setattr(
        "domain_packs.mold.erp.procurement.erp_session.settings",
        lambda: SimpleNamespace(erp_base_url="http://192.168.3.61:18080/dev-api"),
    )
    status = _identity_status(SimpleNamespace(
        erp_user_id="42",
        token_ciphertext="stale",
        authenticated_at=None,
    ))
    assert status["bound"] is True
    assert status["authenticated"] is False


def test_erp_user_id_rejects_empty_payload():
    try:
        _erp_user_id({"user": {}})
    except DomainError as error:
        assert error.code == "ERP_PROTOCOL_ERROR"
    else:
        raise AssertionError("expected ERP_PROTOCOL_ERROR")
