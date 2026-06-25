"""Слияние отчётов: денежные потоки, мета портфеля, флаг FX у сделок

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-24

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Дивиденды/купоны и налоги (для расчёта прибыли и XIRR)
    op.create_table(
        "portfolio_cashflows",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("flow_date", sa.String(length=20), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),  # dividend | tax
        sa.Column("amount", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["email"], ["users.email"], ondelete="CASCADE"),
    )
    op.create_index("ix_cashflows_email", "portfolio_cashflows", ["email"])

    # Дата, на которую актуальны позиции (для выбора самого свежего отчёта)
    op.create_table(
        "portfolio_meta",
        sa.Column("email", sa.String(length=255), primary_key=True),
        sa.Column("positions_asof", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["email"], ["users.email"], ondelete="CASCADE"),
    )

    # Флаг валютной/металлической сделки (исключается из P&L бумаг)
    op.add_column(
        "portfolio_trades",
        sa.Column("is_fx", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("portfolio_trades", "is_fx")
    op.drop_table("portfolio_meta")
    op.drop_index("ix_cashflows_email", table_name="portfolio_cashflows")
    op.drop_table("portfolio_cashflows")
