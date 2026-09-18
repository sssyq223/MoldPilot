"""Initiator-led contact collaboration, not a published approval template."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='a503fe901624'
down_revision='94d51bc730ef'
branch_labels=None
depends_on=None


def identity():
    return [sa.Column('id',sa.String(36),primary_key=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False)]


def upgrade():
    op.create_table('contact_case',*identity(),
        sa.Column('project_id',sa.String(36),sa.ForeignKey('project.id'),nullable=False),
        sa.Column('category',sa.String(60)),sa.Column('title',sa.String(150),nullable=False),
        sa.Column('description',sa.Text(),nullable=False),sa.Column('mode',sa.String(20),nullable=False),
        sa.Column('created_by',sa.String(36),sa.ForeignKey('app_user.id'),nullable=False),
        sa.Column('request_key',sa.String(36),nullable=False),sa.Column('request_hash',sa.String(64),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),sa.UniqueConstraint('created_by','request_key'),
        sa.CheckConstraint("mode IN ('HISTORY','ONLINE')"))
    op.create_index('ix_contact_case_project_id','contact_case',['project_id'])
    op.create_table('contact_task',*identity(),
        sa.Column('case_id',sa.String(36),sa.ForeignKey('contact_case.id'),nullable=False),
        sa.Column('department_id',sa.String(36),sa.ForeignKey('assignment_group.id'),nullable=False),
        sa.Column('title',sa.String(150),nullable=False),
        sa.Column('created_by',sa.String(36),sa.ForeignKey('app_user.id'),nullable=False),
        sa.Column('assignee_id',sa.String(36),sa.ForeignKey('app_user.id')),
        sa.Column('status',sa.String(30),nullable=False),sa.Column('response',sa.Text()),
        sa.CheckConstraint("status IN ('UNASSIGNED','ASSIGNED','RESPONDED')"))
    op.create_index('ix_contact_task_case_id','contact_task',['case_id'])
    op.create_table('contact_record',*identity(),
        sa.Column('case_id',sa.String(36),sa.ForeignKey('contact_case.id'),nullable=False),
        sa.Column('author_id',sa.String(36),sa.ForeignKey('app_user.id'),nullable=False),
        sa.Column('request_key',sa.String(36),nullable=False),sa.Column('request_hash',sa.String(64),nullable=False),
        sa.Column('kind',sa.String(30),nullable=False),sa.Column('occurred_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('detail',postgresql.JSONB(),nullable=False),sa.UniqueConstraint('case_id','author_id','request_key'))
    op.create_index('ix_contact_record_case_id','contact_record',['case_id'])
    op.execute("""CREATE FUNCTION protect_contact_record() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Contact records are append only'; END $$""")
    op.execute('CREATE TRIGGER contact_record_immutable BEFORE UPDATE OR DELETE ON contact_record FOR EACH ROW EXECUTE FUNCTION protect_contact_record()')


def downgrade():
    op.drop_table('contact_record')
    op.execute('DROP FUNCTION protect_contact_record()')
    op.drop_table('contact_task');op.drop_table('contact_case')
