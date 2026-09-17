import json

from app import api as api_module
from app import run_events
from conftest import sign_in


class Publisher:
    def __init__(self, error=None):
        self.messages = []
        self.error = error

    def publish(self, channel, payload):
        if self.error:
            raise self.error
        self.messages.append((channel, json.loads(payload)))


def test_run_notification_contains_only_routing_metadata(monkeypatch):
    publisher = Publisher()
    monkeypatch.setattr(run_events, "_publisher", lambda _url: publisher)

    assert run_events.publish_run_update("conversation-1", "run-1", "RUNNING")

    channel, payload = publisher.messages[0]
    assert channel == "agent:conversation-runs:conversation-1"
    assert payload["run_id"] == "run-1"
    assert payload["status"] == "RUNNING"
    assert set(payload) == {"event_id", "run_id", "status"}


def test_run_event_stream_sends_authorized_database_projection(client, data, monkeypatch):
    sign_in(client)
    created = client.post("/api/runs", json={"prompt": "事件流测试"}).json()

    async def two_signals(conversation_id):
        assert conversation_id == created["conversation_id"]
        yield {"type": "ready"}
        yield {"type": "update", "data": "synthetic-event"}

    monkeypatch.setattr(api_module, "subscribe_run_updates", two_signals)
    with client.stream(
        "GET", f"/api/conversations/{created['conversation_id']}/runs/events"
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert body.count("event: runs") == 2
    assert "事件流测试" in body
    assert created["id"] in body


def test_run_event_stream_rejects_another_users_conversation(client, data):
    sign_in(client, "test_buyer")
    response = client.get("/api/conversations/not-owned/runs/events")
    assert response.status_code == 404
