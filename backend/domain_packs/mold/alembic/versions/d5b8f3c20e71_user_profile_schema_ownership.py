"""move user profile storage from runtime DDL into migrations

Revision ID: d5b8f3c20e71
Revises: c4e7a2b91d60
"""
from alembic import op
import sqlalchemy as sa


revision = "d5b8f3c20e71"
down_revision = "c4e7a2b91d60"
branch_labels = None
depends_on = None


def upgrade():
    # Older application builds created this table lazily. IF NOT EXISTS keeps
    # their data and makes the migration the authoritative schema owner.
    op.execute("""
        CREATE TABLE IF NOT EXISTS app_user_profile (
            user_id VARCHAR(36) PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
            avatar_url TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ
        )
    """)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM app_user_profile)")):
        raise RuntimeError("User profile data exists; downgrade would discard it")
    op.drop_table("app_user_profile")
