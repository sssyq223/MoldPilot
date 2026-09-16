"""plan department confirmations"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'd2f0a9b1c3e4'
down_revision = 'b3c8d1e2f4a7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('plan_department_confirmation',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('plan_change_id', sa.String(36), sa.ForeignKey('business_subject.id'), nullable=False),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('project.id'), nullable=False),
        sa.Column('department', sa.String(100), nullable=False),
        sa.Column('assigned_user_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('task_keys', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('change_types', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('confirmed_by', sa.String(36), sa.ForeignKey('app_user.id'), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('PENDING','CONFIRMED')", name='plan_department_confirmation_status'),
        sa.UniqueConstraint('plan_change_id','department'))
    op.create_index('ix_plan_department_confirmation_plan_change_id', 'plan_department_confirmation', ['plan_change_id'])
    op.create_index('ix_plan_department_confirmation_project_id', 'plan_department_confirmation', ['project_id'])


def downgrade():
    op.drop_index('ix_plan_department_confirmation_project_id', table_name='plan_department_confirmation')
    op.drop_index('ix_plan_department_confirmation_plan_change_id', table_name='plan_department_confirmation')
    op.drop_table('plan_department_confirmation')
