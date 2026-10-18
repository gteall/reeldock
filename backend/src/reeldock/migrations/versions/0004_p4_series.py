"""P4 series hierarchy and independent per-media checkpoints; existing films preserved."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    for name, type_, default, nullable in [
        ("mapping_status", sa.String(40), "not_applicable", False),
        ("manual_mapping", sa.Boolean(), "0", False),
        ("details", sa.JSON(), "{}", False),
        ("artwork", sa.JSON(), "[]", False),
        ("metadata_version", sa.Integer(), None, True),
        ("original_language", sa.String(32), None, True),
        ("subtitle_status", sa.String(40), "pending", False),
        ("subtitle_reason", sa.String(80), None, True),
        ("subtitle_version", sa.Integer(), None, True),
        ("subtitle_evidence", sa.JSON(), "{}", False),
        ("error_code", sa.String(80), None, True),
    ]:
        op.add_column("media", sa.Column(name, type_, nullable=nullable, server_default=default))
    op.create_table(
        "seasons",
        sa.Column("package_id", sa.String(32), sa.ForeignKey("packages.id"), primary_key=True),
        sa.Column("number", sa.Integer(), primary_key=True),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("artwork", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("metadata_version", sa.Integer()),
    )
    with op.batch_alter_table("assets") as batch:
        batch.add_column(sa.Column("media_id", sa.String(32)))
        batch.add_column(sa.Column("season", sa.Integer()))
        batch.create_foreign_key("fk_assets_media", "media", ["media_id"], ["id"])
    op.create_index("ix_assets_media_id", "assets", ["media_id"])


def downgrade():
    op.drop_index("ix_assets_media_id", "assets")
    with op.batch_alter_table("assets") as batch:
        batch.drop_constraint("fk_assets_media", type_="foreignkey")
        batch.drop_column("season")
        batch.drop_column("media_id")
    op.drop_table("seasons")
    for name in [
        "error_code",
        "subtitle_evidence",
        "subtitle_version",
        "subtitle_reason",
        "subtitle_status",
        "original_language",
        "metadata_version",
        "artwork",
        "details",
        "manual_mapping",
        "mapping_status",
    ]:
        op.drop_column("media", name)
