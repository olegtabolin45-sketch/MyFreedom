"""Запасная цена позиции (от брокера) для бумаг без котировки MOEX

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-30

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "portfolio_positions",
        sa.Column("fallback_price", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portfolio_positions", "fallback_price")
