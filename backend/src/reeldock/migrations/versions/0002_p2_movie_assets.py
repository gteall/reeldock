"""P2 durable discovery, matching, provider cache and asset checkpoints."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assets", sa.Column("cache_key", sa.String(64), nullable=True))
    op.add_column(
        "assets", sa.Column("managed", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "assets", sa.Column("remote_snapshot", sa.JSON(), nullable=False, server_default="{}")
    )
    op.add_column("assets", sa.Column("source_tmdb_id", sa.String(32), nullable=True))
    op.create_table(
        "movie_records",
        sa.Column("package_id", sa.String(32), sa.ForeignKey("packages.id"), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("media_path", sa.Text(), nullable=True),
        sa.Column("stable_since", sa.Float(), nullable=False),
        sa.Column("last_seen", sa.Float(), nullable=False),
        sa.Column("scan_status", sa.String(40), nullable=False),
        sa.Column("match_status", sa.String(40), nullable=False),
        sa.Column("manual_id", sa.String(32), nullable=True),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("cast", sa.JSON(), nullable=False),
        sa.Column("artwork", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
    )
    op.create_table(
        "provider_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )


def downgrade():
    op.drop_table("provider_cache")
    op.drop_table("movie_records")
    op.drop_column("assets", "source_tmdb_id")
    op.drop_column("assets", "remote_snapshot")
    op.drop_column("assets", "managed")
    op.drop_column("assets", "cache_key")
