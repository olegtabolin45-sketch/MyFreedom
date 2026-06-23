"""Начальная схема: users и user_goals

Revision ID: 0001
Revises:
Create Date: 2026-06-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_onboarded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "user_goals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("initial_capital", sa.Float(), nullable=False),
        sa.Column("monthly_deposit", sa.Float(), nullable=False),
        sa.Column("target_income", sa.Float(), nullable=False),
        sa.Column("years_horizon", sa.Integer(), nullable=False),
        sa.Column("risk_profile", sa.String(length=50), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("email", name="uq_user_goals_email"),
        sa.ForeignKeyConstraint(["email"], ["users.email"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("user_goals")
    op.drop_table("users")
