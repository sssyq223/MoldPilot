"""compatibility marker for the earlier user-profile migration branch

Revision ID: d4e5f6a7b8c9
Revises: c9d2e5f7a814
Create Date: 2026-09-16 16:55:00.000000
"""
revision = "d4e5f6a7b8c9"
down_revision = "c9d2e5f7a814"
branch_labels = None
depends_on = None


def upgrade():
    # d5b8f3c20e71 is the authoritative, data-preserving owner of this table.
    # Keep this published branch revision as a no-op so databases that already
    # recorded it can converge through the merge revision without creating or
    # dropping the same table twice.
    pass


def downgrade():
    pass
