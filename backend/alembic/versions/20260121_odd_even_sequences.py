"""use_odd_even_sequences_for_events

Revision ID: a1b2c3d4e5f6
Revises: e259b6c5838e
Create Date: 2026-01-21

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'e259b6c5838e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # First, remove the default from columns to drop old sequences
    op.execute("ALTER TABLE events ALTER COLUMN id DROP DEFAULT")
    op.execute("ALTER TABLE recurrent_events ALTER COLUMN id DROP DEFAULT")
    
    # Drop old sequences if they exist
    op.execute("DROP SEQUENCE IF EXISTS events_id_seq CASCADE")
    op.execute("DROP SEQUENCE IF EXISTS recurrent_events_id_seq CASCADE")
    
    # Get the max IDs to set correct starting points
    # Events: start at next odd number after max ID
    # RecurrentEvents: start at next even number after max ID
    op.execute("""
        DO $$
        DECLARE
            max_event_id INTEGER;
            max_recurrent_id INTEGER;
            next_odd INTEGER;
            next_even INTEGER;
        BEGIN
            -- Get max IDs (default to 0 if no records)
            SELECT COALESCE(MAX(id), 0) INTO max_event_id FROM events;
            SELECT COALESCE(MAX(id), 0) INTO max_recurrent_id FROM recurrent_events;
            
            -- Calculate next odd number (for events)
            IF max_event_id = 0 THEN
                next_odd := 1;
            ELSIF max_event_id % 2 = 0 THEN
                next_odd := max_event_id + 1;
            ELSE
                next_odd := max_event_id + 2;
            END IF;
            
            -- Calculate next even number (for recurrent_events)
            IF max_recurrent_id = 0 THEN
                next_even := 2;
            ELSIF max_recurrent_id % 2 = 1 THEN
                next_even := max_recurrent_id + 1;
            ELSE
                next_even := max_recurrent_id + 2;
            END IF;
            
            -- Create sequences with correct starting points
            EXECUTE format('CREATE SEQUENCE events_id_seq START %s INCREMENT 2', next_odd);
            EXECUTE format('CREATE SEQUENCE recurrent_events_id_seq START %s INCREMENT 2', next_even);
        END $$;
    """)
    
    # Set the new sequences as defaults
    op.execute("ALTER TABLE events ALTER COLUMN id SET DEFAULT nextval('events_id_seq')")
    op.execute("ALTER TABLE recurrent_events ALTER COLUMN id SET DEFAULT nextval('recurrent_events_id_seq')")
    
    # Set sequence ownership
    op.execute("ALTER SEQUENCE events_id_seq OWNED BY events.id")
    op.execute("ALTER SEQUENCE recurrent_events_id_seq OWNED BY recurrent_events.id")


def downgrade() -> None:
    # Remove defaults
    op.execute("ALTER TABLE events ALTER COLUMN id DROP DEFAULT")
    op.execute("ALTER TABLE recurrent_events ALTER COLUMN id DROP DEFAULT")
    
    # Drop odd/even sequences
    op.execute("DROP SEQUENCE IF EXISTS events_id_seq CASCADE")
    op.execute("DROP SEQUENCE IF EXISTS recurrent_events_id_seq CASCADE")
    
    # Recreate standard sequences
    op.execute("""
        DO $$
        DECLARE
            max_event_id INTEGER;
            max_recurrent_id INTEGER;
        BEGIN
            SELECT COALESCE(MAX(id), 0) + 1 INTO max_event_id FROM events;
            SELECT COALESCE(MAX(id), 0) + 1 INTO max_recurrent_id FROM recurrent_events;
            
            EXECUTE format('CREATE SEQUENCE events_id_seq START %s INCREMENT 1', max_event_id);
            EXECUTE format('CREATE SEQUENCE recurrent_events_id_seq START %s INCREMENT 1', max_recurrent_id);
        END $$;
    """)
    
    # Restore defaults
    op.execute("ALTER TABLE events ALTER COLUMN id SET DEFAULT nextval('events_id_seq')")
    op.execute("ALTER TABLE recurrent_events ALTER COLUMN id SET DEFAULT nextval('recurrent_events_id_seq')")
