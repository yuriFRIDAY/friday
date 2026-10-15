import json
import os
import threading
import unittest
from http.server import HTTPServer
from http.client import HTTPConnection
from unittest.mock import patch

import app


class FridayWebIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Permite testar a origem local sem alterar a configuração
        # permanente do ambiente Replit.
        cls.allowed_origins = patch.dict(
            os.environ,
            {"FRIDAY_ALLOWED_ORIGINS": ""},
        )
        cls.allowed_origins.start()

        cls.server = HTTPServer(
            ("127.0.0.1", 0),
            app.FridayHandler,
        )
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
        cls.thread.join(timeout=2)
        cls.allowed_origins.stop()

    def setUp(self):
        self.connection = HTTPConnection(
            "127.0.0.1",
            self.port,
            timeout=3,
        )
        self.origin = f"http://127.0.0.1:{self.port}"
        self.token = None


    def test_request_scheme_accepts_https(self):
        with patch.object(
            app.FridayHandler,
            "headers",
            {"X-Forwarded-Proto": "https"},
            create=True,
        ):
            pass

    def tearDown(self):
        self.connection.close()

        if self.token:
            app.SESSION_STORE.invalidate(self.token)

    def request(self, method, path, data=None, headers=None):
        headers = dict(headers or {})
        body = None

        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))

        self.connection.request(
            method,
            path,
            body=body,
            headers=headers,
        )

        response = self.connection.getresponse()
        response_body = response.read()

        try:
            parsed = json.loads(response_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            parsed = response_body.decode(
                "utf-8",
                errors="replace",
            )

        return (
            response.status,
            dict(response.getheaders()),
            parsed,
        )

    def post(self, path, data, cookie=None, origin=None):
        headers = {
            "Origin": origin or self.origin,
        }

        if cookie:
            headers["Cookie"] = cookie

        return self.request(
            "POST",
            path,
            data,
            headers,
        )

    def login(self):
        with patch.object(
            app.auth,
            "authenticate",
            return_value=True,
        ):
            status, headers, body = self.post(
                "/login",
                {"password": "senha-de-teste"},
            )

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

        cookie = headers.get("Set-Cookie", "")
        self.assertIn("friday_session=", cookie)

        self.token = cookie.split(";", 1)[0].split("=", 1)[1]

        return cookie.split(";", 1)[0]


    def test_cookie_secure_modes(self):
        cases = [
            ("always", None, True),
            ("never", "https", False),
            ("auto", "https", True),
            ("auto", "http", False),
        ]

        for mode, forwarded_proto, expected_secure in cases:
            with self.subTest(
                mode=mode,
                forwarded_proto=forwarded_proto,
            ):
                origin_scheme = forwarded_proto or "http"
                headers = {
                    "Origin": f"{origin_scheme}://127.0.0.1:{self.port}",
                }

                if forwarded_proto is not None:
                    headers["X-Forwarded-Proto"] = forwarded_proto

                with patch.object(
                    app,
                    "COOKIE_SECURE_MODE",
                    mode,
                ), patch.object(
                    app.auth,
                    "authenticate",
                    return_value=True,
                ):
                    status, response_headers, body = self.request(
                        "POST",
                        "/login",
                        {"password": "senha-de-teste"},
                        headers,
                    )

                self.assertEqual(status, 200)
                self.assertTrue(body["ok"])

                cookie = response_headers.get("Set-Cookie", "")
                has_secure = "; Secure" in cookie

                self.assertEqual(has_secure, expected_secure)

    def test_home_returns_html(self):
        status, headers, body = self.request("GET", "/")

        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("FRIDAY", body)

    def test_session_without_login_is_unauthenticated(self):
        status, _, body = self.request("GET", "/session")

        self.assertEqual(status, 200)
        self.assertFalse(body["authenticated"])

    def test_login_rejects_wrong_password(self):
        with patch.object(
            app.auth,
            "authenticate",
            return_value=False,
        ), patch.object(
            app.auth,
            "remaining_lockout",
            return_value=0,
        ):
            status, _, body = self.post(
                "/login",
                {"password": "senha-incorreta"},
            )


    def test_login_returns_429_when_client_is_locked(self):
        from core.auth import FridayAuth

        current_time = [100.0]
        test_auth = FridayAuth(
            password="senha-correta",
            max_attempts=3,
            lockout_seconds=30,
            clock=lambda: current_time[0],
        )

        original_handle = app.FridayHandler.handle

        def handle_with_test_client(handler):
            handler.client_address = ("127.0.0.2", 12345)
            original_handle(handler)

        with patch.object(app, "auth", test_auth), patch.object(
            app.FridayHandler,
            "handle",
            handle_with_test_client,
        ):
            for _ in range(2):
                status, _, body = self.post(
                    "/login",
                    {"password": "senha-incorreta"},
                )

            status, _, body = self.post(
                "/login",
                {"password": "senha-incorreta"},
            )
            self.assertEqual(status, 429)
            self.assertTrue(body["locked"])
            self.assertEqual(body["remaining"], 30)

            current_time[0] += 5

            status, _, body = self.post(
                "/login",
                {"password": "senha-incorreta"},
            )
            self.assertEqual(status, 429)
            self.assertTrue(body["locked"])
            self.assertEqual(body["remaining"], 25)


    def test_login_creates_session_cookie(self):
        with patch.object(app, "COOKIE_SECURE_MODE", "always"), patch.object(
            app.auth, "authenticate", return_value=True
        ):
            status, headers, body = self.post(
                "/login",
                {"password": "senha-de-teste"},
            )


        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        logout_cookie = headers.get("Set-Cookie", "")
        self.assertIn("friday_session=", logout_cookie)

        cookie = headers.get("Set-Cookie", "")
        self.assertIn("friday_session=", cookie)
        self.assertIn("Path=/", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Max-Age=1800", cookie)
        self.assertIn("Secure", cookie)

        self.token = cookie.split(";", 1)[0].split("=", 1)[1]

        status, _, body = self.request(
            "GET",
            "/session",
            headers={"Cookie": f"friday_session={self.token}"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(body["authenticated"])


    def test_post_rejects_untrusted_origin(self):
        status, _, body = self.post(
            "/login",
            {"password": "qualquer"},
            origin="https://origem-invalida.example",
        )

        self.assertEqual(status, 403)
        self.assertEqual(
            body["error"],
            "Origem não autorizada.",
        )

    def test_chat_requires_session(self):
        status, _, body = self.post(
            "/chat",
            {"command": "oi"},
        )

        self.assertEqual(status, 401)
        self.assertIn("Sessão inválida", body["error"])

    def test_chat_returns_assistant_response(self):
        cookie = self.login()

        with patch.object(
            app.friday,
            "interpret",
            return_value="greeting",
        ), patch.object(
            app.friday,
            "execute",
            return_value="Olá, comandante!",
        ):
            status, _, body = self.post(
                "/chat",
                {"command": "oi"},
                cookie=cookie,
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["response"], "Olá, comandante!")
        self.assertFalse(body["closed"])

    def test_chat_rejects_empty_command(self):
        cookie = self.login()

        status, _, body = self.post(
            "/chat",
            {"command": "   "},
            cookie=cookie,
        )

        self.assertEqual(status, 400)
        self.assertEqual(
            body["error"],
            "Comando vazio ou muito longo.",
        )

    def test_logout_invalidates_session(self):
        cookie = self.login()

        status, headers, body = self.post(
            "/logout",
            {},
            cookie=cookie,
        )

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

        status, _, body = self.request(
            "GET",
            "/session",
            headers={"Cookie": cookie},
        )

        self.assertEqual(status, 200)
        self.assertFalse(body["authenticated"])

    def test_unknown_route_returns_404(self):
        status, _, body = self.request(
            "GET",
            "/rota-inexistente",
        )

        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_chat_routes_multiple_memory_delete_request(self):
        cookie = self.login()

        with patch.object(
            app.friday,
            "interpret",
            return_value="request_memory_delete_many",
        ) as mock_interpret, patch.object(
            app.friday,
            "execute",
            return_value="Confirme a exclusão das lembranças 1 e 2.",
        ) as mock_execute:
            status, _, body = self.post(
                "/chat",
                {"command": "exclua as lembranças 1 e 2"},
                cookie=cookie,
            )

        self.assertEqual(status, 200)
        self.assertEqual(
            body["response"],
            "Confirme a exclusão das lembranças 1 e 2.",
        )

        mock_interpret.assert_called_once_with(
            "exclua as lembranças 1 e 2"
        )

        mock_execute.assert_called_once()
        args, kwargs = mock_execute.call_args

        self.assertEqual(
            args[:3],
            (
                "request_memory_delete_many",
                "exclua as lembranças 1 e 2",
                "owner",
            ),
        )
        self.assertIsNotNone(kwargs.get("session"))
        
if __name__ == "__main__":
    unittest.main()
