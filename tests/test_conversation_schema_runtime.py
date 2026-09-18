from sqlalchemy import event

from conftest import sign_in


def test_conversation_routes_never_mutate_schema_at_request_time(client, test_engine):
    sign_in(client)
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.strip().upper())

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        response = client.get("/api/conversations")
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)

    assert response.status_code == 200, response.text
    assert not any(statement.startswith("ALTER TABLE") for statement in statements)
