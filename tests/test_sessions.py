import unittest

from core.sessions import SessionStore


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.counter = 0

        def token_factory():
            self.counter += 1
            return f"test-token-{self.counter}"

        self.store = SessionStore(
            ttl_seconds=30,
            clock=self.clock,
            token_factory=token_factory,
        )

    def test_create_and_get_session(self):
        token = self.store.create()

        self.assertIsNotNone(self.store.get(token))
        self.assertEqual(self.store.get(token).token, token)

    def test_expired_session_is_invalidated(self):
        token = self.store.create()
        self.clock.advance(31)

        self.assertIsNone(self.store.get(token))
        self.assertIsNone(self.store.get_history(token))

    def test_histories_are_independent(self):
        first = self.store.create()
        second = self.store.create()

        self.store.get_history(first).append(
            {"role": "user", "content": "Olá"}
        )

        self.assertEqual(len(self.store.get_history(first)), 1)
        self.assertEqual(self.store.get_history(second), [])

    def test_invalidate_clears_history(self):
        token = self.store.create()
        history = self.store.get_history(token)
        history.append({"role": "user", "content": "Teste"})

        self.assertTrue(self.store.invalidate(token))
        self.assertEqual(history, [])
        self.assertIsNone(self.store.get(token))
        self.assertFalse(self.store.invalidate(token))

    def test_cleanup_removes_expired_sessions(self):
        first = self.store.create()
        second = self.store.create()

        self.clock.advance(31)

        removed = self.store.cleanup_expired()

        self.assertEqual(removed, 2)
        self.assertIsNone(self.store.get(first))
        self.assertIsNone(self.store.get(second))

    def test_create_cleans_expired_sessions(self):
        old_token = self.store.create()
        self.clock.advance(31)

        new_token = self.store.create()

        self.assertIsNone(self.store.get(old_token))
        self.assertIsNotNone(self.store.get(new_token))

    def test_duplicate_token_is_rejected(self):
        store = SessionStore(
            ttl_seconds=30,
            clock=self.clock,
            token_factory=lambda: "same-token",
        )

        store.create()

        with self.assertRaises(RuntimeError):
            store.create()


if __name__ == "__main__":
    unittest.main()
