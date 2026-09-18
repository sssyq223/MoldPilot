"""freeze project pause scope and audit resume task shifts"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'd33a12f7b9e1'
down_revision = 'c825ef319d76'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('pause_record',sa.Column('shifted_days',sa.Integer(),nullable=False,server_default='0'))
    op.add_column('pause_record',sa.Column('shift_applied',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.add_column('pause_record',sa.Column('customer_due_date_snapshot',sa.Date()))
    op.create_index('uq_pause_record_open_project','pause_record',['project_id'],unique=True,
                    postgresql_where=sa.text('end_date IS NULL'))
    op.create_table('project_pause_detail',
        sa.Column('subject_id',sa.String(36),sa.ForeignKey('business_subject.id'),primary_key=True),
        sa.Column('decision',sa.String(20),nullable=False),
        sa.Column('effective_date',sa.Date(),nullable=False),
        sa.Column('expected_resume_date',sa.Date()),
        sa.Column('reason',sa.Text(),nullable=False),
        sa.Column('evidence',sa.Text(),nullable=False),
        sa.Column('source_pause_subject_id',sa.String(36),sa.ForeignKey('business_subject.id')),
        sa.Column('plan_subject_id',sa.String(36),sa.ForeignKey('business_subject.id')),
        sa.Column('task_snapshot',postgresql.JSONB(),nullable=False,server_default=sa.text("'[]'::jsonb")),
        sa.Column('customer_due_date_snapshot',sa.Date()),
        sa.CheckConstraint("decision IN ('PAUSE','RESUME')",name='project_pause_decision'))
    op.create_table('pause_task_shift',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('pause_id',sa.String(36),sa.ForeignKey('pause_record.id'),nullable=False),
        sa.Column('task_id',sa.String(36),sa.ForeignKey('plan_task.id'),nullable=False),
        sa.Column('previous_start',sa.Date(),nullable=False),
        sa.Column('previous_end',sa.Date(),nullable=False),
        sa.Column('shifted_start',sa.Date(),nullable=False),
        sa.Column('shifted_end',sa.Date(),nullable=False),
        sa.Column('shifted_days',sa.Integer(),nullable=False),
        sa.Column('task_status',sa.String(30),nullable=False),
        sa.UniqueConstraint('pause_id','task_id'),
        sa.CheckConstraint('shifted_days >= 0'))
    op.create_index('ix_pause_task_shift_pause_id','pause_task_shift',['pause_id'])
    op.create_index('ix_pause_task_shift_task_id','pause_task_shift',['task_id'])


def downgrade():
    raise RuntimeError('Pause and resume audit facts require a reviewed forward migration; downgrade is not destructive.')
