"""material xlsx review packages"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'a2b7c9d4e5f6'
down_revision = 'e18f0a6b9c2d'
branch_labels = None
depends_on = None


def upgrade():
    json_type = postgresql.JSONB(astext_type=sa.Text()) if op.get_bind().dialect.name == 'postgresql' else sa.JSON()
    op.create_table('material_review',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('template_id', sa.String(36), sa.ForeignKey('material_template.id'), nullable=False),
        sa.Column('mapping_id', sa.String(36), sa.ForeignKey('material_template_xlsx_mapping.id'), nullable=True),
        sa.Column('file_id', sa.String(36), sa.ForeignKey('file_object.id'), nullable=False),
        sa.Column('owner_id', sa.String(36), sa.ForeignKey('app_user.id'), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('material_data', json_type, nullable=False),
        sa.Column('issues', json_type, nullable=False),
        sa.Column('template_hash', sa.String(64), nullable=False),
        sa.Column('mapping_hash', sa.String(64), nullable=False),
        sa.Column('file_sha256', sa.String(64), nullable=False),
        sa.Column('review_hash', sa.String(64), nullable=False),
        sa.Column('confirmed_by', sa.String(36), sa.ForeignKey('app_user.id'), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('NEEDS_REVIEW','READY_FOR_CONFIRMATION','CONFIRMED','REJECTED')"))
    op.create_index('ix_material_review_template_id', 'material_review', ['template_id'])
    op.create_index('ix_material_review_mapping_id', 'material_review', ['mapping_id'])
    op.create_index('ix_material_review_file_id', 'material_review', ['file_id'])
    op.create_index('ix_material_review_owner_id', 'material_review', ['owner_id'])


def downgrade():
    op.drop_index('ix_material_review_owner_id', table_name='material_review')
    op.drop_index('ix_material_review_file_id', table_name='material_review')
    op.drop_index('ix_material_review_mapping_id', table_name='material_review')
    op.drop_index('ix_material_review_template_id', table_name='material_review')
    op.drop_table('material_review')
