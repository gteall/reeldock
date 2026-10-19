"""Persist search overrides and matching evidence without changing existing metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("movie_records", sa.Column("search_query", sa.Text(), nullable=True))
    op.add_column(
        "movie_records", sa.Column("match_evidence", sa.JSON(), nullable=False, server_default="{}")
    )


def downgrade():
    op.drop_column("movie_records", "match_evidence")
    op.drop_column("movie_records", "search_query")
