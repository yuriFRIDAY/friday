import os
import hmac
import time


class FridayAuth:
    def __init__(self):
        self.password = os.getenv("FRIDAY_PASSWORD")
        self.authenticated = False
        self.failed_attempts = 0
        self.max_attempts = 3
        self.lockout_seconds = 30
        self.locked_until = 0

    def authenticate(self, password):
        if not self.password:
            return False

        now = time.monotonic()

        if now < self.locked_until:
            return False

        if hmac.compare_digest(password, self.password):
            self.authenticated = True
            self.failed_attempts = 0
            self.locked_until = 0
            return True

        self.failed_attempts += 1

        if self.failed_attempts >= self.max_attempts:
            self.failed_attempts = 0
            self.locked_until = now + self.lockout_seconds

        return False

    def is_authenticated(self):
        return self.authenticated

    def logout(self):
        self.authenticated = False