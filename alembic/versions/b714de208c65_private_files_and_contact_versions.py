"""Private originals and immutable contact attachment versions."""
from alembic import op
import sqlalchemy as sa
revision='b714de208c65'
down_revision='a503fe901624'
branch_labels=None
depends_on=None


def identity():return [sa.Column('id',sa.String(36),primary_key=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False)]


def upgrade():
    op.create_table('file_object',*identity(),
        sa.Column('owner_id',sa.String(36),sa.ForeignKey('app_user.id'),nullable=False),
        sa.Column('conversation_id',sa.String(36),sa.ForeignKey('ai_conversation.id'),nullable=False),
        sa.Column('request_key',sa.String(36),nullable=False),sa.Column('filename',sa.String(200),nullable=False),
        sa.Column('media_type',sa.String(120),nullable=False),sa.Column('size',sa.Integer(),nullable=False),
        sa.Column('sha256',sa.String(64),nullable=False),sa.Column('backend',sa.String(10),nullable=False),
        sa.Column('storage_namespace',sa.String(200),nullable=False),sa.Column('object_key',sa.String(150),nullable=False,unique=True),
        sa.Column('storage_version',sa.String(1024)),sa.UniqueConstraint('owner_id','request_key'),
        sa.CheckConstraint('size > 0'),sa.CheckConstraint("backend IN ('local','s3')"))
    for col in ('owner_id','conversation_id'):op.create_index('ix_file_object_'+col,'file_object',[col])
    op.create_table('contact_attachment',*identity(),
        sa.Column('case_id',sa.String(36),sa.ForeignKey('contact_case.id'),nullable=False),
        sa.Column('file_id',sa.String(36),sa.ForeignKey('file_object.id'),nullable=False),
        sa.Column('document_id',sa.String(36),nullable=False),sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('title',sa.String(150),nullable=False),
        sa.Column('previous_id',sa.String(36),sa.ForeignKey('contact_attachment.id')),
        sa.Column('created_by',sa.String(36),sa.ForeignKey('app_user.id'),nullable=False),
        sa.UniqueConstraint('case_id','document_id','version'),sa.UniqueConstraint('case_id','file_id'),sa.CheckConstraint('version > 0'))
    for col in ('case_id','file_id'):op.create_index('ix_contact_attachment_'+col,'contact_attachment',[col])
    op.create_table('agent_run_file',sa.Column('run_id',sa.String(36),sa.ForeignKey('ai_run.id'),primary_key=True),
        sa.Column('file_id',sa.String(36),sa.ForeignKey('file_object.id'),primary_key=True))
    op.execute("""CREATE FUNCTION protect_attachment_fact() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'File originals and attachment versions are immutable'; END $$""")
    for table in ('file_object','contact_attachment','agent_run_file'):
        op.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_attachment_fact()')


def downgrade():
    for table in ('agent_run_file','contact_attachment','file_object'):op.drop_table(table)
    op.execute('DROP FUNCTION protect_attachment_fact()')
