"""teachers

Revision ID: f9f2d9c327b2
Revises: 65fa8193a492
Create Date: 2026-09-09 01:06:18.529251

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9f2d9c327b2'
down_revision: Union[str, Sequence[str], None] = '65fa8193a492'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('notification',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('receivers', sa.String(length=255), nullable=False),
    sa.Column('notification_type', sa.String(length=50), nullable=False),
    sa.Column('message', sa.String(length=1024), nullable=False),
    sa.Column('is_sent', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('notification_pkey'))
    )
    
    # Add teacher_id as NULLABLE first
    with op.batch_alter_table('event', schema=None) as batch_op:
        batch_op.add_column(sa.Column('teacher_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(batch_op.f('event_teacher_id_fkey'), 'user', ['teacher_id'], ['id'])
    
    # Populate existing rows: teacher_id = user_id (assume creator is the teacher)
    op.execute(sa.text('UPDATE event SET teacher_id = user_id'))
    
    # Now make it NOT NULL
    with op.batch_alter_table('event', schema=None) as batch_op:
        batch_op.alter_column('teacher_id', existing_type=sa.Uuid(), nullable=False)
    
    # Same for recurrent_event
    with op.batch_alter_table('recurrent_event', schema=None) as batch_op:
        batch_op.add_column(sa.Column('teacher_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(batch_op.f('recurrent_event_teacher_id_fkey'), 'user', ['teacher_id'], ['id'])
    
    op.execute(sa.text('UPDATE recurrent_event SET teacher_id = user_id'))
    
    with op.batch_alter_table('recurrent_event', schema=None) as batch_op:
        batch_op.alter_column('teacher_id', existing_type=sa.Uuid(), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('recurrent_event', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('recurrent_event_teacher_id_fkey'), type_='foreignkey')
        batch_op.drop_column('teacher_id')

    with op.batch_alter_table('event', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('event_teacher_id_fkey'), type_='foreignkey')
        batch_op.drop_column('teacher_id')

    op.drop_table('notification')
