import unittest
from unittest.mock import MagicMock, patch

from core.assistant import FridayAssistant


class FridayAssistantTests(unittest.TestCase):

    def setUp(self):
        self.patches = [
            patch("core.assistant.FridayMemory"),
            patch("core.assistant.PermissionManager"),
            patch("core.assistant.FridayContext"),
            patch("core.assistant.FridayPersonality"),
            patch("core.assistant.ToolManager"),
            patch("core.assistant.AuditLog"),
        ]

        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(self._stop_patches)

        self.assistant = FridayAssistant()
        self.assistant.permissions.has_permission.return_value = True

        calculator = MagicMock()
        calculator.add.return_value = 7.0
        calculator.subtract.return_value = 3.0
        calculator.multiply.return_value = 12.0
        calculator.divide.return_value = 2.0

        self.assistant.tools.get.return_value = calculator

    def _stop_patches(self):
        for patcher in reversed(self.patches):
            patcher.stop()

    def test_interpret_greeting(self):
        self.assertEqual(self.assistant.interpret("Olá"), "greeting")

    def test_interpret_status(self):
        self.assertEqual(self.assistant.interpret("status"), "status")

    def test_interpret_calculation(self):
        self.assertEqual(
            self.assistant.interpret("calcule 3 + 4"),
            "calculate",
        )

    def test_interpret_unknown_question(self):
        self.assertEqual(
            self.assistant.interpret("Explique inteligência artificial"),
            "unknown",
        )

    def test_interpret_memory_update(self):
        self.assertEqual(
            self.assistant.interpret(
                "corrija a lembrança 3 para meu codinome é Aurora-27"
            ),
            "update_memory",
        )

    def test_greeting_response(self):
        response = self.assistant.execute("greeting", "oi", "owner")
        self.assertIn("Olá", response)

    def test_permission_denied(self):
        self.assistant.permissions.has_permission.return_value = False

        response = self.assistant.execute(
            "calculate", "calcule 3 + 4", "guest"
        )

        self.assertEqual(response, "Acesso negado para esta ação.")

    def test_calculator_addition(self):
        response = self.assistant.execute(
            "calculate", "calcule 3 + 4", "owner"
        )

        self.assertEqual(response, "O resultado é 7.0.")

    def test_calculator_division_by_zero(self):
        calculator = self.assistant.tools.get.return_value
        calculator.divide.side_effect = ZeroDivisionError

        response = self.assistant.execute(
            "calculate", "calcule 4 / 0", "owner"
        )

        self.assertEqual(
            response,
            "Não consegui interpretar esse cálculo.",
        )

    def test_invalid_intention(self):
        response = self.assistant.execute(
            "intencao_inexistente", "teste", "owner"
        )

        self.assertEqual(response, "Não reconheço essa intenção.")

    def test_guest_cannot_save_memory(self):
        self.assistant.permissions.has_permission.return_value = False

        response = self.assistant.execute(
            "remember", "lembre que gosto de Python", "guest"
        )

        self.assertEqual(response, "Acesso negado para esta ação.")

    def test_user_cannot_save_memory(self):
        self.assistant.permissions.has_permission.return_value = False

        response = self.assistant.execute(
            "remember", "lembre que gosto de Python", "user"
        )

        self.assertEqual(response, "Acesso negado para esta ação.")

    def test_owner_can_save_memory(self):
        self.assistant.memory.save.return_value = 1

        response = self.assistant.execute(
            "remember", "lembre que gosto de Python", "owner"
        )

        self.assistant.memory.save.assert_called_once_with(
            "gosto de Python", "preferencia"
        )

    def test_unknown_role_cannot_use_ai(self):
        with patch("core.ai_provider.FridayAIProvider") as provider_class:
            self.assistant.permissions.has_permission.return_value = False

            response = self.assistant.execute(
                "unknown",
                "Explique inteligência artificial",
                "unknown_role",
            )

        self.assertEqual(response, "Acesso negado para esta ação.")
        provider_class.assert_not_called()

    def test_update_memory_changes_only_selected_id(self):
        self.assistant.memory.update.return_value = True

        response = self.assistant.execute(
            "update_memory",
            "corrija a lembrança 3 para meu codinome é Aurora-27",
            "owner",
        )

        self.assertIn("Lembrança 3 corrigida", response)
        self.assistant.memory.update.assert_called_once_with(
            3, "meu codinome é Aurora-27"
        )

    def test_update_memory_missing_id(self):
        self.assistant.memory.update.return_value = False

        response = self.assistant.execute(
            "update_memory",
            "corrija a lembrança 999999 para informação nova",
            "owner",
        )

        self.assertIn("Não encontrei uma lembrança", response)
        self.assistant.memory.update.assert_called_once_with(
            999999, "informação nova"
        )

    def test_update_memory_invalid_format(self):
        response = self.assistant.execute(
            "update_memory",
            "corrija a lembrança sem informar um ID",
            "owner",
        )

        self.assertIn("Formato inválido", response)
        self.assistant.memory.update.assert_not_called()

    def test_guest_cannot_update_memory(self):
        self.assistant.permissions.has_permission.return_value = False

        response = self.assistant.execute(
            "update_memory",
            "corrija a lembrança 3 para informação nova",
            "guest",
        )

        self.assertEqual(response, "Acesso negado para esta ação.")
        self.assistant.memory.update.assert_not_called()

    def test_interpret_delete_multiple_memories(self):
        commands = [
            "exclua 1, 2",
            "apague as lembranças 1 e 2",
            "remova ids 1, 2 e 3",
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(
                    self.assistant.interpret(command),
                    "request_memory_delete_many",
                )

    def test_request_delete_many_requires_session(self):
        response = self.assistant.execute(
            "request_memory_delete_many",
            "exclua 1, 2",
            "owner",
            session=None,
        )

        self.assertEqual(
            response,
            "Sessão inválida. Entre novamente.",
        )

    def test_request_delete_many_rejects_duplicate_ids(self):
        session = MagicMock()
        session.pending_action = None
        self.assistant.memory.get_details.return_value = []

        response = self.assistant.execute(
            "request_memory_delete_many",
            "exclua 1, 1",
            "owner",
            session=session,
        )

        self.assertIn("IDs repetidos", response)
        self.assertIsNone(session.pending_action)
        self.assistant.memory.delete_many.assert_not_called()

    def test_request_delete_many_rejects_missing_ids(self):
        session = MagicMock()
        self.assistant.memory.get_details.return_value = [
            {
                "id": 1,
                "content": "Gosto de Python",
                "category": "preferencia",
            }
        ]

        response = self.assistant.execute(
            "request_memory_delete_many",
            "exclua 1, 999",
            "owner",
            session=session,
        )

        self.assertIn("999", response)
        self.assertIn("Nenhuma lembrança foi removida", response)
        self.assistant.memory.delete_many.assert_not_called()

    def test_request_delete_many_creates_confirmation(self):
        session = MagicMock()
        self.assistant.memory.get_details.return_value = [
            {
                "id": 1,
                "content": "Gosto de Python",
                "category": "preferencia",
            },
            {
                "id": 2,
                "content": "Estudo robótica",
                "category": "estudos",
            },
        ]

        response = self.assistant.execute(
            "request_memory_delete_many",
            "exclua 1, 2",
            "owner",
            session=session,
        )

        self.assertIn("confirme a exclusão", response.lower())
        self.assertEqual(
            session.pending_action["type"],
            "memory_delete_many",
        )
        self.assertEqual(
            [
                item["id"]
                for item in session.pending_action["memories"]
            ],
            [1, 2],
        )
        self.assistant.memory.delete_many.assert_not_called()

    def test_cancel_delete_many_preserves_memories(self):
        session = MagicMock()
        session.pending_action = {
            "type": "memory_delete_many",
            "memories": [
                {
                    "id": 1,
                    "content": "Gosto de Python",
                    "category": "preferencia",
                },
                {
                    "id": 2,
                    "content": "Estudo robótica",
                    "category": "estudos",
                },
            ],
        }

        response = self.assistant.execute(
            "cancel_memory_delete",
            "cancelar exclusão",
            "owner",
            session=session,
        )

        self.assertIn("cancelada", response.lower())
        self.assertIsNone(session.pending_action)
        self.assistant.memory.delete_many.assert_not_called()

    def test_confirm_delete_many_deletes_selected_ids(self):
        session = MagicMock()
        session.pending_action = {
            "type": "memory_delete_many",
            "memories": [
                {
                    "id": 1,
                    "content": "Gosto de Python",
                    "category": "preferencia",
                },
                {
                    "id": 2,
                    "content": "Estudo robótica",
                    "category": "estudos",
                },
            ],
        }
        self.assistant.memory.get_details.return_value = [
            {
                "id": 1,
                "content": "Gosto de Python",
                "category": "preferencia",
            },
            {
                "id": 2,
                "content": "Estudo robótica",
                "category": "estudos",
            },
            {
                "id": 3,
                "content": "Projeto FRIDAY",
                "category": "projeto",
            },
        ]
        self.assistant.memory.delete_many.return_value = True

        response = self.assistant.execute(
            "confirm_memory_delete",
            "confirmar exclusão",
            "owner",
            session=session,
        )

        self.assertIn("2 lembranças excluídas", response)
        self.assistant.memory.delete_many.assert_called_once_with([1, 2])
        self.assertIsNone(session.pending_action)

    def test_confirm_delete_many_aborts_if_memory_changed(self):
        session = MagicMock()
        session.pending_action = {
            "type": "memory_delete_many",
            "memories": [
                {
                    "id": 1,
                    "content": "Gosto de Python",
                    "category": "preferencia",
                }
            ],
        }
        self.assistant.memory.get_details.return_value = [
            {
                "id": 1,
                "content": "Conteúdo alterado",
                "category": "preferencia",
            }
        ]

        response = self.assistant.execute(
            "confirm_memory_delete",
            "confirmar exclusão",
            "owner",
            session=session,
        )

        self.assertIn("Nenhuma exclusão foi realizada", response)
        self.assistant.memory.delete_many.assert_not_called()
        self.assertIsNone(session.pending_action)

    def test_guest_cannot_request_delete_many(self):
        self.assistant.permissions.has_permission.return_value = False
        session = MagicMock()

        response = self.assistant.execute(
            "request_memory_delete_many",
            "exclua 1, 2",
            "guest",
            session=session,
        )

        self.assertEqual(response, "Acesso negado para esta ação.")
        self.assistant.memory.delete_many.assert_not_called()
        
if __name__ == "__main__":
    unittest.main(verbosity=2)
