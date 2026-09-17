"""generic agent-workbench host baseline

Revision ID: a10c0e000001
"""
from pathlib import Path
from hashlib import sha256

from alembic import op
import sqlalchemy as sa


revision = "a10c0e000001"
down_revision = None
branch_labels = ("agent_core",)
depends_on = None

_SQL_PATH = Path(__file__).resolve().parents[1] / "sql" / "core_host_baseline.sql"
_SQL_SHA256 = "3329b5770dddcc3732bfe04db36e26aa002a04a3f144fd105dd416e985c6c91e"
_TABLES = (
    "material_binding", "approval_action", "material_review", "approval_seat",
    "ai_step", "agent_run_file", "file_object", "approval_instance", "ai_run",
    "workflow_definition", "permission_grant", "notification",
    "material_template_xlsx_mapping", "login_session", "inbox_event",
    "human_action_intent", "audit_event", "assignment_member", "app_user_profile",
    "ai_conversation", "agent_capability_assignment", "agent_approval_delegation",
    "workflow_category", "outbox_event", "material_template", "assignment_group",
    "app_user",
)


def _statements():
    source = _SQL_PATH.read_text(encoding="utf-8")
    if sha256(source.encode()).hexdigest() != _SQL_SHA256:
        raise RuntimeError("Generic host baseline SQL checksum mismatch")
    return [part.strip() for part in source.split(";\n\n") if part.strip()]


def upgrade():
    for statement in _statements():
        op.execute(sa.text(statement))


def downgrade():
    bind = op.get_bind()
    installed = bind.execute(sa.text("""
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = current_schema()
          AND tablename NOT LIKE 'alembic_%'
        ORDER BY tablename
    """)).scalars().all()
    extras = sorted(set(installed) - set(_TABLES))
    if extras:
        raise RuntimeError(
            "Domain-pack tables still exist; downgrade the active pack before the core host"
        )
    for table in _TABLES:
        op.drop_table(table)
