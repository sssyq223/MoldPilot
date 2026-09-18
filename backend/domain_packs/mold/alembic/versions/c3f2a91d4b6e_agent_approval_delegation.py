"""agent approval delegation policies"""
from alembic import op
import sqlalchemy as sa


revision = 'c3f2a91d4b6e'
down_revision = '5c8a1e9b2d40'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'agent_approval_delegation',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('app_user.id'), nullable=False),
        sa.Column('process_key', sa.String(80), nullable=False),
        sa.Column('node_key', sa.String(80), nullable=False),
        sa.Column('decision', sa.String(20), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('valid_from', sa.DateTime(timezone=True)),
        sa.Column('valid_to', sa.DateTime(timezone=True)),
        sa.Column('created_by', sa.String(36), sa.ForeignKey('app_user.id'), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.Column('revoked_by', sa.String(36), sa.ForeignKey('app_user.id')),
        sa.Column('revoke_reason', sa.Text()),
        sa.UniqueConstraint('user_id', 'process_key', 'node_key', 'decision'),
        sa.CheckConstraint("decision IN ('APPROVE')", name='agent_approval_delegation_decision'),
    )
    op.create_index('ix_agent_approval_delegation_user_id', 'agent_approval_delegation', ['user_id'])
    op.create_index('ix_agent_approval_delegation_lookup', 'agent_approval_delegation', ['process_key', 'node_key', 'active'])


def downgrade():
    op.drop_index('ix_agent_approval_delegation_lookup', table_name='agent_approval_delegation')
    op.drop_index('ix_agent_approval_delegation_user_id', table_name='agent_approval_delegation')
    op.drop_table('agent_approval_delegation')
