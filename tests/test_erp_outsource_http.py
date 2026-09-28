from domain_packs.mold.erp.procurement import erp_outsource_http
from domain_packs.mold.ports.errors import DomainError


def _patch_readable_token(monkeypatch):
    monkeypatch.setattr(erp_outsource_http, "decrypt", lambda value: "token")


class User:
    id = "user-1"


class Identity:
    erp_user_id = "42"
    token_ciphertext = "ciphertext"


class FakeDB:
    def __init__(self):
        self.operation = None
        self.commits = 0

    def get(self, model, key):
        return Identity()

    def scalar(self, statement):
        return self.operation

    def add(self, operation):
        self.operation = operation

    def commit(self):
        self.commits += 1

    def delete(self, operation):
        if self.operation is operation:
            self.operation = None


def test_dispatch_erp_reuses_successful_intent_without_second_write(monkeypatch):
    _patch_readable_token(monkeypatch)
    db = FakeDB()
    calls = []
    monkeypatch.setattr(
        erp_outsource_http,
        "call_erp",
        lambda *args, **kwargs: calls.append(kwargs) or {"code": 200, "data": {"id": 7}},
    )
    arguments = {
        "intent_id": "intent-1",
        "action": "processor_accept",
        "native_id": "order:7",
        "method": "POST",
        "path": "entrust/inquiry/order/7/accept",
    }
    first = erp_outsource_http.dispatch_erp(db, User(), **arguments)
    second = erp_outsource_http.dispatch_erp(db, User(), **arguments)
    assert first == second == {"code": 200, "data": {"id": 7}}
    assert len(calls) == 1
    assert db.operation.state == "SUCCEEDED"
    assert db.commits == 2


def test_post_erp_with_action_but_no_intent_fails_closed(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(
        erp_outsource_http, "call_erp",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("bare ERP call must not happen")),
    )
    try:
        erp_outsource_http.post_erp(
            db, User(), "entrust/inquiry/order/7/accept", {},
            intent_id=None, action="processor_accept", native_id="order:7",
        )
    except DomainError as error:
        assert error.code == "CONFIRMATION_INVALID"
    else:
        raise AssertionError("expected CONFIRMATION_INVALID")
    assert db.operation is None


def test_dispatch_erp_in_flight_intent_is_reported_not_replayed(monkeypatch):
    _patch_readable_token(monkeypatch)
    db = FakeDB()
    monkeypatch.setattr(
        erp_outsource_http, "call_erp",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not replay")),
    )
    arguments = {
        "intent_id": "intent-3",
        "action": "processor_accept",
        "native_id": "order:7",
        "method": "POST",
        "path": "entrust/inquiry/order/7/accept",
    }
    request_hash = erp_outsource_http.content_hash({
        "method": "POST", "path": arguments["path"], "body": {}, "params": {},
    })
    db.operation = type("Op", (), {
        "intent_id": "intent-3", "request_hash": request_hash, "state": "DISPATCHING",
        "error_code": None, "response": None,
    })()
    try:
        erp_outsource_http.dispatch_erp(db, User(), **arguments)
    except DomainError as error:
        assert error.code == "ERP_OUTCOME_UNKNOWN"
        assert "正在提交" in error.message
    else:
        raise AssertionError("expected ERP_OUTCOME_UNKNOWN")


def test_dispatch_erp_marks_unknown_and_never_auto_retries(monkeypatch):
    _patch_readable_token(monkeypatch)
    db = FakeDB()
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise DomainError("ERP_OUTCOME_UNKNOWN", "timeout", 502)

    monkeypatch.setattr(erp_outsource_http, "call_erp", fail)
    arguments = {
        "intent_id": "intent-2",
        "action": "warehouse_inbound",
        "native_id": "product-shipment:9",
        "method": "POST",
        "path": "entrust/arrival-confirm/9/confirm-inbound",
    }
    for _ in range(2):
        try:
            erp_outsource_http.dispatch_erp(db, User(), **arguments)
        except DomainError as error:
            assert error.code == "ERP_OUTCOME_UNKNOWN"
        else:
            raise AssertionError("expected ERP_OUTCOME_UNKNOWN")
    assert calls == 1
    assert db.operation.state == "UNKNOWN"


def test_dispatch_erp_unreadable_token_does_not_open_operation(monkeypatch):
    def boom(value):
        raise DomainError("ERP_LOGIN_REQUIRED", "ERP 连接凭据已失效，请重新验证", 401)

    monkeypatch.setattr(erp_outsource_http, "decrypt", boom)
    monkeypatch.setattr(
        erp_outsource_http,
        "discard_unreadable_erp_token",
        lambda identity: True,
    )
    db = FakeDB()
    try:
        erp_outsource_http.dispatch_erp(
            db, User(),
            intent_id="intent-stale",
            action="processor_accept",
            native_id="order:7",
            method="POST",
            path="entrust/inquiry/order/7/accept",
        )
    except DomainError as error:
        assert error.code == "ERP_LOGIN_REQUIRED"
    else:
        raise AssertionError("expected ERP_LOGIN_REQUIRED")
    assert db.operation is None


def test_dispatch_erp_login_failure_does_not_consume_intent(monkeypatch):
    _patch_readable_token(monkeypatch)
    db = FakeDB()

    def forbidden(*args, **kwargs):
        raise DomainError("ERP_FORBIDDEN", "ERP 登录失效或原系统权限不足", 403)

    monkeypatch.setattr(erp_outsource_http, "call_erp", forbidden)
    try:
        erp_outsource_http.dispatch_erp(
            db, User(),
            intent_id="intent-auth",
            action="processor_accept",
            native_id="order:7",
            method="POST",
            path="entrust/inquiry/order/7/accept",
        )
    except DomainError as error:
        assert error.code == "ERP_FORBIDDEN"
    else:
        raise AssertionError("expected ERP_FORBIDDEN")
    assert db.operation is None
