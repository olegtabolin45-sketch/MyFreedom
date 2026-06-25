"""Денежные остатки портфеля (свободные средства по валютам)

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-24

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portfolio_cash",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["email"], ["users.email"], ondelete="CASCADE"),
    )
    op.create_index("ix_cash_email", "portfolio_cash", ["email"])


def downgrade() -> None:
    op.drop_index("ix_cash_email", table_name="portfolio_cash")
    op.drop_table("portfolio_cash")
