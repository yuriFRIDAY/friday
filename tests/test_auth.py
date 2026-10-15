import unittest

from core.auth import FridayAuth


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FridayAuthTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.auth = FridayAuth(
            password="senha-de-teste",
            max_attempts=3,
            lockout_seconds=30,
            clock=self.clock,
        )

    def test_correct_password_authenticates(self):
        self.assertTrue(
            self.auth.authenticate("senha-de-teste")
        )

    def test_wrong_password_is_rejected(self):
        self.assertFalse(self.auth.authenticate("incorreta"))

    def test_lockout_after_max_attempts(self):
        for _ in range(3):
            self.assertFalse(self.auth.authenticate("incorreta"))

        self.assertEqual(self.auth.remaining_lockout(), 30)
        self.assertFalse(self.auth.authenticate("incorreta"))

    def test_lockout_expires(self):
        for _ in range(3):
            self.auth.authenticate("incorreta")

        self.clock.advance(31)

        self.assertEqual(self.auth.remaining_lockout(), 0)
        self.assertFalse(self.auth.authenticate("incorreta"))
        self.assertTrue(
            self.auth.authenticate("senha-de-teste")
        )

    def test_success_clears_failed_attempts(self):
        self.auth.authenticate("incorreta")
        self.assertTrue(
            self.auth.authenticate("senha-de-teste")
        )

        self.auth.authenticate("incorreta")
        self.auth.authenticate("incorreta")

        self.assertEqual(self.auth.remaining_lockout(), 0)

    def test_attempts_are_independent_by_client(self):
        for _ in range(3):
            self.auth.authenticate("incorreta", client_key="cliente-a")

        self.assertGreater(
            self.auth.remaining_lockout("cliente-a"), 0
        )
        self.assertEqual(
            self.auth.remaining_lockout("cliente-b"), 0
        )


if __name__ == "__main__":
    unittest.main()
