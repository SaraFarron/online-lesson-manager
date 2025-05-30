import sqlite3
from datetime import datetime, date, timedelta


def find_closest_weekday(weekday: int) -> date:
    jan1 = date(2025, 1, 1)
    delta = (weekday - jan1.weekday()) % 7
    return jan1 + timedelta(days=delta if delta <= 3 else delta - 7)


class DatabaseMigrator:
    def __init__(self, source_path: str, dest_path: str):
        """Initialize migrator with source and destination DB paths"""
        self.source_path = source_path
        self.dest_path = dest_path

        # Will be initialized in connect()
        self.source_conn = None
        self.dest_conn = None
        self.source_cur = None
        self.dest_cur = None

    def connect(self):
        """Establish connections to both databases"""
        self.source_conn = sqlite3.connect(self.source_path)
        self.dest_conn = sqlite3.connect(self.dest_path)
        self.source_cur = self.source_conn.cursor()
        self.dest_cur = self.dest_conn.cursor()

    def disconnect(self):
        """Close database connections"""
        if self.source_conn:
            self.source_conn.close()
        if self.dest_conn:
            self.dest_conn.close()

    def _prepare_table_migration(self, table_name: str, dest_columns: dict[str, str], primary_key: str = None) -> str:
        """
        Prepare table for migration with custom destination schema

        Args:
            table_name: Name of the table to create
            dest_columns: Dictionary of {column_name: column_type} for new table
            primary_key: Optional name of primary key column

        Returns:
            Prepared INSERT SQL statement with parameter placeholders
        """
        # Build CREATE TABLE statement
        columns_def = []
        for name, type_def in dest_columns.items():
            columns_def.append(f"{name} {type_def}")

        # Prepare parameterized insert statement
        column_names = list(dest_columns.keys())
        placeholders = ", ".join(["?"] * len(column_names))
        insert_sql = f"INSERT INTO {table_name} ({', '.join(column_names)}) VALUES ({placeholders})"

        return insert_sql

    def migrate_users(self):
        dest_schema = {
            "telegram_id": "INTEGER UNIQUE",
            "username": "TEXT",
            "full_name": "TEXT",
            "role": "TEXT NOT NULL",
            "executor_id": "INTEGER references executors",
            "id": "INTEGER NOT NULL",
        }
        insert_sql = self._prepare_table_migration(table_name="users", dest_columns=dest_schema, primary_key="id")

        self.source_cur.execute("SELECT id, telegram_id, name, teacher_id, telegram_username FROM user_account")
        for id, telegram_id, name, teacher_id, telegram_username in self.source_cur.fetchall():
            transformed_row = (
                telegram_id,
                telegram_username,
                name,
                "STUDENT",
                teacher_id,
                id,
            )
            self.dest_cur.execute(insert_sql, transformed_row)

    def migrate_lessons(self):
        """Migrate lessons table - implement your logic here"""
        dest_schema = {
            "cancelled": "BOOLEAN",
            "reschedule_id": "INTEGER references events",
            "is_reschedule": "BOOLEAN",
            "user_id": "INTEGER NOT NULL",
            "executor_id": "INTEGER NOT NULL references executors",
            "event_type": "TEXT",
            "start": "DATETIME",
            "end": "DATETIME",
            "id": "INTEGER NOT NULL",
        }
        insert_sql = self._prepare_table_migration(table_name="events", dest_columns=dest_schema, primary_key="id")

        self.source_cur.execute("SELECT id, user_id, date, end_time, status, start_time FROM lesson")
        for id, user_id, date, end_time, status, start_time in self.source_cur.fetchall():
            transformed_row = (
                False,
                None,
                False,
                user_id,
                2,
                "Урок",
                date + " " + start_time,
                date + " " + end_time,
                id,
            )
            self.dest_cur.execute(insert_sql, transformed_row)

        # scheduled lessons
        dest_schema = {
            "interval": "INTEGER",
            "interval_end": "DATETIME",
            "user_id": "INTEGER references users",
            "executor_id": "INTEGER NOT NULL references executors",
            "event_type": "TEXT",
            "start": "DATETIME",
            "end": "DATETIME",
            "id": "INTEGER NOT NULL",
        }
        insert_sql = self._prepare_table_migration(table_name="recurrent_events", dest_columns=dest_schema, primary_key="id")

        self.source_cur.execute("SELECT id, user_id, weekday, start_time, end_time FROM scheduled_lesson")
        for id, user_id, weekday, start_time, end_time in self.source_cur.fetchall():
            weekday_date = find_closest_weekday(weekday)
            transformed_row = (
                7,
                None,
                user_id,
                2,
                "Урок",
                str(weekday_date) + " " + start_time,
                str(weekday_date) + " " + end_time,
                id,
            )
            self.dest_cur.execute(insert_sql, transformed_row)

    def migrate_all(self):
        """Execute all migrations in proper order"""
        try:
            self.connect()

            # Execute migrations in logical order
            self.migrate_users()
            self.migrate_lessons()
            self.dest_conn.commit()

            print("Migration completed successfully!")
        except Exception as e:
            print(f"Migration failed: {e!s}")
            if self.dest_conn:
                self.dest_conn.rollback()
            raise e
        finally:
            self.disconnect()

def migrate_db(source_path: str, dest_path: str):
    """Main migration function"""
    migrator = DatabaseMigrator(source_path, dest_path)
    migrator.migrate_all()

if __name__ == "__main__":
    # Example usage
    source_db = "prod2.sqlite"
    dest_db = "new_database.sqlite"

    migrate_db(source_db, dest_db)
