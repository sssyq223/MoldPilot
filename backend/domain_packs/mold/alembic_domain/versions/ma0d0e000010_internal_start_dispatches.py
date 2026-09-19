"""Append-only formal-start department handoff evidence.

Revision ID: ma0d0e000010
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "ma0d0e000010"
down_revision = "m90d0e000009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "internal_start_dispatch",
        sa.Column("start_subject_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("role_key", sa.String(length=60), nullable=False),
        sa.Column("role_name", sa.String(length=100), nullable=False),
        sa.Column("department_label", sa.String(length=100), nullable=False),
        sa.Column(
            "assignment_source",
            sa.String(length=30),
            nullable=False,
            server_default="PROJECT_ROLE",
        ),
        sa.Column(
            "recipient_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("dispatch_status", sa.String(length=30), nullable=False),
        sa.Column("event_id", sa.String(length=36)),
        sa.Column("dispatched_by", sa.String(length=36), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "assignment_source = 'PROJECT_ROLE'",
            name="internal_start_dispatch_assignment_source",
        ),
        sa.CheckConstraint(
            "dispatch_status IN ('QUEUED','UNASSIGNED')",
            name="internal_start_dispatch_status",
        ),
        sa.CheckConstraint(
            "(dispatch_status = 'QUEUED' AND event_id IS NOT NULL) OR "
            "(dispatch_status = 'UNASSIGNED' AND event_id IS NULL)",
            name="internal_start_dispatch_event_state",
        ),
        sa.ForeignKeyConstraint(["dispatched_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["outbox_event.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["start_subject_id"], ["business_subject.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "start_subject_id", "role_key", name="internal_start_dispatch_role"
        ),
    )
    op.create_index(
        "ix_internal_start_dispatch_project_id",
        "internal_start_dispatch",
        ["project_id"],
    )
    op.create_index(
        "ix_internal_start_dispatch_start_subject_id",
        "internal_start_dispatch",
        ["start_subject_id"],
    )
    op.execute("""
        CREATE FUNCTION protect_internal_start_dispatch() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Internal start dispatch evidence is append-only';
        END $$ LANGUAGE plpgsql
    """)
    op.execute(
        "CREATE TRIGGER internal_start_dispatch_immutable "
        "BEFORE UPDATE OR DELETE ON internal_start_dispatch "
        "FOR EACH ROW EXECUTE FUNCTION protect_internal_start_dispatch()"
    )


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS internal_start_dispatch_immutable "
        "ON internal_start_dispatch"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_internal_start_dispatch()")
    op.drop_index(
        "ix_internal_start_dispatch_start_subject_id",
        table_name="internal_start_dispatch",
    )
    op.drop_index(
        "ix_internal_start_dispatch_project_id",
        table_name="internal_start_dispatch",
    )
    op.drop_table("internal_start_dispatch")
