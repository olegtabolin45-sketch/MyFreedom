"""Двухфакторная аутентификация: поля TOTP в users

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-24

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "is_2fa_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_2fa_enabled")
    op.drop_column("users", "totp_secret")
