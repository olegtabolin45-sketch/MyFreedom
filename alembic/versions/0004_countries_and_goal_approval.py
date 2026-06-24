"""Страны проживания и статус утверждения цели

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-24

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Коды стран через запятую (например, "RU,GE,AE")
    op.add_column("user_goals", sa.Column("countries", sa.Text(), nullable=True))
    op.add_column(
        "user_goals",
        sa.Column(
            "goal_approved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("user_goals", "goal_approved")
    op.drop_column("user_goals", "countries")
