"""use_native_datetime_types

Revision ID: e2a37259f759
Revises: 79f9a238df02
Create Date: 2026-01-13 19:22:40.593741

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2a37259f759'
down_revision: Union[str, None] = '79f9a238df02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL for type conversion with USING clause
    op.execute(
        "ALTER TABLE teacher_settings "
        "ALTER COLUMN work_start TYPE TIME WITHOUT TIME ZONE "
        "USING work_start::time without time zone"
    )
    op.execute(
        "ALTER TABLE teacher_settings "
        "ALTER COLUMN work_end TYPE TIME WITHOUT TIME ZONE "
        "USING work_end::time without time zone"
    )


def downgrade() -> None:
    # Convert back to VARCHAR
    op.execute(
        "ALTER TABLE teacher_settings "
        "ALTER COLUMN work_start TYPE VARCHAR(5) "
        "USING to_char(work_start, 'HH24:MI')"
    )
    op.execute(
        "ALTER TABLE teacher_settings "
        "ALTER COLUMN work_end TYPE VARCHAR(5) "
        "USING to_char(work_end, 'HH24:MI')"
    )
