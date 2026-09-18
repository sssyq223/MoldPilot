"""User-maintained workflow categories, independent of business adapters."""
from alembic import op
import sqlalchemy as sa
revision='83c405a62edf'
down_revision='71ab32091cde'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('workflow_category',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('name',sa.String(100),nullable=False,unique=True),
        sa.Column('active',sa.Boolean(),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False))
    op.add_column('workflow_definition',sa.Column('category_id',sa.String(36),nullable=True))
    op.create_foreign_key('workflow_definition_category_fk','workflow_definition','workflow_category',['category_id'],['id'])
    # Preserve published config, BPMN and package hashes; classify old templates without guessing business category.
    op.execute("INSERT INTO workflow_category(id,created_at,name,active,version) VALUES ('9a8d3e91-70af-48b6-985a-a195da134638',CURRENT_TIMESTAMP,'待整理',true,1)")
    op.execute("UPDATE workflow_definition SET category_id='9a8d3e91-70af-48b6-985a-a195da134638' WHERE category_id IS NULL")

def downgrade():
    op.drop_constraint('workflow_definition_category_fk','workflow_definition',type_='foreignkey')
    op.drop_column('workflow_definition','category_id')
    op.drop_table('workflow_category')
