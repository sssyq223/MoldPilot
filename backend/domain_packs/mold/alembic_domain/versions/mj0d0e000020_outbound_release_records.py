"""Record Agent-owned outbound self-inspection and release evidence."""
from alembic import op
import sqlalchemy as sa


revision = 'mj0d0e000020'
down_revision = 'mi0d0e000020'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'outbound_release_record',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('project_version', sa.Integer(), nullable=False),
        sa.Column('trial_request_id', sa.String(length=36), nullable=True),
        sa.Column('previous_record_id', sa.String(length=36), nullable=True),
        sa.Column('inspection_type', sa.String(length=30), nullable=False),
        sa.Column('result', sa.String(length=30), nullable=False),
        sa.Column('inspected_date', sa.Date(), nullable=False),
        sa.Column('issue_description', sa.Text(), nullable=False),
        sa.Column('corrective_due_date', sa.Date(), nullable=True),
        sa.Column('evidence', sa.Text(), nullable=False),
        sa.Column('source_ref', sa.String(length=120), nullable=False),
        sa.Column('confirmed_by', sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "inspection_type IN ('SELF_INSPECTION','OUTBOUND_ACCEPTANCE')",
            name='outbound_release_inspection_type',
        ),
        sa.CheckConstraint(
            "result IN ('PASSED','FAILED','CONDITIONALLY_PASSED')",
            name='outbound_release_result',
        ),
        sa.CheckConstraint(
            "previous_record_id IS NULL OR previous_record_id <> id",
            name='outbound_release_previous_not_self',
        ),
        sa.CheckConstraint(
            "corrective_due_date IS NULL OR corrective_due_date >= inspected_date",
            name='outbound_release_corrective_due_date',
        ),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['trial_request_id'], ['business_subject.id']),
        sa.ForeignKeyConstraint(
            ['previous_record_id'],
            ['outbound_release_record.id'],
            name='fk_outbound_release_previous',
        ),
        sa.ForeignKeyConstraint(['confirmed_by'], ['app_user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'source_ref', name='outbound_release_unique_source'),
        sa.UniqueConstraint('previous_record_id', name='uq_outbound_release_previous'),
    )
    op.create_index(
        op.f('ix_outbound_release_record_project_id'),
        'outbound_release_record',
        ['project_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_outbound_release_record_trial_request_id'),
        'outbound_release_record',
        ['trial_request_id'],
        unique=False,
    )
    op.execute(
        """CREATE FUNCTION protect_outbound_release_fact() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'outbound release facts are immutable'; END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        'CREATE TRIGGER outbound_release_record_immutable '
        'BEFORE UPDATE OR DELETE ON outbound_release_record FOR EACH ROW '
        'EXECUTE FUNCTION protect_outbound_release_fact()'
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text('SELECT EXISTS (SELECT 1 FROM outbound_release_record)')
    ):
        raise RuntimeError(
            'Outbound release evidence exists; refusing to discard confirmed release facts'
        )
    op.execute('DROP TRIGGER outbound_release_record_immutable ON outbound_release_record')
    op.execute('DROP FUNCTION protect_outbound_release_fact()')
    op.drop_index(
        op.f('ix_outbound_release_record_trial_request_id'),
        table_name='outbound_release_record',
    )
    op.drop_index(
        op.f('ix_outbound_release_record_project_id'),
        table_name='outbound_release_record',
    )
    op.drop_table('outbound_release_record')

