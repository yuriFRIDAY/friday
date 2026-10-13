
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

        if "olá" in command or "oi" in command:
            return "greeting"

        if "status" in command:
            return "status"

        if "quem é você" in command:
            return "identity"

        if "lembre que" in command:
            return "remember"

        if "o que você lembra" in command:
            return "recall"

        if command.startswith((
            "calcule ",
            "calcular ",
            "quanto é ",
            "quanto e ",
        )):
            return "calculate"

        if "sobre você" in command or "suas informações" in command:
            return "context"

        return "unknown"

    def format_response(self, message):
        instructions = self.personality.get_instructions()
        name = instructions.get("name", "FRIDAY")
        return f"{name}: {message}"

    def execute(self, intention, command, role, history=None):
        self.audit.record("COMMAND", command)

        # Perguntas gerais passam pela IA, mantendo as permissões.
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


            # Seleciona lembranças relevantes para a pergunta.
            question = command.lower()

            if any(term in question for term in (
                "meu projeto",
                "projeto tony stark",
                "projeto friday",
                "sobre o projeto",
            )):
                memories = self.memory.get_by_category("projeto")

            elif any(term in question for term in (
                "minhas preferências",
                "minhas preferencias",
                "o que eu prefiro",
                "do que eu gosto",
            )):
                memories = self.memory.get_by_category("preferencia")

            elif any(term in question for term in (
                "meus estudos",
                "o que estou estudando",
                "o que estou aprendendo",
            )):
                memories = self.memory.get_by_category("estudos")

            else:
                memories = self.memory.get_all()

            # Limita o contexto enviado à IA.
            recent_memories = memories[-20:]


            memory_context = ""

            if recent_memories:
                memory_context = (
                    "\n\nInformações salvas na memória da FRIDAY:\n"
                    + "\n".join(
                        f"- {memory}"
                        for memory in recent_memories
                    )
                    + "\nUse essas informações quando forem relevantes. "
                    "Não invente informações pessoais ausentes."
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
            "calculate": "use_tools",
            "exit": "converse"
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
            information = command.replace(
                "lembre que", "", 1
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
                f"Informação armazenada na memória, "
                f"categoria: {category}."
            )
            
        if intention == "recall":
            memories = self.memory.get_all()

            if not memories:
                return "Minha memória ainda está vazia."

            response = (
                "Estas são as informações que tenho armazenadas:\n"
            )

            for memory in memories:
                response += f"- {memory}\n"

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
