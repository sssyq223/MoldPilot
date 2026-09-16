"""material bindings for submitted workflows"""
from alembic import op
import sqlalchemy as sa


revision = 'b3c8d1e2f4a7'
down_revision = 'a2b7c9d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('material_binding',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resource_type', sa.String(30), nullable=False),
        sa.Column('resource_id', sa.String(36), nullable=False),
        sa.Column('resource_revision', sa.Integer(), nullable=False),
        sa.Column('definition_id', sa.String(36), sa.ForeignKey('workflow_definition.id'), nullable=False),
        sa.Column('template_id', sa.String(36), sa.ForeignKey('material_template.id'), nullable=False),
        sa.Column('review_id', sa.String(36), sa.ForeignKey('material_review.id'), nullable=False),
        sa.Column('material_hash', sa.String(64), nullable=False),
        sa.Column('review_hash', sa.String(64), nullable=False),
        sa.Column('bound_by', sa.String(36), sa.ForeignKey('app_user.id'), nullable=False),
        sa.CheckConstraint("resource_type IN ('purchase_request','business_subject')"))
    op.create_index('ix_material_binding_resource_type', 'material_binding', ['resource_type'])
    op.create_index('ix_material_binding_resource_id', 'material_binding', ['resource_id'])
    op.create_index('ix_material_binding_definition_id', 'material_binding', ['definition_id'])
    op.create_index('ix_material_binding_template_id', 'material_binding', ['template_id'])
    op.create_index('ix_material_binding_review_id', 'material_binding', ['review_id'])
    op.create_index('ix_material_binding_bound_by', 'material_binding', ['bound_by'])


def downgrade():
    op.drop_index('ix_material_binding_bound_by', table_name='material_binding')
    op.drop_index('ix_material_binding_review_id', table_name='material_binding')
    op.drop_index('ix_material_binding_template_id', table_name='material_binding')
    op.drop_index('ix_material_binding_definition_id', table_name='material_binding')
    op.drop_index('ix_material_binding_resource_id', table_name='material_binding')
    op.drop_index('ix_material_binding_resource_type', table_name='material_binding')
    op.drop_table('material_binding')
