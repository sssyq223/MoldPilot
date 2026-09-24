from domain_packs.mold.erp.procurement.erp_session import _erp_user_id, router
from domain_packs.mold.ports.errors import DomainError


def test_erp_session_routes_are_registered():
    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/api/erp-session/status" in paths
    assert "/api/erp-session/captcha" in paths
    assert "/api/erp-session/login" in paths


def test_erp_user_id_reads_nested_user():
    assert _erp_user_id({"user": {"userId": 88}}) == "88"
    assert _erp_user_id({"data": {"user": {"user_id": "12"}}}) == "12"


def test_erp_user_id_rejects_empty_payload():
    try:
        _erp_user_id({"user": {}})
    except DomainError as error:
        assert error.code == "ERP_PROTOCOL_ERROR"
    else:
        raise AssertionError("expected ERP_PROTOCOL_ERROR")
