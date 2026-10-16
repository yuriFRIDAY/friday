import unittest
from unittest.mock import Mock

from core.assistant import FridayAssistant


class TestFridayAssistant(unittest.TestCase):

    def setUp(self):
        self.assistant = FridayAssistant.__new__(FridayAssistant)

        self.assistant.name = "FRIDAY"
        self.assistant.memory = Mock()
        self.assistant.permissions = Mock()
        self.assistant.context = Mock()
        self.assistant.personality = Mock()
        self.assistant.tools = Mock()
        self.assistant.audit = Mock()

        self.assistant.permissions.has_permission.return_value = True
        self.assistant.memory.get_details.return_value = []
        self.assistant.memory.save.return_value = 1
        self.assistant.memory.update.return_value = True
        self.assistant.memory.delete.return_value = True
        self.assistant.memory.delete_many.return_value = True

    def make_session(self):
        session = Mock()
        session.pending_action = None
        return session

    # -----------------------------------------------
    # Interpretação de comandos
    # -----------------------------------------------

    def test_interpret_greeting(self):
        self.assertEqual(self.assistant.interpret("oi"), "greeting")

    def test_interpret_status(self):
        self.assertEqual(
            self.assistant.interpret("status"),
            "status",
        )

    def test_interpret_identity(self):
        self.assertEqual(
            self.assistant.interpret("quem é você"),
            "identity",
        )

    def test_interpret_exit(self):
        self.assertEqual(
            self.assistant.interpret("sair"),
            "exit",
        )

    def test_interpret_calculation(self):
        self.assertEqual(
            self.assistant.interpret("calcule 2 + 2"),
            "calculate",
        )

    def test_interpret_remember(self):
        self.assertEqual(
            self.assistant.interpret("lembre que gosto de tecnologia"),
            "remember",
        )

    def test_interpret_recall(self):
        self.assertEqual(
            self.assistant.interpret("listar lembranças"),
            "recall",
        )

    def test_interpret_recall_natural_language(self):
        commands = [
            "Me mostra tudo que você lembra.",
            "O que você guardou sobre mim?",
            "Me mostre todas as minhas lembranças.",
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(
                    self.assistant.interpret(command),
                    "recall",
                )

    def test_interpret_remember_future_conversations(self):
        commands = [
            "Guarde isso para as próximas conversas: estou criando a FRIDAY.",
            "Não se esqueça de que este é o Projeto Tony Stark.",
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(
                    self.assistant.interpret(command),
                    "remember",
                )

    def test_interpret_update_memory(self):
        self.assertEqual(
            self.assistant.interpret(
                "corrija a lembrança 3 para conteúdo novo"
            ),
            "update_memory",
        )

    def test_interpret_confirm_update(self):
        self.assertEqual(
            self.assistant.interpret("confirmar atualização"),
            "confirm_memory_update",
        )

    def test_interpret_update_conflict(self):
        self.assertEqual(
            self.assistant.interpret("atualizar lembrança"),
            "confirm_memory_update",
        )

    def test_interpret_keep_both(self):
        self.assertEqual(
            self.assistant.interpret("manter ambas"),
            "confirm_memory_keep_both",
        )

    def test_interpret_cancel_pending(self):
        self.assertEqual(
            self.assistant.interpret("cancelar operação"),
            "cancel_pending",
        )

    def test_interpret_delete_many(self):
        self.assertEqual(
            self.assistant.interpret("exclua 4 e 2"),
            "request_memory_delete_many",
        )

    def test_interpret_unknown(self):
        self.assertEqual(
            self.assistant.interpret("comando que não existe"),
            "unknown",
        )

    # -----------------------------------------------
    # Permissões
    # -----------------------------------------------

    def test_permission_denied(self):
        self.assistant.permissions.has_permission.return_value = False

        response = self.assistant.execute(
            "remember",
            "lembre que gosto de tecnologia",
            "guest",
        )

        self.assertEqual(
            response,
            "Acesso negado para esta ação.",
        )
        self.assistant.memory.save.assert_not_called()

    # -----------------------------------------------
    # Atualização segura de lembranças
    # -----------------------------------------------

    def test_update_memory_requires_confirmation(self):
        session = self.make_session()

        self.assistant.memory.get_details.return_value = [
            {
                "id": 3,
                "content": "Codinome antigo",
                "category": "geral",
            }
        ]

        response = self.assistant.execute(
            "update_memory",
            "corrija a lembrança 3 para Codinome novo",
            "owner",
            session=session,
        )

        self.assertIn("confirme a atualização", response.lower())
        self.assertEqual(
            session.pending_action["type"],
            "memory_update",
        )
        self.assertEqual(
            session.pending_action["memory_id"],
            3,
        )
        self.assertEqual(
            session.pending_action["existing_content"],
            "Codinome antigo",
        )
        self.assertEqual(
            session.pending_action["new_content"],
            "Codinome novo",
        )
        self.assistant.memory.update.assert_not_called()

    def test_update_memory_requires_session(self):
        response = self.assistant.execute(
            "update_memory",
            "corrija a lembrança 3 para conteúdo novo",
            "owner",
            session=None,
        )

        self.assertEqual(
            response,
            "Sessão inválida. Entre novamente.",
        )
        self.assistant.memory.update.assert_not_called()

    def test_update_memory_rejects_missing_id(self):
        session = self.make_session()
        self.assistant.memory.get_details.return_value = []

        response = self.assistant.execute(
            "update_memory",
            "corrija a lembrança 3 para conteúdo novo",
            "owner",
            session=session,
        )

        self.assertIn("não encontrei", response.lower())
        self.assistant.memory.update.assert_not_called()

    def test_update_memory_rejects_unchanged_content(self):
        session = self.make_session()

        self.assistant.memory.get_details.return_value = [
            {
                "id": 3,
                "content": "Mesmo conteúdo",
                "category": "geral",
            }
        ]

        response = self.assistant.execute(
            "update_memory",
            "corrija a lembrança 3 para Mesmo conteúdo",
            "owner",
            session=session,
        )

        self.assertIn("igual ao atual", response.lower())
        self.assistant.memory.update.assert_not_called()

    def test_update_memory_rejects_guest(self):
        session = self.make_session()
        self.assistant.permissions.has_permission.return_value = False

        response = self.assistant.execute(
            "update_memory",
            "corrija a lembrança 3 para conteúdo novo",
            "guest",
            session=session,
        )

        self.assertEqual(
            response,
            "Acesso negado para esta ação.",
        )
        self.assistant.memory.update.assert_not_called()
        self.assertIsNone(session.pending_action)

    def test_confirm_update_changes_memory(self):
        session = self.make_session()
        session.pending_action = {
            "type": "memory_update",
            "memory_id": 3,
            "existing_content": "Conteúdo antigo",
            "existing_category": "geral",
            "new_content": "Conteúdo novo",
        }

        self.assistant.memory.get_details.return_value = [
            {
                "id": 3,
                "content": "Conteúdo antigo",
                "category": "geral",
            }
        ]

        response = self.assistant.execute(
            "confirm_memory_update",
            "confirmar atualização",
            "owner",
            session=session,
        )

        self.assistant.memory.update.assert_called_once_with(
            3,
            "Conteúdo novo",
        )
        self.assertIsNone(session.pending_action)
        self.assertIn("atualizada", response.lower())

    def test_cancel_update_does_not_change_memory(self):
        session = self.make_session()
        session.pending_action = {
            "type": "memory_update",
            "memory_id": 3,
            "existing_content": "Conteúdo antigo",
            "existing_category": "geral",
            "new_content": "Conteúdo novo",
        }

        response = self.assistant.execute(
            "cancel_pending",
            "cancelar operação",
            "owner",
            session=session,
        )

        self.assistant.memory.update.assert_not_called()
        self.assertIsNone(session.pending_action)
        self.assertIn("cancelada", response.lower())

    def test_confirm_update_rejects_changed_original(self):
        session = self.make_session()
        session.pending_action = {
            "type": "memory_update",
            "memory_id": 3,
            "existing_content": "Conteúdo antigo",
            "existing_category": "geral",
            "new_content": "Conteúdo novo",
        }

        self.assistant.memory.get_details.return_value = [
            {
                "id": 3,
                "content": "Conteúdo alterado por outro comando",
                "category": "geral",
            }
        ]

        response = self.assistant.execute(
            "confirm_memory_update",
            "confirmar atualização",
            "owner",
            session=session,
        )

        self.assistant.memory.update.assert_not_called()
        self.assertIsNone(session.pending_action)
        self.assertIn("mudou", response.lower())

    def test_keep_both_rejected_for_direct_update(self):
        session = self.make_session()
        session.pending_action = {
            "type": "memory_update",
            "memory_id": 3,
            "existing_content": "Conteúdo antigo",
            "existing_category": "geral",
            "new_content": "Conteúdo novo",
        }

        response = self.assistant.execute(
            "confirm_memory_keep_both",
            "manter ambas",
            "owner",
            session=session,
        )

        self.assertIn("só se aplica", response.lower())
        self.assistant.memory.save.assert_not_called()
        self.assistant.memory.update.assert_not_called()

    def test_delete_confirmation_cannot_confirm_update(self):
        session = self.make_session()
        session.pending_action = {
            "type": "memory_update",
            "memory_id": 3,
            "existing_content": "Conteúdo antigo",
            "existing_category": "geral",
            "new_content": "Conteúdo novo",
        }

        response = self.assistant.execute(
            "confirm_memory_delete",
            "confirmar exclusão",
            "owner",
            session=session,
        )

        self.assertIn("não há exclusão", response.lower())
        self.assistant.memory.update.assert_not_called()
        self.assistant.memory.delete.assert_not_called()

    # -----------------------------------------------
    # Exclusão em lote
    # -----------------------------------------------

    def test_delete_many_requires_session(self):
        response = self.assistant.execute(
            "request_memory_delete_many",
            "exclua 4 e 2",
            "owner",
            session=None,
        )

        self.assertEqual(
            response,
            "Sessão inválida. Entre novamente.",
        )
        self.assistant.memory.delete_many.assert_not_called()

    def test_delete_many_rejects_duplicate_ids(self):
        session = self.make_session()

        response = self.assistant.execute(
            "request_memory_delete_many",
            "exclua 4 e 4",
            "owner",
            session=session,
        )

        self.assertIn("repetidos", response.lower())
        self.assistant.memory.delete_many.assert_not_called()

    def test_delete_many_rejects_missing_ids(self):
        session = self.make_session()
        self.assistant.memory.get_details.return_value = [
            {
                "id": 4,
                "content": "Lembrança existente",
                "category": "geral",
            }
        ]

        response = self.assistant.execute(
            "request_memory_delete_many",
            "exclua 4 e 9",
            "owner",
            session=session,
        )

        self.assertIn("não encontrei", response.lower())
        self.assistant.memory.delete_many.assert_not_called()

    # -----------------------------------------------
    # Calculadora
    # -----------------------------------------------

    def test_calculate_addition(self):
        calculator = Mock()
        calculator.add.return_value = 4
        self.assistant.tools.get.return_value = calculator

        response = self.assistant.execute(
            "calculate",
            "calcule 2 + 2",
            "owner",
        )

        calculator.add.assert_called_once_with(2.0, 2.0)
        self.assertEqual(response, "O resultado é 4.")

    def test_calculate_division_by_zero(self):
        calculator = Mock()
        calculator.divide.side_effect = ZeroDivisionError
        self.assistant.tools.get.return_value = calculator

        response = self.assistant.execute(
            "calculate",
            "calcule 10 / 0",
            "owner",
        )

        self.assertEqual(
            response,
            "Não consegui interpretar esse cálculo.",
        )

    def test_unknown_intention(self):
        response = self.assistant.execute(
            "intencao_inexistente",
            "qualquer coisa",
            "owner",
        )

        self.assertEqual(
            response,
            "Não reconheço essa intenção.",
        )


if __name__ == "__main__":
    unittest.main()
