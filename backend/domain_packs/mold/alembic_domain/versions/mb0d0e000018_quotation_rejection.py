"""add quotation rejection table

Revision ID: mb0d0e000018
Revises: mb0d0e000017
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa


revision = "mb0d0e000018"
down_revision = "mb0d0e000017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'quotation_rejection',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('project_id', sa.String(length=64), nullable=False),
        sa.Column('rejection_reason', sa.Text(), nullable=False),
        sa.Column('rejection_category', sa.String(length=30), nullable=False),
        sa.Column('rejected_by', sa.String(length=64), nullable=False),
        sa.Column('rejected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('evidence', sa.Text(), nullable=True),
        sa.Column('customer_feedback', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
        sa.ForeignKeyConstraint(['rejected_by'], ['app_user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_quotation_rejection_project_id', 'quotation_rejection', ['project_id'])
    op.create_index('ix_quotation_rejection_rejected_at', 'quotation_rejection', ['rejected_at'])
    op.create_index('ix_quotation_rejection_category', 'quotation_rejection', ['rejection_category'])


def downgrade():
    op.drop_index('ix_quotation_rejection_category', table_name='quotation_rejection')
    op.drop_index('ix_quotation_rejection_rejected_at', table_name='quotation_rejection')
    op.drop_index('ix_quotation_rejection_project_id', table_name='quotation_rejection')
    op.drop_table('quotation_rejection')
