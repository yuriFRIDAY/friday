
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

            # Migração compatível: adiciona a categoria
            # somente se a coluna ainda não existir.
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
            connection.execute(
                """
                INSERT INTO memories (content, category)
                VALUES (?, ?)
                """,
                (content, category)
            )

    def get_all(self):
        with self.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT content FROM memories ORDER BY id"
            )
            memories = cursor.fetchall()

        return [memory[0] for memory in memories]

    def get_by_category(self, category):
        with self.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT content FROM memories
                WHERE category = ?
                ORDER BY id
                """,
                (category,)
            )
            memories = cursor.fetchall()

        return [memory[0] for memory in memories]
