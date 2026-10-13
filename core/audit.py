
import re
from datetime import datetime


class AuditLog:
    """Registra eventos operacionais sem expor conteúdo pessoal."""

    ALLOWED_EVENTS = {
        "COMMAND",
        "UNKNOWN_COMMAND",
        "PERMISSION_DENIED",
        "PERMISSION_GRANTED",
        "UNKNOWN_INTENTION",
        "AI_ERROR",
        "AI_RESPONSE",
        "MEMORY_SAVED",
        "MEMORY_UPDATED",
        "TOOL_ERROR",
        "TOOL_EXECUTION",
        "SYSTEM_EXIT",
    }

    SAFE_STATIC_DETAILS = {
        "AI_ERROR": {"empty response"},
        "AI_RESPONSE": {"Resposta recebida"},
        "TOOL_ERROR": {
            "calculator not found",
            "invalid calculation",
        },
        "SYSTEM_EXIT": {"Encerramento solicitado"},
    }

    def __init__(self, filename="friday_audit.log"):
        self.filename = filename

    def _safe_details(self, event, details):
        """
        Permite somente detalhes reconhecidamente seguros.
        Texto livre é descartado por padrão.
        """
        if not isinstance(details, str):
            return ""

        details = details.strip()

        if not details:
            return ""

        # Detalhes fixos e previamente autorizados.
        if details in self.SAFE_STATIC_DETAILS.get(event, set()):
            return details

        # Registra apenas funções e permissões com formato conhecido.
        if event in {"PERMISSION_DENIED", "PERMISSION_GRANTED"}:
            match = re.fullmatch(
                r"(guest|user|owner|system) -> "
                r"(converse|read_memory|write_memory|use_tools|"
                r"configure|critical_operations)",
                details,
            )
            return details if match else ""

        # Para atualizações, registra somente o ID numérico.
        if event == "MEMORY_UPDATED":
            match = re.fullmatch(r"ID (\d+): conteúdo corrigido", details)
            return f"ID {match.group(1)}" if match else ""

        # Para ferramentas, registra a operação, sem o resultado.
        if event == "TOOL_EXECUTION":
            match = re.fullmatch(
                r"calculator -> [-+]?\d+(?:\.\d+)?",
                details,
            )
            return "calculator" if match else ""

        # Não registra conteúdo de comandos, lembranças ou
        # outros detalhes livres que não tenham sido autorizados.
        return ""

    def record(self, event, details=""):
        if not isinstance(event, str):
            event = "INVALID_EVENT"

        event = event.strip().upper()

        if not re.fullmatch(r"[A-Z_]{1,50}", event):
            event = "INVALID_EVENT"

        if event not in self.ALLOWED_EVENTS:
            event = "INVALID_EVENT"

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] {event}"

        safe_details = self._safe_details(event, details)

        if safe_details:
            entry += f": {safe_details}"

        with open(self.filename, "a", encoding="utf-8") as file:
            file.write(entry + "\n")
