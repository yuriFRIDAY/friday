
class FridayContext:
    """Organiza a identidade e o contexto usado pela FRIDAY."""

    def __init__(self):
        self.name = "FRIDAY"
        self.role = "personal_assistant"
        self.language = "pt-BR"
        self.owner = "owner"

    def get_context(self):
        """Mantém compatibilidade com o código existente."""
        return {
            "name": self.name,
            "role": self.role,
            "language": self.language,
            "owner": self.owner,
        }

    @staticmethod
    def _clean_text(value, max_length):
        """Aceita apenas texto e limita seu tamanho."""
        if not isinstance(value, str):
            return ""
        return value.strip()[:max_length]

    def build_context(
        self,
        question,
        history=None,
        memories=None,
    ):
        """
        Monta um contexto estruturado para responder à pergunta.

        Não executa ações, não altera permissões e não modifica
        as lembranças armazenadas no banco de dados.
        """
        clean_question = self._clean_text(question, 4000)

        clean_history = []

        if isinstance(history, (list, tuple)):
            for item in history[-10:]:
                if not isinstance(item, dict):
                    continue

                role = item.get("role")
                content = item.get("content")

                if role not in ("user", "assistant"):
                    continue

                clean_content = self._clean_text(content, 4000)

                if not clean_content:
                    continue

                clean_history.append({
                    "role": role,
                    "content": clean_content,
                })

        clean_memories = []

        if isinstance(memories, (list, tuple)):
            for memory in memories[:5]:
                clean_memory = self._clean_text(memory, 1000)

                if clean_memory:
                    clean_memories.append(clean_memory)

        return {
            "identity": self.get_context(),
            "question": clean_question,
            "history": clean_history,
            "memories": clean_memories,
        }
