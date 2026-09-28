"""Keep the exact ERP source used for a human project/mold handoff."""
from alembic import op
import sqlalchemy as sa


revision = "mo0d0e000025"
down_revision = "mn0d0e000024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "mold",
        sa.Column(
            "source_system",
            sa.String(length=20),
            nullable=False,
            server_default="MANUAL",
        ),
    )
    op.add_column("mold", sa.Column("source_ref", sa.String(length=300), nullable=True))
    op.add_column(
        "mold",
        sa.Column("source_as_of", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "mold_source_system",
        "mold",
        "source_system IN ('AGENT','ERP','MANUAL')",
    )


def downgrade():
    op.drop_constraint("mold_source_system", "mold", type_="check")
    op.drop_column("mold", "source_as_of")
    op.drop_column("mold", "source_ref")
    op.drop_column("mold", "source_system")
