from datetime import datetime


class AuditLog:

    def __init__(self, filename="friday_audit.log"):
        self.filename = filename

    def record(self, event, details=""):

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        entry = f"[{timestamp}] {event}"

        if details:
            entry += f": {details}"

        with open(self.filename, "a", encoding="utf-8") as file:
            file.write(entry + "\n")