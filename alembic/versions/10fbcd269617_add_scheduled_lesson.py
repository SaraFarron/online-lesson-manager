"""add scheduled lesson

Revision ID: 10fbcd269617
Revises: a049f3b78bc4
Create Date: 2024-09-04 17:48:59.523898

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '10fbcd269617'
down_revision: Union[str, None] = 'a049f3b78bc4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
