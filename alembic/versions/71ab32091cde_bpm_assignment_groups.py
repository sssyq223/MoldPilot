"""Stable role/department membership and node-entry assignment snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '71ab32091cde'
down_revision = '57b26330cbf2'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('assignment_group',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('kind',sa.String(20),nullable=False),
        sa.Column('name',sa.String(100),nullable=False),
        sa.Column('active',sa.Boolean(),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False),
        sa.UniqueConstraint('kind','name'),sa.CheckConstraint("kind IN ('ROLE','DEPARTMENT')"))
    op.create_table('assignment_member',
        sa.Column('group_id',sa.String(36),sa.ForeignKey('assignment_group.id'),primary_key=True),
        sa.Column('user_id',sa.String(36),sa.ForeignKey('app_user.id'),primary_key=True),
        sa.Column('is_head',sa.Boolean(),nullable=False))
    op.add_column('approval_instance',sa.Column('assignment_snapshots',postgresql.JSONB(),nullable=False,server_default=sa.text("'{}'::jsonb")))

def downgrade():
    op.drop_column('approval_instance','assignment_snapshots')
    op.drop_table('assignment_member')
    op.drop_table('assignment_group')
