"""Contact resolution approval, verification and manual closure."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='c825ef319d76'
down_revision='b714de208c65'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('contact_case',sa.Column('closed_at',sa.DateTime(timezone=True)))
    op.add_column('contact_case',sa.Column('closed_by',sa.String(36),sa.ForeignKey('app_user.id')))
    op.add_column('contact_case',sa.Column('reviewer_id',sa.String(36),sa.ForeignKey('app_user.id')))
    op.add_column('contact_task',sa.Column('verified_plan_id',sa.String(36),sa.ForeignKey('business_subject.id')))
    for constraint in sa.inspect(op.get_bind()).get_check_constraints('contact_task'):
        if 'status' in constraint['sqltext']:
            op.drop_constraint(constraint['name'],'contact_task',type_='check')
    op.create_check_constraint('contact_task_state','contact_task',"status IN ('UNASSIGNED','ASSIGNED','RESPONDED','VERIFIED','CANCELLED')")
    op.create_table('contact_resolution',
        sa.Column('subject_id',sa.String(36),sa.ForeignKey('business_subject.id'),primary_key=True),
        sa.Column('case_id',sa.String(36),sa.ForeignKey('contact_case.id'),nullable=False),
        sa.Column('case_revision',sa.Integer(),nullable=False),sa.Column('solution',sa.Text(),nullable=False),
        sa.Column('customer_due_affected',sa.Boolean(),nullable=False),sa.Column('customer_evidence',sa.Text()),
        sa.Column('material_snapshot',postgresql.JSONB(),nullable=False))
    op.create_index('ix_contact_resolution_case_id','contact_resolution',['case_id'])
    op.execute('CREATE TRIGGER contact_resolution_immutable BEFORE UPDATE OR DELETE ON contact_resolution FOR EACH ROW EXECUTE FUNCTION protect_contact_record()')

def downgrade():
    raise RuntimeError('Closure and approval facts require a reviewed forward migration; downgrade is not destructive.')
