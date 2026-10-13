
import re

from core.memory import FridayMemory
from core.permissions import PermissionManager
from core.context import FridayContext
from core.personality import FridayPersonality
from core.tools.manager import ToolManager
from core.audit import AuditLog


class FridayAssistant:

    def __init__(self):
        self.name = "FRIDAY"
        self.memory = FridayMemory()
        self.permissions = PermissionManager()
        self.context = FridayContext()
        self.personality = FridayPersonality()
        self.tools = ToolManager()
        self.tools.load_default_tools()
        self.audit = AuditLog()


    def interpret(self, command):
        command = command.lower().strip()

        if command == "sair":
            return "exit"

        if command in (
            "oi", "olá", "ola", "bom dia",
            "boa tarde", "boa noite"
        ):
            return "greeting"

        if "status" in command:
            return "status"

        if (
            "quem é você" in command
            or "quem e voce" in command
        ):
            return "identity"

        if "lembre que" in command:
            return "remember"

        # Listagem explícita de todas as lembranças.
        if (
            "listar lembranças" in command
            or "listar memorias" in command
            or "listar memórias" in command
            or "mostrar todas as lembranças" in command
            or "mostrar todas as memorias" in command
            or "mostrar todas as memórias" in command
            or command in (
                "mostrar lembranças",
                "mostrar memorias",
                "mostrar memórias",
                "o que você lembra",
                "o que voce lembra",
            )
        ):
            return "recall"

        if re.match(
            r"^(corrija|corrigir) a lembrança\b",
            command
        ):
            return "update_memory"

        if command.startswith((
            "calcule ",
            "calcular ",
            "quanto é ",
            "quanto e ",
        )):
            return "calculate"

        if (
            "sobre você" in command
            or "sobre voce" in command
            or "suas informações" in command
        ):
            return "context"

        # Perguntas específicas seguem para a IA,
        # que poderá consultar as lembranças relevantes.
        return "unknown"

    def format_response(self, message):
        instructions = self.personality.get_instructions()
        name = instructions.get("name", "FRIDAY")
        return f"{name}: {message}"

    def retrieve_relevant_memories(self, question, limit=5):
        memories = self.memory.get_details()

        if not memories:
            return []

        stopwords = {
            "a", "o", "as", "os", "um", "uma", "de", "do",
            "da", "dos", "das", "em", "no", "na", "nos",
            "nas", "e", "ou", "que", "eu", "meu", "minha",
            "meus", "minhas", "você", "voce", "é", "para",
            "por", "com", "sobre", "como", "qual", "quais",
            "quem", "quando", "onde", "está", "esta", "são",
            "sao", "foi", "ser", "tem", "tenho", "lembra",
            "lembrar", "disse", "falamos"
        }

        question_words = set(
            re.findall(r"\w+", question.lower())
        ) - stopwords

        ranked = []

        for memory in memories:
            content_words = set(
                re.findall(
                    r"\w+",
                    memory["content"].lower()
                )
            ) - stopwords

            overlap = len(question_words & content_words)

            if overlap > 0:
                ranked.append((overlap, memory))

        ranked.sort(
            key=lambda item: item[0],
            reverse=True
        )

        return [
            item[1]["content"]
            for item in ranked[:limit]
        ]

    def execute(self, intention, command, role, history=None):
        self.audit.record("COMMAND", command)

        if intention == "unknown":
            self.audit.record("UNKNOWN_COMMAND", command)

            if not self.permissions.has_permission(role, "converse"):
                self.audit.record(
                    "PERMISSION_DENIED",
                    f"{role} -> converse"
                )
                return "Acesso negado para esta ação."

            from core.ai_provider import FridayAIProvider

            provider = FridayAIProvider()

            if not provider.available():
                return "A IA não está configurada."

            memories = self.retrieve_relevant_memories(command)
            memory_context = ""

            if memories:
                memory_context = (
                    "\n\nLembranças relevantes da FRIDAY:\n"
                    + "\n".join(
                        f"- {memory}" for memory in memories
                    )
                    + "\nUse essas informações somente quando "
                    "forem relevantes. Não invente informações "
                    "pessoais ausentes."
                )

            contextual_command = command

            if memory_context:
                contextual_command = (
                    f"{memory_context}\n\n"
                    f"Pergunta do comandante: {command}"
                )

            response = provider.generate(
                contextual_command,
                history=history
            )

            if not response:
                self.audit.record("AI_ERROR", "empty response")
                return "Não consegui obter uma resposta da IA agora."

            self.audit.record("AI_RESPONSE", "Resposta recebida")
            return response

        permission_map = {
            "context": "converse",
            "greeting": "converse",
            "status": "converse",
            "identity": "converse",
            "remember": "write_memory",
            "recall": "read_memory",
            "update_memory": "write_memory",
            "calculate": "use_tools",
            "exit": "converse",
        }

        required_permission = permission_map.get(intention)

        if required_permission is None:
            self.audit.record("UNKNOWN_INTENTION", intention)
            return "Não reconheço essa intenção."

        if not self.permissions.has_permission(
            role, required_permission
        ):
            self.audit.record(
                "PERMISSION_DENIED",
                f"{role} -> {required_permission}"
            )
            return "Acesso negado para esta ação."

        self.audit.record(
            "PERMISSION_GRANTED",
            f"{role} -> {required_permission}"
        )

        if intention == "context":
            information = self.context.get_context()
            return (
                f"Sou {information['name']}, "
                "seu assistente pessoal. "
                f"Meu idioma configurado é "
                f"{information['language']}."
            )

        if intention == "greeting":
            return "Olá, comandante. Como posso ajudar?"

        if intention == "status":
            return "Todos os sistemas básicos estão operacionais."

        if intention == "identity":
            return "Eu sou FRIDAY, seu assistente pessoal."

        if intention == "remember":
            information = re.sub(
                r"lembre que",
                "",
                command,
                count=1,
                flags=re.IGNORECASE
            ).strip()

            if not information:
                return "Não recebi nenhuma informação para armazenar."

            text = information.lower()

            if any(word in text for word in (
                "projeto tony stark",
                "meu projeto",
                "projeto friday",
                "projeto da friday",
            )):
                category = "projeto"

            elif any(word in text for word in (
                "prefiro",
                "minha preferência",
                "minhas preferências",
                "gosto de",
                "não gosto de",
            )):
                category = "preferencia"

            elif any(word in text for word in (
                "estudo",
                "estou aprendendo",
                "estou estudando",
                "quero aprender",
            )):
                category = "estudos"

            else:
                category = "geral"

            self.memory.save(information, category)
            self.audit.record(
                "MEMORY_SAVED",
                f"{category}: {information}"
            )

            return (
                "Informação armazenada na memória, "
                f"categoria: {category}."
            )

        if intention == "update_memory":
            match = re.match(
                r"^(?:corrija|corrigir) a lembrança\s+"
                r"(\d+)\s+para\s+(.+)$",
                command.strip(),
                re.IGNORECASE
            )

            if not match:
                return (
                    "Formato inválido. Use: "
                    "Corrija a lembrança 3 para novo conteúdo."
                )

            memory_id = int(match.group(1))
            new_content = match.group(2).strip()

            if not new_content:
                return "O novo conteúdo não pode estar vazio."

            updated = self.memory.update(
                memory_id,
                new_content
            )

            if not updated:
                return (
                    f"Não encontrei uma lembrança "
                    f"com ID {memory_id}."
                )

            self.audit.record(
                "MEMORY_UPDATED",
                f"ID {memory_id}: conteúdo corrigido"
            )

            return (
                f"Lembrança {memory_id} corrigida "
                "com sucesso."
            )

        if intention == "recall":
            memories = self.memory.get_details()

            if not memories:
                return "Minha memória ainda está vazia."

            response = "Estas são minhas lembranças armazenadas:\n"

            for memory in memories:
                response += (
                    f"\nID: {memory['id']}"
                    f"\nCategoria: {memory['category']}"
                    f"\nInformação: {memory['content']}\n"
                )

            return response.rstrip()

        if intention == "calculate":
            calculator = self.tools.get("calculator")

            if calculator is None:
                self.audit.record(
                    "TOOL_ERROR",
                    "calculator not found"
                )
                return "A ferramenta calculadora não está disponível."

            expression = command.lower().strip()

            prefixes = [
                "calcule ",
                "calcular ",
                "quanto é ",
                "quanto e ",
            ]

            for prefix in prefixes:
                if expression.startswith(prefix):
                    expression = expression[len(prefix):].strip()
                    break

            try:
                if "+" in expression:
                    parts = expression.split("+")
                    if len(parts) != 2:
                        raise ValueError("Expressão inválida")

                    a = float(parts[0].strip())
                    b = float(parts[1].strip())
                    result = calculator.add(a, b)

                elif "-" in expression:
                    parts = expression.split("-")
                    if len(parts) != 2:
                        raise ValueError("Expressão inválida")

                    a = float(parts[0].strip())
                    b = float(parts[1].strip())
                    result = calculator.subtract(a, b)

                elif "*" in expression:
                    parts = expression.split("*")
                    if len(parts) != 2:
                        raise ValueError("Expressão inválida")

                    a = float(parts[0].strip())
                    b = float(parts[1].strip())
                    result = calculator.multiply(a, b)

                elif "/" in expression:
                    parts = expression.split("/")
                    if len(parts) != 2:
                        raise ValueError("Expressão inválida")

                    a = float(parts[0].strip())
                    b = float(parts[1].strip())
                    result = calculator.divide(a, b)

                else:
                    return "Não consegui identificar a operação."

                self.audit.record(
                    "TOOL_EXECUTION",
                    f"calculator -> {result}"
                )

                return f"O resultado é {result}."

            except (ValueError, IndexError, ZeroDivisionError):
                self.audit.record(
                    "TOOL_ERROR",
                    "invalid calculation"
                )
                return "Não consegui interpretar esse cálculo."

        if intention == "exit":
            self.audit.record(
                "SYSTEM_EXIT",
                "Encerramento solicitado"
            )
            return None

        return "Ainda não sei executar essa ação."
