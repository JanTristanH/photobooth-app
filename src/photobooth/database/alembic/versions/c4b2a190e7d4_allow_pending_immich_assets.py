"""allow pending immich asset mappings

Revision ID: c4b2a190e7d4
Revises: 9fe7d36d42c1
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4b2a190e7d4"
down_revision: str | None = "9fe7d36d42c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("immich_assets") as batch_op:
        batch_op.alter_column("asset_id", existing_type=sa.UUID(), nullable=True)
        batch_op.alter_column(
            "uploaded_at",
            existing_type=sa.DateTime(timezone=True),
            existing_server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
            server_default=None,
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM immich_assets WHERE asset_id IS NULL"))
    with op.batch_alter_table("immich_assets") as batch_op:
        batch_op.alter_column("asset_id", existing_type=sa.UUID(), nullable=False)
        batch_op.alter_column(
            "uploaded_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        )
