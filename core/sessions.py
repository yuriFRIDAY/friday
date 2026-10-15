import math
import secrets
import time


class Session:
    """Representa uma sessão autenticada da FRIDAY."""

    def __init__(self, token, expires_at):
        self.token = token
        self.expires_at = expires_at
        self.history = []
        self.pending_action = None


class SessionStore:
    """Armazena sessões independentes com expiração e histórico próprio."""

    def __init__(
        self,
        ttl_seconds=1800,
        *,
        clock=time.monotonic,
        token_factory=None,
    ):
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(ttl_seconds)
            or ttl_seconds <= 0
        ):
            raise ValueError("ttl_seconds deve ser positivo e finito")

        if not callable(clock):
            raise TypeError("clock deve ser uma função")

        if token_factory is not None and not callable(token_factory):
            raise TypeError("token_factory deve ser uma função")

        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._token_factory = token_factory or (
            lambda: secrets.token_urlsafe(32)
        )
        self._sessions = {}

    def cleanup_expired(self):
        """Remove sessões expiradas e limpa seus históricos."""

        now = self._clock()

        expired_tokens = [
            token
            for token, session in self._sessions.items()
            if now >= session.expires_at
        ]

        for token in expired_tokens:
            self.invalidate(token)

        return len(expired_tokens)

    def create(self):
        """Cria uma sessão com token aleatório e prazo de expiração."""

        # Evita acumular sessões abandonadas na memória.
        self.cleanup_expired()

        token = self._token_factory()

        if not isinstance(token, str) or not token:
            raise RuntimeError(
                "O gerador produziu um token de sessão inválido"
            )

        if token in self._sessions:
            raise RuntimeError(
                "O gerador produziu um token de sessão duplicado"
            )

        self._sessions[token] = Session(
            token=token,
            expires_at=self._clock() + self.ttl_seconds,
        )

        return token

    def get(self, token):
        """Retorna a sessão válida ou None se estiver ausente/expirada."""

        if not isinstance(token, str) or not token:
            return None

        session = self._sessions.get(token)

        if session is None:
            return None

        if self._clock() >= session.expires_at:
            self.invalidate(token)
            return None

        return session

    def get_history(self, token):
        """Retorna o histórico da sessão válida."""

        session = self.get(token)
        return session.history if session else None

    def invalidate(self, token):
        """Invalida uma sessão e limpa seu histórico."""

        if not isinstance(token, str) or not token:
            return False

        session = self._sessions.pop(token, None)

        if session is None:
            return False

        session.history.clear()
        session.pending_action = None
        return True
