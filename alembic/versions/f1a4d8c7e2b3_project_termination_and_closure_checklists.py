"""project termination and auditable closure checklists"""
from alembic import op
import sqlalchemy as sa


revision = 'f1a4d8c7e2b3'
down_revision = 'd33a12f7b9e1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('project_closure_case',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('project_id',sa.String(36),sa.ForeignKey('project.id'),nullable=False),
        sa.Column('mode',sa.String(20),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),
        sa.Column('current_stage',sa.String(200),nullable=False),
        sa.Column('opened_by',sa.String(36),sa.ForeignKey('app_user.id'),nullable=False),
        sa.Column('source_termination_subject_id',sa.String(36),sa.ForeignKey('business_subject.id'),unique=True),
        sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('closed_by',sa.String(36),sa.ForeignKey('app_user.id')),
        sa.Column('closed_at',sa.DateTime(timezone=True)),
        sa.CheckConstraint("mode IN ('NORMAL','TERMINATION')",name='project_closure_mode'),
        sa.CheckConstraint("status IN ('OPEN','CLOSED','CANCELLED')",name='project_closure_status'))
    op.create_index('ix_project_closure_case_project_id','project_closure_case',['project_id'])
    op.create_index('uq_project_closure_open_project','project_closure_case',['project_id'],unique=True,
        postgresql_where=sa.text("status = 'OPEN'"))

    op.create_table('project_closure_item',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('case_id',sa.String(36),sa.ForeignKey('project_closure_case.id'),nullable=False),
        sa.Column('item_key',sa.String(80),nullable=False),
        sa.Column('label',sa.String(200),nullable=False),
        sa.Column('status',sa.String(30),nullable=False),
        sa.Column('allow_not_applicable',sa.Boolean(),nullable=False),
        sa.Column('system_managed',sa.Boolean(),nullable=False),
        sa.Column('result',sa.Text(),nullable=False),
        sa.Column('evidence',sa.Text(),nullable=False),
        sa.Column('source_system',sa.String(20),nullable=False),
        sa.Column('source_ref',sa.String(300)),
        sa.Column('source_as_of',sa.DateTime(timezone=True)),
        sa.Column('updated_by',sa.String(36),sa.ForeignKey('app_user.id')),
        sa.Column('updated_at',sa.DateTime(timezone=True)),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.UniqueConstraint('case_id','item_key'),
        sa.CheckConstraint("status IN ('PENDING','DONE','NOT_APPLICABLE')",name='project_closure_item_status'),
        sa.CheckConstraint("source_system IN ('AGENT','ERP','MANUAL')",name='project_closure_item_source'))
    op.create_index('ix_project_closure_item_case_id','project_closure_item',['case_id'])

    op.create_table('project_closure_item_revision',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('item_id',sa.String(36),sa.ForeignKey('project_closure_item.id'),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('from_status',sa.String(30)),
        sa.Column('to_status',sa.String(30),nullable=False),
        sa.Column('result',sa.Text(),nullable=False),
        sa.Column('evidence',sa.Text(),nullable=False),
        sa.Column('source_system',sa.String(20),nullable=False),
        sa.Column('source_ref',sa.String(300)),
        sa.Column('source_as_of',sa.DateTime(timezone=True)),
        sa.Column('changed_by',sa.String(36),sa.ForeignKey('app_user.id'),nullable=False),
        sa.UniqueConstraint('item_id','revision'))
    op.create_index('ix_project_closure_item_revision_item_id','project_closure_item_revision',['item_id'])

    op.create_table('project_closure_detail',
        sa.Column('subject_id',sa.String(36),sa.ForeignKey('business_subject.id'),primary_key=True),
        sa.Column('decision',sa.String(30),nullable=False),
        sa.Column('effective_date',sa.Date(),nullable=False),
        sa.Column('reason',sa.Text(),nullable=False),
        sa.Column('evidence',sa.Text(),nullable=False),
        sa.Column('project_version',sa.Integer(),nullable=False),
        sa.Column('closure_case_id',sa.String(36),sa.ForeignKey('project_closure_case.id')),
        sa.Column('closure_case_version',sa.Integer()),
        sa.Column('current_stage',sa.String(200)),
        sa.Column('completed_work_summary',sa.Text()),
        sa.Column('incurred_cost_summary',sa.Text()),
        sa.Column('incurred_cost_amount',sa.Numeric(18,2)),
        sa.Column('currency',sa.String(3)),
        sa.CheckConstraint("decision IN ('TERMINATE','NORMAL_CLOSE','SETTLEMENT_CLOSE')",name='project_closure_decision'),
        sa.CheckConstraint('incurred_cost_amount IS NULL OR incurred_cost_amount >= 0',name='project_closure_cost'))


def downgrade():
    raise RuntimeError('Project closure history requires a reviewed forward migration; downgrade is not destructive.')
