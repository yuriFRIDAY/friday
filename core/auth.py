import os
import hmac
import math
import time


class FridayAuth:
    """Gerencia a autenticação e o bloqueio por tentativas incorretas."""

    def __init__(
        self,
        password=None,
        *,
        max_attempts=3,
        lockout_seconds=30,
        clock=time.monotonic,
    ):
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or max_attempts <= 0
        ):
            raise ValueError(
                "max_attempts deve ser um inteiro positivo"
            )

        if (
            isinstance(lockout_seconds, bool)
            or not isinstance(lockout_seconds, (int, float))
            or not math.isfinite(lockout_seconds)
            or lockout_seconds <= 0
        ):
            raise ValueError(
                "lockout_seconds deve ser positivo e finito"
            )

        if not callable(clock):
            raise TypeError("clock deve ser uma função")

        self.password = (
            os.getenv("FRIDAY_PASSWORD")
            if password is None
            else password
        )

        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._clock = clock
        self._attempts_by_client = {}

    def authenticate(self, password, client_key="local"):
        """
        Autentica o cliente e bloqueia novas tentativas
        após o limite configurado.
        """

        if (
            not isinstance(password, str)
            or not isinstance(self.password, str)
            or not self.password
        ):
            return False

        now = self._clock()
        client_key = str(client_key)

        state = self._attempts_by_client.get(client_key)

        # Credenciais válidas sempre podem recuperar o acesso do cliente.
        if hmac.compare_digest(password, self.password):
            self._attempts_by_client.pop(client_key, None)
            return True

        # Bloqueia novas tentativas incorretas até o prazo terminar.
        if state and state["locked_until"] > now:
            return False

        # Reinicia as tentativas quando o bloqueio expira.
        if state and state["locked_until"]:
            del self._attempts_by_client[client_key]
            state = None

        failures = state["failures"] if state else 0
        failures += 1

        if failures >= self.max_attempts:
            self._attempts_by_client[client_key] = {
                "failures": 0,
                "locked_until": now + self.lockout_seconds,
            }
        else:
            self._attempts_by_client[client_key] = {
                "failures": failures,
                "locked_until": 0,
            }

        return False

    def remaining_lockout(self, client_key="local"):
        """
        Retorna os segundos restantes do bloqueio.
        Retorna zero se o cliente não estiver bloqueado.
        """

        state = self._attempts_by_client.get(str(client_key))

        if not state:
            return 0

        remaining = state["locked_until"] - self._clock()
        return max(0, math.ceil(remaining))