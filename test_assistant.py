```python
import os
import tempfile
import unittest
from unittest.mock import patch

from core.assistant import FridayAssistant
from core.memory import FridayMemory
from core.audit import AuditLog


class TestFridayAssistant(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

        database = os.path.join(
            self.temp_dir.name,
            "test_friday.db"
        )
        audit_file = os.path.join(
            self.temp_dir.name,
            "test_audit.log"
        )

        with patch(
            "core.assistant.FridayMemory"
        ), patch(
            "core.assistant.AuditLog"
        ), patch(
            "core.assistant.ToolManager"
        ):
            self.assistant = FridayAssistant()

        self.assistant.memory = FridayMemory(database)
        self.assistant.audit = AuditLog(audit_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_guest_cannot_save_memory(self):
        response = self.assistant.execute(
            "remember",
            "lembre que gosto de Python",
            "guest"
        )

        self.assertEqual(
            response,
            "Acesso negado para esta ação."
        )
        self.assertEqual(
            self.assistant.memory.get_all(),
            []
        )

    def test_user_cannot_save_memory(self):
        response = self.assistant.execute(
            "remember",
            "lembre que gosto de Python",
            "user"
        )

        self.assertEqual(
            response,
            "Acesso negado para esta ação."
        )
        self.assertEqual(
            self.assistant.memory.get_all(),
            []
        )

    def test_owner_can_save_memory(self):
        response = self.assistant.execute(
            "remember",
            "lembre que gosto de Python",
            "owner"
        )

        self.assertIn("Informação armazenada", response)
        self.assertEqual(
            self.assistant.memory.get_all(),
            ["gosto de Python"]
        )

    def test_unknown_role_cannot_use_ai(self):
        with patch(
            "core.ai_provider.FridayAIProvider"
        ) as provider_class:
            response = self.assistant.execute(
                "unknown",
                "Explique inteligência artificial",
                "unknown_role"
            )

        self.assertEqual(
            response,
            "Acesso negado para esta ação."
        )
        provider_class.assert_not_called()

    def test_memory_retrieval_finds_relevant_content(self):
        self.assistant.memory.save(
            "O comandante está estudando Python",
            "estudos"
        )
        self.assistant.memory.save(
            "O projeto FRIDAY usa OpenRouter",
            "projeto"
        )

        memories = self.assistant.retrieve_relevant_memories(
            "Estou estudando Python"
        )

        self.assertIn(
            "O comandante está estudando Python",
            memories
        )

    def test_empty_memory_returns_empty_list(self):
        memories = self.assistant.retrieve_relevant_memories(
            "Qualquer pergunta"
        )

        self.assertEqual(memories, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
```