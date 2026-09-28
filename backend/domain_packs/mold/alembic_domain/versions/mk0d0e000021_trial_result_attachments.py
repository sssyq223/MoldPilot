"""Link immutable report originals to confirmed trial results."""
from alembic import op
import sqlalchemy as sa


revision = 'mk0d0e000021'
down_revision = 'mj0d0e000020'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'trial_result_attachment',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('trial_result_id', sa.String(length=36), nullable=False),
        sa.Column('file_id', sa.String(length=36), nullable=False),
        sa.Column('role', sa.String(length=30), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('content_sha256', sa.String(length=64), nullable=False),
        sa.Column('linked_by', sa.String(length=36), nullable=False),
        sa.Column('previous_id', sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "role IN ('TRIAL_REPORT')",
            name='trial_result_attachment_role',
        ),
        sa.CheckConstraint(
            'version > 0',
            name='trial_result_attachment_version',
        ),
        sa.CheckConstraint(
            'previous_id IS NULL OR previous_id <> id',
            name='trial_result_attachment_previous_not_self',
        ),
        sa.ForeignKeyConstraint(['trial_result_id'], ['trial_result.id']),
        sa.ForeignKeyConstraint(['file_id'], ['file_object.id']),
        sa.ForeignKeyConstraint(['linked_by'], ['app_user.id']),
        sa.ForeignKeyConstraint(
            ['previous_id'],
            ['trial_result_attachment.id'],
            name='fk_trial_result_attachment_previous',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'trial_result_id',
            'version',
            name='trial_result_attachment_unique_version',
        ),
        sa.UniqueConstraint(
            'trial_result_id',
            'file_id',
            'role',
            name='trial_result_attachment_unique_file',
        ),
        sa.UniqueConstraint(
            'previous_id',
            name='trial_result_attachment_unique_previous',
        ),
    )
    op.create_index(
        op.f('ix_trial_result_attachment_trial_result_id'),
        'trial_result_attachment',
        ['trial_result_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_trial_result_attachment_file_id'),
        'trial_result_attachment',
        ['file_id'],
        unique=False,
    )
    op.execute(
        """CREATE FUNCTION protect_trial_result_attachment_fact() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'trial result attachment facts are immutable';
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        'CREATE TRIGGER trial_result_attachment_immutable '
        'BEFORE UPDATE OR DELETE ON trial_result_attachment FOR EACH ROW '
        'EXECUTE FUNCTION protect_trial_result_attachment_fact()'
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text('SELECT EXISTS (SELECT 1 FROM trial_result_attachment)')
    ):
        raise RuntimeError(
            'Trial result attachment evidence exists; refusing to discard report links'
        )
    op.execute(
        'DROP TRIGGER trial_result_attachment_immutable ON trial_result_attachment'
    )
    op.execute('DROP FUNCTION protect_trial_result_attachment_fact()')
    op.drop_index(
        op.f('ix_trial_result_attachment_file_id'),
        table_name='trial_result_attachment',
    )
    op.drop_index(
        op.f('ix_trial_result_attachment_trial_result_id'),
        table_name='trial_result_attachment',
    )
    op.drop_table('trial_result_attachment')
