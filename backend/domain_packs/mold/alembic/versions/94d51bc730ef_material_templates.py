"""Versioned reusable material field contracts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='94d51bc730ef'
down_revision='83c405a62edf'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('material_template',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('template_key',sa.String(80),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('name',sa.String(150),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),
        sa.Column('contract',postgresql.JSONB(),nullable=False),
        sa.Column('package_hash',sa.String(64),nullable=False),
        sa.UniqueConstraint('template_key','version'),sa.CheckConstraint("status IN ('DRAFT','PUBLISHED')"))
    op.add_column('workflow_definition',sa.Column('material_template_id',sa.String(36),nullable=True))
    op.create_foreign_key('workflow_material_template_fk','workflow_definition','material_template',['material_template_id'],['id'])
    op.execute("""CREATE FUNCTION protect_published_material_template() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.status = 'PUBLISHED' THEN RAISE EXCEPTION 'Published material template is immutable'; END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$""")
    op.execute('CREATE TRIGGER material_template_immutable BEFORE UPDATE OR DELETE ON material_template FOR EACH ROW EXECUTE FUNCTION protect_published_material_template()')

def downgrade():
    op.drop_constraint('workflow_material_template_fk','workflow_definition',type_='foreignkey')
    op.drop_column('workflow_definition','material_template_id')
    op.execute('DROP TRIGGER material_template_immutable ON material_template')
    op.execute('DROP FUNCTION protect_published_material_template()')
    op.drop_table('material_template')
