"""Link immutable inspection originals to confirmed outbound release facts."""
from alembic import op
import sqlalchemy as sa


revision = 'mm0d0e000023'
down_revision = 'ml0d0e000022'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'outbound_release_attachment',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('outbound_release_id', sa.String(length=36), nullable=False),
        sa.Column('file_id', sa.String(length=36), nullable=False),
        sa.Column('role', sa.String(length=40), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('content_sha256', sa.String(length=64), nullable=False),
        sa.Column('linked_by', sa.String(length=36), nullable=False),
        sa.Column('previous_id', sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "role IN ('RELEASE_EVIDENCE')",
            name='outbound_release_attachment_role',
        ),
        sa.CheckConstraint(
            'version > 0',
            name='outbound_release_attachment_version',
        ),
        sa.CheckConstraint(
            'previous_id IS NULL OR previous_id <> id',
            name='outbound_release_attachment_previous_not_self',
        ),
        sa.ForeignKeyConstraint(
            ['outbound_release_id'],
            ['outbound_release_record.id'],
        ),
        sa.ForeignKeyConstraint(['file_id'], ['file_object.id']),
        sa.ForeignKeyConstraint(['linked_by'], ['app_user.id']),
        sa.ForeignKeyConstraint(
            ['previous_id'],
            ['outbound_release_attachment.id'],
            name='fk_outbound_release_attachment_previous',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'outbound_release_id',
            'version',
            name='outbound_release_attachment_unique_version',
        ),
        sa.UniqueConstraint(
            'outbound_release_id',
            'file_id',
            'role',
            name='outbound_release_attachment_unique_file',
        ),
        sa.UniqueConstraint(
            'previous_id',
            name='outbound_release_attachment_unique_previous',
        ),
    )
    op.create_index(
        op.f('ix_outbound_release_attachment_outbound_release_id'),
        'outbound_release_attachment',
        ['outbound_release_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_outbound_release_attachment_file_id'),
        'outbound_release_attachment',
        ['file_id'],
        unique=False,
    )
    op.execute(
        """CREATE FUNCTION protect_outbound_release_attachment_fact() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'outbound release attachment facts are immutable';
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        'CREATE TRIGGER outbound_release_attachment_immutable '
        'BEFORE UPDATE OR DELETE ON outbound_release_attachment FOR EACH ROW '
        'EXECUTE FUNCTION protect_outbound_release_attachment_fact()'
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text('SELECT EXISTS (SELECT 1 FROM outbound_release_attachment)')
    ):
        raise RuntimeError(
            'Outbound release attachment evidence exists; refusing to discard links'
        )
    op.execute(
        'DROP TRIGGER outbound_release_attachment_immutable '
        'ON outbound_release_attachment'
    )
    op.execute('DROP FUNCTION protect_outbound_release_attachment_fact()')
    op.drop_index(
        op.f('ix_outbound_release_attachment_file_id'),
        table_name='outbound_release_attachment',
    )
    op.drop_index(
        op.f('ix_outbound_release_attachment_outbound_release_id'),
        table_name='outbound_release_attachment',
    )
    op.drop_table('outbound_release_attachment')
