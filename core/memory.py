import sqlite3


class FridayMemory:

    def __init__(self, database="friday.db"):
        self.database = database
        self.create_table()

    def connect(self):
        return sqlite3.connect(self.database)

    def create_table(self):
        with self.connect() as connection:
            cursor = connection.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL
                )
            """)

            cursor.execute("PRAGMA table_info(memories)")
            columns = {
                column[1]
                for column in cursor.fetchall()
            }

            if "category" not in columns:
                cursor.execute("""
                    ALTER TABLE memories
                    ADD COLUMN category TEXT NOT NULL
                    DEFAULT 'geral'
                """)

    def save(self, content, category="geral"):
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memories (content, category)
                VALUES (?, ?)
                """,
                (content, category)
            )
            return cursor.lastrowid

    def get_all(self):
        with self.connect() as connection:
            cursor = connection.execute(
                "SELECT content FROM memories ORDER BY id"
            )
            memories = cursor.fetchall()

        return [memory[0] for memory in memories]

    def get_by_category(self, category):
        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT content FROM memories
                WHERE category = ?
                ORDER BY id
                """,
                (category,)
            )
            memories = cursor.fetchall()

        return [memory[0] for memory in memories]

    def get_details(self, category=None):
        with self.connect() as connection:
            if category is None:
                cursor = connection.execute("""
                    SELECT id, content, category
                    FROM memories
                    ORDER BY id
                """)
            else:
                cursor = connection.execute(
                    """
                    SELECT id, content, category
                    FROM memories
                    WHERE category = ?
                    ORDER BY id
                    """,
                    (category,)
                )

            memories = cursor.fetchall()

        return [
            {
                "id": memory[0],
                "content": memory[1],
                "category": memory[2],
            }
            for memory in memories
        ]

    def update(self, memory_id, content, category=None):
        with self.connect() as connection:
            if category is None:
                cursor = connection.execute(
                    """
                    UPDATE memories
                    SET content = ?
                    WHERE id = ?
                    """,
                    (content, memory_id)
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE memories
                    SET content = ?, category = ?
                    WHERE id = ?
                    """,
                    (content, category, memory_id)
                )

            return cursor.rowcount > 0

    def delete(self, memory_id):
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM memories WHERE id = ?",
                (memory_id,)
            )

            return cursor.rowcount > 0
            