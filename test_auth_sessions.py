import http.client
import json
import threading
import unittest
from http.cookies import SimpleCookie
from http.server import HTTPServer
from unittest.mock import patch

import app
from core.auth import FridayAuth
from core.sessions import SessionStore


class TestFridayAuth(unittest.TestCase):
    def test_password_must_be_a_nonempty_string(self):
        auth = FridayAuth(password="secret")

        for password in (None, 12, [], {}, ""):
            with self.subTest(password_type=type(password).__name__):
                self.assertFalse(
                    auth.authenticate(password, client_key="client-a")
                )

        self.assertFalse(FridayAuth(password="").authenticate("anything"))

    def test_lockout_is_independent_per_client(self):
        now = [100.0]
        auth = FridayAuth(
            password="secret",
            max_attempts=3,
            lockout_seconds=5,
            clock=lambda: now[0],
        )

        for _ in range(3):
            self.assertFalse(
                auth.authenticate("wrong", client_key="client-a")
            )

        self.assertFalse(auth.authenticate("wrong", client_key="client-a"))
        self.assertTrue(
            auth.authenticate("secret", client_key="client-b")
        )

        now[0] += 5
        self.assertTrue(
            auth.authenticate("secret", client_key="client-a")
        )

    def test_correct_password_is_not_blocked_by_failed_attempts(self):
        auth = FridayAuth(
            password="secret",
            max_attempts=2,
            lockout_seconds=30,
            clock=lambda: 100.0,
        )
        for _ in range(2):
            self.assertFalse(
                auth.authenticate("wrong", client_key="shared-client")
            )

        self.assertTrue(
            auth.authenticate("secret", client_key="shared-client")
        )


class TestSessionStore(unittest.TestCase):
    def test_expired_session_is_invalidated_and_history_cleared(self):
        now = [100.0]
        store = SessionStore(
            ttl_seconds=30,
            clock=lambda: now[0],
            token_factory=lambda: "session-token",
        )
        token = store.create()
        session = store.get(token)
        session.history.extend(["mensagem", "resposta"])

        now[0] = 130.0

        self.assertIsNone(store.get(token))
        self.assertEqual(session.history, [])
        self.assertFalse(store.invalidate(token))

    def test_invalid_tokens_are_rejected(self):
        store = SessionStore()

        for token in (None, "", "not-a-session"):
            with self.subTest(token=token):
                self.assertIsNone(store.get(token))
                self.assertIsNone(store.get_history(token))
                self.assertFalse(store.invalidate(token))

    def test_sessions_keep_independent_histories(self):
        tokens = iter(("session-a", "session-b"))
        store = SessionStore(token_factory=lambda: next(tokens))
        first_token = store.create()
        second_token = store.create()
        first_history = store.get_history(first_token)
        second_history = store.get_history(second_token)
        first_history.append("histórico A")
        second_history.append("histórico B")

        self.assertTrue(store.invalidate(first_token))
        self.assertEqual(first_history, [])
        self.assertEqual(store.get_history(second_token), ["histórico B"])


class QuietFridayHandler(app.FridayHandler):
    def log_message(self, format, *args):
        pass


class TestFridaySessionRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), QuietFridayHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever,
            daemon=True,
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        self.now = [1000.0]
        self.store = SessionStore(clock=lambda: self.now[0])
        self.auth = FridayAuth(password="test-password")
        self.patches = [
            patch.object(app, "SESSION_STORE", self.store),
            patch.object(app, "auth", self.auth),
            patch.object(app, "COOKIE_SECURE_MODE", "auto"),
            patch.dict("os.environ", {"FRIDAY_ALLOWED_ORIGINS": ""}),
        ]
        for patcher in self.patches:
            patcher.start()

        self.origin = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()

    def post(self, path, data, *, origin="same", cookie=None, scheme=None):
        headers = {"Content-Type": "application/json"}
        if origin == "same":
            headers["Origin"] = self.origin
        elif origin is not None:
            headers["Origin"] = origin
        if cookie is not None:
            headers["Cookie"] = cookie
        if scheme is not None:
            headers["X-Forwarded-Proto"] = scheme

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.port,
            timeout=3,
        )
        connection.request(
            "POST",
            path,
            body=json.dumps(data).encode("utf-8"),
            headers=headers,
        )
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        result = {
            "status": response.status,
            "body": body,
            "headers": dict(response.getheaders()),
        }
        connection.close()
        return result

    def get(self, path, *, cookie=None):
        headers = {}
        if cookie is not None:
            headers["Cookie"] = cookie

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.port,
            timeout=3,
        )
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        result = {
            "status": response.status,
            "body": body,
            "headers": dict(response.getheaders()),
        }
        connection.close()
        return result

    def login(self, *, origin="same", scheme=None):
        return self.post(
            "/login",
            {"password": "test-password"},
            origin=origin,
            scheme=scheme,
        )

    @staticmethod
    def token_from(response):
        cookie = SimpleCookie()
        cookie.load(response["headers"]["Set-Cookie"])
        return cookie["friday_session"].value

    def test_login_creates_session_and_interface_reports_authenticated(self):
        response = self.login()

        self.assertEqual(response["status"], 200)
        token = self.token_from(response)
        self.assertIsNotNone(self.store.get(token))
        self.assertIn("HttpOnly", response["headers"]["Set-Cookie"])

        session_status = self.get(
            "/session",
            cookie=f"friday_session={token}",
        )

        self.assertEqual(session_status["status"], 200)
        self.assertTrue(session_status["body"]["authenticated"])

    def test_login_rejects_missing_or_foreign_origin(self):
        missing = self.login(origin=None)
        foreign = self.login(origin="https://other.example")

        self.assertEqual(missing["status"], 403)
        self.assertEqual(foreign["status"], 403)

    def test_secure_cookie_tracks_forwarded_https(self):
        secure_origin = f"https://127.0.0.1:{self.port}"
        response = self.login(origin=secure_origin, scheme="https")

        self.assertEqual(response["status"], 200)
        self.assertIn("; Secure", response["headers"]["Set-Cookie"])
        self.assertIn(
            "Max-Age=1800",
            response["headers"]["Set-Cookie"],
        )

    def test_chat_command_uses_authenticated_session_without_external_api(self):
        login = self.login()
        token = self.token_from(login)

        with patch.object(app, "friday") as assistant:
            assistant.interpret.return_value = "greeting"
            assistant.execute.return_value = "Olá, resposta local."
            response = self.post(
                "/chat",
                {"command": "Olá, FRIDAY"},
                cookie=f"friday_session={token}",
            )

        self.assertEqual(response["status"], 200)
        self.assertEqual(
            response["body"],
            {"response": "Olá, resposta local.", "closed": False},
        )
        assistant.interpret.assert_called_once_with("Olá, FRIDAY")
        assistant.execute.assert_called_once()
        self.assertIs(
            assistant.execute.call_args.kwargs["history"],
            self.store.get_history(token),
        )

    def test_expired_session_returns_interface_to_signed_out_state(self):
        login = self.login()
        token = self.token_from(login)
        self.assertTrue(
            self.get(
                "/session",
                cookie=f"friday_session={token}",
            )["body"]["authenticated"]
        )

        self.now[0] += app.SESSION_TTL_SECONDS
        session_status = self.get(
            "/session",
            cookie=f"friday_session={token}",
        )

        self.assertEqual(session_status["status"], 200)
        self.assertFalse(session_status["body"]["authenticated"])
        self.assertIn("Max-Age=0", session_status["headers"]["Set-Cookie"])
        self.assertIsNone(self.store.get(token))

    def test_logout_invalidates_session_and_clears_its_history(self):
        login = self.login()
        self.assertEqual(login["status"], 200)
        token = self.token_from(login)
        session = self.store.get(token)
        history = session.history
        history.extend(["mensagem privada", "resposta"])

        response = self.post(
            "/logout",
            {},
            cookie=f"friday_session={token}",
        )

        self.assertEqual(response["status"], 200)
        self.assertIsNone(self.store.get(token))
        self.assertEqual(history, [])
        self.assertIn("Max-Age=0", response["headers"]["Set-Cookie"])
        session_status = self.get(
            "/session",
            cookie=f"friday_session={token}",
        )
        self.assertFalse(session_status["body"]["authenticated"])

    def test_invalid_token_is_rejected_before_assistant_call(self):
        with patch.object(app, "friday") as assistant:
            response = self.post(
                "/chat",
                {"command": "não deve chegar ao assistente"},
                cookie="friday_session=invalid-token",
            )

        self.assertEqual(response["status"], 401)
        self.assertIn("Max-Age=0", response["headers"]["Set-Cookie"])
        assistant.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
