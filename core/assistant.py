class FridayAssistant:

        def __init__(self):
            self.name = "FRIDAY"

        def interpret(self, command):
            command = command.lower().strip()

            if "olá" in command or "oi" in command:
                return "greeting"

            if "status" in command:
                return "status"

            if "quem é você" in command:
                return "identity"

            if "sair" in command:
                return "exit"

            return "unknown"

        def execute(self, intention):

            if intention == "greeting":
                return "Olá. Como posso ajudar?"

            if intention == "status":
                return "Todos os sistemas básicos estão operacionais."

            if intention == "identity":
                return "Eu sou FRIDAY, seu assistente pessoal."

            if intention == "exit":
                return None

            return "Ainda não sei executar essa ação."