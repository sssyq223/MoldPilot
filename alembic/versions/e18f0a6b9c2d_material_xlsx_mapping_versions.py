"""material xlsx mapping versions"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'e18f0a6b9c2d'
down_revision = 'c3f2a91d4b6e'
branch_labels = None
depends_on = None


def upgrade():
    json_type = postgresql.JSONB(astext_type=sa.Text()) if op.get_bind().dialect.name == 'postgresql' else sa.JSON()
    op.create_table('material_template_xlsx_mapping',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('template_id', sa.String(36), sa.ForeignKey('material_template.id'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('mapping', json_type, nullable=False),
        sa.Column('mapping_hash', sa.String(64), nullable=False),
        sa.Column('created_by', sa.String(36), sa.ForeignKey('app_user.id'), nullable=False),
        sa.UniqueConstraint('template_id', 'version'))
    op.create_index('ix_material_template_xlsx_mapping_template_id', 'material_template_xlsx_mapping', ['template_id'])


def downgrade():
    op.drop_index('ix_material_template_xlsx_mapping_template_id', table_name='material_template_xlsx_mapping')
    op.drop_table('material_template_xlsx_mapping')
