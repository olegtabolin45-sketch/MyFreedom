"""Мультипортфели: таблица portfolios + привязка данных к портфелю

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-25

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Таблицы с данными портфеля, которым добавляем portfolio_id
_DATA_TABLES = ("portfolio_positions", "portfolio_trades", "portfolio_cashflows", "portfolio_cash")


def upgrade() -> None:
    op.create_table(
        "portfolios",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="RUB"),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="broker"),
        sa.Column("broker_commission", sa.Float(), nullable=True),
        sa.Column("positions_asof", sa.String(length=20), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["email"], ["users.email"], ondelete="CASCADE"),
    )
    op.create_index("ix_portfolios_email", "portfolios", ["email"])

    # Привязка данных к портфелю (nullable: старые строки осиротеют — в dev допустимо)
    for table in _DATA_TABLES:
        op.add_column(table, sa.Column("portfolio_id", sa.BigInteger(), nullable=True))
        op.create_index(f"ix_{table}_portfolio", table, ["portfolio_id"])
        op.create_foreign_key(
            f"fk_{table}_portfolio",
            table,
            "portfolios",
            ["portfolio_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    for table in _DATA_TABLES:
        op.drop_constraint(f"fk_{table}_portfolio", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_portfolio", table_name=table)
        op.drop_column(table, "portfolio_id")
    op.drop_index("ix_portfolios_email", table_name="portfolios")
    op.drop_table("portfolios")
