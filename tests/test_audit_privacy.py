import tempfile
import unittest
from pathlib import Path

from core.audit import AuditLog


class TestAuditPrivacy(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temp_dir.name) / "audit_test.log"
        self.audit = AuditLog(str(self.log_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def read_log(self):
        return self.log_path.read_text(encoding="utf-8")

    def test_command_content_is_not_logged(self):
        secret_text = "minha informação pessoal confidencial"

        self.audit.record("COMMAND", secret_text)

        log = self.read_log()
        self.assertIn("COMMAND", log)
        self.assertNotIn(secret_text, log)

    def test_memory_content_is_not_logged(self):
        secret_text = "minha lembrança privada"

        self.audit.record("MEMORY_SAVED", f"geral: {secret_text}")

        log = self.read_log()
        self.assertIn("MEMORY_SAVED", log)
        self.assertNotIn(secret_text, log)

    def test_permission_details_are_preserved(self):
        self.audit.record(
            "PERMISSION_DENIED",
            "guest -> write_memory",
        )

        log = self.read_log()
        self.assertIn("PERMISSION_DENIED", log)
        self.assertIn("guest -> write_memory", log)

    def test_calculator_result_is_not_logged(self):
        self.audit.record("TOOL_EXECUTION", "calculator -> 12345")

        log = self.read_log()
        self.assertIn("TOOL_EXECUTION: calculator", log)
        self.assertNotIn("12345", log)

    def test_unknown_event_is_rejected(self):
        self.audit.record("EVENTO_INVENTADO", "conteúdo privado")

        log = self.read_log()
        self.assertIn("INVALID_EVENT", log)
        self.assertNotIn("conteúdo privado", log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
