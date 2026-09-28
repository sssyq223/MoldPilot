"""Link immutable customer receipt originals to signature facts."""
from alembic import op
import sqlalchemy as sa


revision = 'mn0d0e000024'
down_revision = 'mm0d0e000023'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'customer_delivery_signature_attachment',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('signature_id', sa.String(length=36), nullable=False),
        sa.Column('file_id', sa.String(length=36), nullable=False),
        sa.Column('role', sa.String(length=40), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('content_sha256', sa.String(length=64), nullable=False),
        sa.Column('linked_by', sa.String(length=36), nullable=False),
        sa.Column('previous_id', sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "role IN ('SIGNATURE_EVIDENCE')",
            name='customer_delivery_signature_attachment_role',
        ),
        sa.CheckConstraint(
            'version > 0',
            name='customer_delivery_signature_attachment_version',
        ),
        sa.CheckConstraint(
            'previous_id IS NULL OR previous_id <> id',
            name='customer_delivery_signature_attachment_previous_not_self',
        ),
        sa.ForeignKeyConstraint(
            ['signature_id'],
            ['customer_delivery_signature.id'],
        ),
        sa.ForeignKeyConstraint(['file_id'], ['file_object.id']),
        sa.ForeignKeyConstraint(['linked_by'], ['app_user.id']),
        sa.ForeignKeyConstraint(
            ['previous_id'],
            ['customer_delivery_signature_attachment.id'],
            name='fk_customer_delivery_signature_attachment_previous',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'signature_id',
            'version',
            name='customer_delivery_signature_attachment_unique_version',
        ),
        sa.UniqueConstraint(
            'signature_id',
            'file_id',
            'role',
            name='customer_delivery_signature_attachment_unique_file',
        ),
        sa.UniqueConstraint(
            'previous_id',
            name='customer_delivery_signature_attachment_unique_previous',
        ),
    )
    op.create_index(
        op.f('ix_customer_delivery_signature_attachment_signature_id'),
        'customer_delivery_signature_attachment',
        ['signature_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_customer_delivery_signature_attachment_file_id'),
        'customer_delivery_signature_attachment',
        ['file_id'],
        unique=False,
    )
    op.execute(
        """CREATE FUNCTION protect_customer_delivery_signature_attachment_fact() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'customer delivery signature attachment facts are immutable';
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        'CREATE TRIGGER customer_delivery_signature_attachment_immutable '
        'BEFORE UPDATE OR DELETE ON customer_delivery_signature_attachment FOR EACH ROW '
        'EXECUTE FUNCTION protect_customer_delivery_signature_attachment_fact()'
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text('SELECT EXISTS (SELECT 1 FROM customer_delivery_signature_attachment)')
    ):
        raise RuntimeError(
            'Customer signature attachment evidence exists; refusing to discard links'
        )
    op.execute(
        'DROP TRIGGER customer_delivery_signature_attachment_immutable '
        'ON customer_delivery_signature_attachment'
    )
    op.execute('DROP FUNCTION protect_customer_delivery_signature_attachment_fact()')
    op.drop_index(
        op.f('ix_customer_delivery_signature_attachment_file_id'),
        table_name='customer_delivery_signature_attachment',
    )
    op.drop_index(
        op.f('ix_customer_delivery_signature_attachment_signature_id'),
        table_name='customer_delivery_signature_attachment',
    )
    op.drop_table('customer_delivery_signature_attachment')
