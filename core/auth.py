import os
import hmac
import math
import time


class FridayAuth:
    def __init__(
        self,
        password=None,
        *,
        max_attempts=3,
        lockout_seconds=30,
        clock=time.monotonic,
    ):
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise TypeError("max_attempts deve ser um inteiro positivo")
        if max_attempts <= 0:
            raise ValueError("max_attempts deve ser um inteiro positivo")
        if (
            isinstance(lockout_seconds, bool)
            or not isinstance(lockout_seconds, (int, float))
            or not math.isfinite(lockout_seconds)
            or lockout_seconds <= 0
        ):
            raise ValueError("lockout_seconds deve ser positivo e finito")

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
        if (
            not isinstance(password, str)
            or not isinstance(self.password, str)
            or not self.password
        ):
            return False

        now = self._clock()
        client_key = str(client_key)

        if hmac.compare_digest(password, self.password):
            self._attempts_by_client.pop(client_key, None)
            return True

        for key, state in list(self._attempts_by_client.items()):
            if now - state["last_attempt"] >= max(
                self.lockout_seconds * 2,
                60,
            ):
                del self._attempts_by_client[key]

        state = self._attempts_by_client.get(client_key)
        if state and state["locked_until"] > now:
            return False

        if state and state["locked_until"]:
            del self._attempts_by_client[client_key]
            state = None

        failures = state["failures"] if state else 0
        failures += 1

        self._attempts_by_client[client_key] = {
            "failures": 0 if failures >= self.max_attempts else failures,
            "locked_until": (
                now + self.lockout_seconds
                if failures >= self.max_attempts
                else 0
            ),
            "last_attempt": now,
        }

        return False