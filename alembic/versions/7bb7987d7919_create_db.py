"""Create db

Revision ID: 7bb7987d7919
Revises: 10fbcd269617
Create Date: 2024-09-04 19:14:51.643609

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7bb7987d7919'
down_revision: Union[str, None] = '10fbcd269617'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
