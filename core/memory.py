from contextlib import contextmanager
import sqlite3

from core.database import DATABASE_URL, DB_PATH, get_db


class FridayMemory:
    """Memory store; default connection shares FRIDAY DB, explicit paths remain SQLite test support."""

    def __init__(self, database=None):
        self.database = database
        self.shared = database is None
        self.create_table()

    def connect(self):
        return get_db() if self.shared else sqlite3.connect(self.database)

    @contextmanager
    def _connection(self):
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _sql(self, sqlite_sql, postgres_sql=None):
        if self.shared and DATABASE_URL:
            return postgres_sql or sqlite_sql.replace("?", "%s")
        return sqlite_sql

    def create_table(self):
        with self._connection() as connection:
            cursor = connection.cursor()
            if self.shared and DATABASE_URL:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS memories (
                        id BIGSERIAL PRIMARY KEY,
                        content TEXT NOT NULL,
                        category TEXT NOT NULL DEFAULT 'geral'
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS memories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        category TEXT NOT NULL DEFAULT 'geral'
                    )
                """)
                cursor.execute("PRAGMA table_info(memories)")
                columns = {column[1] for column in cursor.fetchall()}
                if "category" not in columns:
                    cursor.execute("ALTER TABLE memories ADD COLUMN category TEXT NOT NULL DEFAULT 'geral'")
            cursor.close()

    def save(self, content, category="geral"):
        with self._connection() as connection:
            cursor = connection.cursor()
            if self.shared and DATABASE_URL:
                cursor.execute("INSERT INTO memories(content,category) VALUES (%s,%s) RETURNING id", (content,category))
                result = cursor.fetchone()[0]
            else:
                cursor.execute("INSERT INTO memories(content,category) VALUES (?,?)", (content,category))
                result = cursor.lastrowid
            cursor.close()
            return result

    def get_all(self):
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT content FROM memories ORDER BY id")
            rows = cursor.fetchall()
            cursor.close()
        return [row[0] for row in rows]

    def get_by_category(self, category):
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(self._sql("SELECT content FROM memories WHERE category = ? ORDER BY id"), (category,))
            rows = cursor.fetchall()
            cursor.close()
        return [row[0] for row in rows]

    def get_details(self, category=None):
        with self._connection() as connection:
            cursor = connection.cursor()
            if category is None:
                cursor.execute("SELECT id,content,category FROM memories ORDER BY id")
            else:
                cursor.execute(self._sql("SELECT id,content,category FROM memories WHERE category = ? ORDER BY id"), (category,))
            rows = cursor.fetchall()
            cursor.close()
        return [{"id": row[0], "content": row[1], "category": row[2]} for row in rows]

    def update(self, memory_id, content, category=None):
        with self._connection() as connection:
            cursor = connection.cursor()
            if category is None:
                cursor.execute(self._sql("UPDATE memories SET content = ? WHERE id = ?"), (content,memory_id))
            else:
                cursor.execute(self._sql("UPDATE memories SET content = ?,category = ? WHERE id = ?"), (content,category,memory_id))
            changed = cursor.rowcount > 0
            cursor.close()
            return changed

    def delete(self, memory_id):
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(self._sql("DELETE FROM memories WHERE id = ?"), (memory_id,))
            changed = cursor.rowcount > 0
            cursor.close()
            return changed

    def delete_many(self, memory_ids):
        if not isinstance(memory_ids, (list, tuple, set)):
            raise TypeError("memory_ids deve ser uma lista de IDs")
        ids = list(dict.fromkeys(memory_ids))
        if not ids or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in ids):
            return False
        placeholders = ", ".join("%s" if self.shared and DATABASE_URL else "?" for _ in ids)
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(f"SELECT id FROM memories WHERE id IN ({placeholders})", ids)
            existing = {row[0] for row in cursor.fetchall()}
            if existing != set(ids):
                cursor.close()
                return False
            cursor.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", ids)
            cursor.close()
            return True
