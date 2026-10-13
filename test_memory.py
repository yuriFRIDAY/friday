```python
import os
import tempfile
import unittest

from core.memory import FridayMemory
from core.permissions import PermissionManager


class TestFridayMemory(unittest.TestCase):

    def setUp(self):
        # Cada teste recebe seu próprio banco temporário.
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = os.path.join(
            self.temp_dir.name,
            "test_friday.db"
        )
        self.memory = FridayMemory(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_get_all(self):
        self.memory.save("Projeto FRIDAY", "projeto")

        memories = self.memory.get_all()

        self.assertEqual(memories, ["Projeto FRIDAY"])

    def test_get_by_category(self):
        self.memory.save("Estudar Python", "estudos")
        self.memory.save("Projeto FRIDAY", "projeto")

        memories = self.memory.get_by_category("estudos")

        self.assertEqual(memories, ["Estudar Python"])

    def test_get_details_contains_id_content_and_category(self):
        memory_id = self.memory.save("Aprender programação", "estudos")

        memories = self.memory.get_details()

        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["id"], memory_id)
        self.assertEqual(
            memories[0]["content"],
            "Aprender programação"
        )
        self.assertEqual(memories[0]["category"], "estudos")

    def test_update_memory(self):
        memory_id = self.memory.save("Texto antigo", "geral")

        updated = self.memory.update(
            memory_id,
            "Texto corrigido"
        )

        self.assertTrue(updated)
        self.assertEqual(
            self.memory.get_all(),
            ["Texto corrigido"]
        )

    def test_update_category(self):
        memory_id = self.memory.save("Aprender Python", "geral")

        updated = self.memory.update(
            memory_id,
            "Aprender Python",
            "estudos"
        )

        self.assertTrue(updated)
        self.assertEqual(
            self.memory.get_by_category("estudos"),
            ["Aprender Python"]
        )

    def test_delete_memory(self):
        memory_id = self.memory.save("Memória temporária")

        deleted = self.memory.delete(memory_id)

        self.assertTrue(deleted)
        self.assertEqual(self.memory.get_all(), [])

    def test_update_missing_id(self):
        self.assertFalse(
            self.memory.update(999999, "Texto")
        )

    def test_delete_missing_id(self):
        self.assertFalse(
            self.memory.delete(999999)
        )


class TestFridayPermissions(unittest.TestCase):

    def setUp(self):
        self.permissions = PermissionManager()

    def test_guest_can_only_converse(self):
        self.assertTrue(
            self.permissions.has_permission("guest", "converse")
        )
        self.assertFalse(
            self.permissions.has_permission("guest", "read_memory")
        )
        self.assertFalse(
            self.permissions.has_permission("guest", "write_memory")
        )
        self.assertFalse(
            self.permissions.has_permission("guest", "use_tools")
        )

    def test_user_can_read_but_not_write_memory(self):
        self.assertTrue(
            self.permissions.has_permission("user", "read_memory")
        )
        self.assertFalse(
            self.permissions.has_permission("user", "write_memory")
        )

    def test_owner_has_expected_permissions(self):
        for permission in (
            "converse",
            "read_memory",
            "write_memory",
            "use_tools",
            "configure",
        ):
            with self.subTest(permission=permission):
                self.assertTrue(
                    self.permissions.has_permission(
                        "owner",
                        permission
                    )
                )

    def test_system_has_only_critical_operations(self):
        self.assertTrue(
            self.permissions.has_permission(
                "system",
                "critical_operations"
            )
        )
        self.assertFalse(
            self.permissions.has_permission("system", "converse")
        )

    def test_unknown_role_is_denied(self):
        self.assertFalse(
            self.permissions.has_permission(
                "unknown_role",
                "converse"
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
```