import os
import tempfile
import unittest
from unittest.mock import patch

from core.assistant import FridayAssistant
from core.memory import FridayMemory


class FakeSession:
    def __init__(self):
        self.pending_action = None


class TestMemoryDeletionFlow(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(
            self.temp_dir.name,
            "test_friday.db",
        )

        # Injeta o banco temporário antes de criar o assistente.
        with patch(
            "core.assistant.FridayMemory",
            return_value=FridayMemory(self.db_path),
        ), patch("core.assistant.AuditLog"):
            self.friday = FridayAssistant()

        self.session = FakeSession()

        self.id1 = self.friday.memory.save(
            "lembrança de teste 1"
        )
        self.id2 = self.friday.memory.save(
            "lembrança de teste 2"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def execute_command(self, command):
        intention = self.friday.interpret(command)
        return self.friday.execute(
            intention,
            command,
            "owner",
            session=self.session,
        )

    def test_batch_delete_requires_confirmation(self):
        response = self.execute_command(
            f"esqueça a ID {self.id1} e ID {self.id2}"
        )

        self.assertTrue(
            self.session.pending_action,
            "A FRIDAY deveria registrar uma exclusão pendente.",
        )
        self.assertIn(
            self.id1,
            {
                item["id"]
                for item in self.friday.memory.get_details()
            },
        )
        self.assertIn(
            self.id2,
            {
                item["id"]
                for item in self.friday.memory.get_details()
            },
        )
        self.assertTrue(response)

    def test_batch_delete_confirmation_and_persistence(self):
        self.execute_command(
            f"esqueça a ID {self.id1} e ID {self.id2}"
        )
        response = self.execute_command("confirmar exclusão")

        self.assertIn("excluídas com sucesso", response.lower())
        self.assertEqual(
            self.friday.memory.get_details(),
            [],
        )

        # Confere a persistência em outra conexão ao mesmo banco.
        reopened = FridayMemory(self.db_path)
        self.assertEqual(reopened.get_details(), [])

    def test_cancel_batch_delete_preserves_memories(self):
        self.execute_command(
            f"esqueça a ID {self.id1} e ID {self.id2}"
        )
        response = self.execute_command("cancelar exclusão")

        self.assertIn("cancelada", response.lower())
        remaining_ids = {
            item["id"]
            for item in self.friday.memory.get_details()
        }
        self.assertEqual(
            remaining_ids,
            {self.id1, self.id2},
        )

    def test_changed_memory_blocks_batch_delete(self):
        self.execute_command(
            f"esqueça a ID {self.id1} e ID {self.id2}"
        )
        self.friday.memory.update(
            self.id1,
            "conteúdo alterado durante a confirmação",
        )

        response = self.execute_command("confirmar exclusão")

        self.assertIn("mudaram", response.lower())
        remaining_ids = {
            item["id"]
            for item in self.friday.memory.get_details()
        }
        self.assertEqual(
            remaining_ids,
            {self.id1, self.id2},
        )

    def test_single_delete_requires_confirmation(self):
        response = self.execute_command(
            f"esqueça a ID {self.id1}"
        )
        self.assertIn(
            "confirma a exclusão",
            response.lower(),
        )
        self.assertIn(
            self.id1,
            {
                item["id"]
                for item in self.friday.memory.get_details()
            },
        )

        response = self.execute_command("confirmar exclusão")
        self.assertIn(
            "excluída com sucesso",
            response.lower(),
        )

        remaining_ids = {
            item["id"]
            for item in self.friday.memory.get_details()
        }
        self.assertEqual(remaining_ids, {self.id2})

    def test_delete_failure_does_not_report_success(self):
        self.execute_command(
            f"esqueça a ID {self.id1}"
        )

        with patch.object(
            self.friday.memory,
            "delete",
            return_value=False,
        ):
            response = self.execute_command(
                "confirmar exclusão"
            )

        self.assertIn(
            "não foi possível excluir",
            response.lower(),
        )
        self.assertIn(
            self.id1,
            {
                item["id"]
                for item in self.friday.memory.get_details()
            },
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
    