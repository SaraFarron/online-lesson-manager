"""correct v1.0.0 version db

Revision ID: 377fda9f1ab5
Revises: 5a29eb4e8832
Create Date: 2024-12-20 11:29:42.579832

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '377fda9f1ab5'
down_revision: Union[str, None] = '5a29eb4e8832'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('lesson', sa.Column('start_time', sa.Time(), nullable=False))
    op.drop_column('lesson', 'time')
    with op.batch_alter_table("restricted_time") as batch_op:
        batch_op.alter_column('start_time',
                   existing_type=sa.TIME(),
                   nullable=False)
        batch_op.alter_column('end_time',
                   existing_type=sa.TIME(),
                   nullable=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('restricted_time', 'end_time',
               existing_type=sa.TIME(),
               nullable=True)
    op.alter_column('restricted_time', 'start_time',
               existing_type=sa.TIME(),
               nullable=True)
    op.add_column('lesson', sa.Column('time', sa.TIME(), nullable=False))
    op.drop_column('lesson', 'start_time')
    # ### end Alembic commands ###
