"""Conversation pinning and archiving for the sidebar."""
from alembic import op
import sqlalchemy as sa

revision='5c8a1e9b2d40'
down_revision='a8c4e1d92f70'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('ai_conversation',sa.Column('pinned',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.add_column('ai_conversation',sa.Column('archived',sa.Boolean(),nullable=False,server_default=sa.false()))


def downgrade():
    op.drop_column('ai_conversation','archived')
    op.drop_column('ai_conversation','pinned')
