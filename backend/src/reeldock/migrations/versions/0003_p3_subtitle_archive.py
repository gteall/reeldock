"""P3 durable subtitle evidence and archive state. Intents already exist in P1."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "packages", sa.Column("subtitle_evidence", sa.JSON(), nullable=False, server_default="{}")
    )
    op.add_column(
        "packages",
        sa.Column("archive_status", sa.String(40), nullable=False, server_default="not_started"),
    )


def downgrade():
    op.drop_column("packages", "archive_status")
    op.drop_column("packages", "subtitle_evidence")
