"""add geocode cache

Revision ID: 4c1d2e3f5a6b
Revises: 9e4e71063e00
Create Date: 2026-10-16 10:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "4c1d2e3f5a6b"
down_revision: Union[str, Sequence[str], None] = "9e4e71063e00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "geocode_cache",
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("place", sa.String(length=255), nullable=False),
        sa.Column(
            "looked_up_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("geocode_cache")
