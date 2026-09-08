"""add immich asset mappings

Revision ID: 9fe7d36d42c1
Revises: 543b0c11a790
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9fe7d36d42c1"
down_revision: str | None = "543b0c11a790"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "immich_assets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mediaitem_id", sa.UUID(), nullable=False),
        sa.Column("variant", sa.String(), nullable=False),
        sa.Column("asset_id", sa.UUID(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["mediaitem_id"], ["mediaitems.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mediaitem_id", "variant"),
    )
    op.create_index(op.f("ix_immich_assets_mediaitem_id"), "immich_assets", ["mediaitem_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_immich_assets_mediaitem_id"), table_name="immich_assets")
    op.drop_table("immich_assets")
