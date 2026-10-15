
import os
import tempfile
import unittest

from core.memory import FridayMemory


class TestMemoryDeleteSafe(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(
            self.temp_dir.name,
            "test_friday.db",
        )
        self.memory = FridayMemory(self.db_path)

        self.id1 = self.memory.save(
            "lembrança de teste 1"
        )
        self.id2 = self.memory.save(
            "lembrança de teste 2"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_delete_many_removes_all_requested_ids(self):
        result = self.memory.delete_many(
            [self.id1, self.id2]
        )

        self.assertTrue(result)
        self.assertEqual(self.memory.get_details(), [])

    def test_missing_id_does_not_delete_existing_memory(self):
        result = self.memory.delete_many(
            [self.id1, 999999]
        )

        self.assertFalse(result)
        ids = {
            item["id"]
            for item in self.memory.get_details()
        }
        self.assertIn(self.id1, ids)

    def test_invalid_ids_are_rejected(self):
        self.assertFalse(self.memory.delete_many([0]))
        self.assertFalse(self.memory.delete_many([-1]))
        self.assertFalse(self.memory.delete_many([True]))

    def test_single_delete(self):
        self.assertTrue(self.memory.delete(self.id1))
        self.assertFalse(self.memory.delete(self.id1))
        self.assertEqual(
            [item["id"] for item in self.memory.get_details()],
            [self.id2],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
