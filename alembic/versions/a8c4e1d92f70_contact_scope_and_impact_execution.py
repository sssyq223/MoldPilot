"""complete contact identity, impact actions, and execution evidence"""
from alembic import op
import sqlalchemy as sa


revision = 'a8c4e1d92f70'
down_revision = 'f1a4d8c7e2b3'
branch_labels = None
depends_on = None


def upgrade():
    for name,column_type in (
        ('customer_ref',sa.String(200)),('customer_name',sa.String(200)),('mold_number',sa.String(100)),
        ('product_ref',sa.String(200)),('application_date',sa.Date()),('problem_source',sa.String(40)),
        ('current_stage',sa.String(200)),('change_type',sa.String(30)),('urgency',sa.String(20))):
        op.add_column('contact_case',sa.Column(name,column_type,nullable=True))
    op.create_check_constraint('contact_problem_source','contact_case',
        "problem_source IS NULL OR problem_source IN ('CUSTOMER_CHANGE','DESIGN_ISSUE','ASSEMBLY_ISSUE','MACHINING_ISSUE','PROCUREMENT_ISSUE','QUALITY_ISSUE','TRIAL_ISSUE','OUTSOURCE_DEFECT','COST_REDUCTION','PROCESS_IMPROVEMENT','OTHER')")
    op.create_check_constraint('contact_change_type','contact_case',"change_type IS NULL OR change_type IN ('CHANGE','EXCEPTION','IMPROVEMENT')")
    op.create_check_constraint('contact_urgency','contact_case',"urgency IS NULL OR urgency IN ('NORMAL','URGENT','CRITICAL')")

    additions=(
        ('affected_type',sa.String(40)),('affected_ref',sa.String(300)),('impact_description',sa.Text()),
        ('planned_action',sa.String(30)),('delivery_impact_days',sa.Integer()),('estimated_amount',sa.Numeric(18,2)),
        ('currency',sa.String(3)),('source_system',sa.String(20)),('source_ref',sa.String(300)),
        ('source_as_of',sa.DateTime(timezone=True)),('actual_completed_at',sa.DateTime(timezone=True)),
        ('actual_hours',sa.Numeric(12,2)),('actual_amount',sa.Numeric(18,2)),('actual_currency',sa.String(3)),
        ('execution_evidence',sa.Text()),('execution_source_system',sa.String(20)),
        ('execution_source_ref',sa.String(300)),('execution_source_as_of',sa.DateTime(timezone=True)))
    for name,column_type in additions:
        op.add_column('contact_task',sa.Column(name,column_type,nullable=True))
    # Legacy rows remain explicit as migrated, unverified facts; new API calls require complete structured input.
    op.execute("UPDATE contact_task SET affected_type='OTHER', affected_ref='LEGACY:' || id, impact_description=title, planned_action='REWORK', delivery_impact_days=0, source_system='AGENT' WHERE affected_type IS NULL")
    for name in ('affected_type','affected_ref','impact_description','planned_action','delivery_impact_days','source_system'):
        op.alter_column('contact_task',name,nullable=False)
    op.create_check_constraint('contact_task_affected_type','contact_task',
        "affected_type IN ('DRAWING','MATERIAL','PURCHASE_ORDER','WIP_TASK','SUPPLIER_TASK','PLAN_NODE','CONTRACT','FINANCE','LOGISTICS','OTHER')")
    op.create_check_constraint('contact_task_planned_action','contact_task',"planned_action IN ('CONTINUE','PAUSE','CANCEL','REWORK','REISSUE')")
    op.create_check_constraint('contact_task_source','contact_task',"source_system IN ('AGENT','ERP','MANUAL')")
    op.create_check_constraint('contact_task_execution_source','contact_task',"execution_source_system IS NULL OR execution_source_system IN ('AGENT','ERP','MANUAL')")
    op.create_check_constraint('contact_task_delivery_days','contact_task','delivery_impact_days >= 0')
    op.create_check_constraint('contact_task_estimated_amount','contact_task','estimated_amount IS NULL OR estimated_amount >= 0')
    op.create_check_constraint('contact_task_actual_hours','contact_task','actual_hours IS NULL OR actual_hours >= 0')
    op.create_check_constraint('contact_task_actual_amount','contact_task','actual_amount IS NULL OR actual_amount >= 0')


def downgrade():
    raise RuntimeError('Contact impact and execution history requires a reviewed forward migration; downgrade is not destructive.')
