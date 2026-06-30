"""Интеграции с брокерами по API: зашифрованные токены + привязка счёта

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-30

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "broker_integrations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("token_enc", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["email"], ["users.email"], ondelete="CASCADE"),
        sa.UniqueConstraint("email", "provider", name="uq_broker_integration"),
    )

    # Привязка портфеля к источнику и брокерскому счёту (для синхронизации по API)
    op.add_column("portfolios", sa.Column("source", sa.String(length=20), nullable=True))
    op.add_column(
        "portfolios", sa.Column("broker_account_id", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("portfolios", "broker_account_id")
    op.drop_column("portfolios", "source")
    op.drop_table("broker_integrations")
