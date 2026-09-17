"""Conversation pinning and archiving for the sidebar."""
from alembic import op

revision='5c8a1e9b2d40'
down_revision='a8c4e1d92f70'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('ALTER TABLE ai_conversation ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false')
    op.execute('ALTER TABLE ai_conversation ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT false')


def downgrade():
    op.execute('ALTER TABLE ai_conversation DROP COLUMN IF EXISTS archived')
    op.execute('ALTER TABLE ai_conversation DROP COLUMN IF EXISTS pinned')
