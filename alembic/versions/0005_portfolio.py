"""Портфель: позиции и сделки (импорт из брокерского отчёта)

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-24

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portfolio_positions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("ticker", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("isin", sa.String(length=20), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["email"], ["users.email"], ondelete="CASCADE"),
    )
    op.create_index("ix_positions_email", "portfolio_positions", ["email"])

    op.create_table(
        "portfolio_trades",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("trade_date", sa.String(length=20), nullable=True),
        sa.Column("trade_time", sa.String(length=20), nullable=True),
        sa.Column("side", sa.String(length=20), nullable=True),
        sa.Column("ticker", sa.String(length=50), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("commission", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["email"], ["users.email"], ondelete="CASCADE"),
    )
    op.create_index("ix_trades_email", "portfolio_trades", ["email"])


def downgrade() -> None:
    op.drop_index("ix_trades_email", table_name="portfolio_trades")
    op.drop_table("portfolio_trades")
    op.drop_index("ix_positions_email", table_name="portfolio_positions")
    op.drop_table("portfolio_positions")
