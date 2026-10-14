"""Store Telegram identity on the shared account.

Revision ID: 8bb41e3a1001
Revises: f9f2d9c327b2
"""

import sqlalchemy as sa
from alembic import op

revision = "8bb41e3a1001"
down_revision = "f9f2d9c327b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.alter_column("email", existing_type=sa.String(256), nullable=True)
        batch.alter_column(
            "hashed_password", existing_type=sa.String(1024), nullable=True
        )
        batch.add_column(sa.Column("telegram_user_id", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column("telegram_private_chat_id", sa.BigInteger(), nullable=True)
        )
        batch.create_unique_constraint("user_telegram_user_id_key", ["telegram_user_id"])


def downgrade() -> None:
    count = op.get_bind().execute(
        sa.text(
            'SELECT count(*) FROM "user" WHERE email IS NULL '
            "OR hashed_password IS NULL OR telegram_user_id IS NOT NULL "
            "OR telegram_private_chat_id IS NOT NULL"
        )
    ).scalar_one()
    if count:
        raise RuntimeError(
            "Cannot downgrade Telegram identity: accounts have missing credentials "
            "or Telegram bindings. Export/migrate these accounts and explicitly "
            "clear their bindings before retrying; no accounts were deleted."
        )
    with op.batch_alter_table("user") as batch:
        batch.drop_constraint("user_telegram_user_id_key", type_="unique")
        batch.drop_column("telegram_private_chat_id")
        batch.drop_column("telegram_user_id")
        batch.alter_column("email", existing_type=sa.String(256), nullable=False)
        batch.alter_column(
            "hashed_password", existing_type=sa.String(1024), nullable=False
        )
