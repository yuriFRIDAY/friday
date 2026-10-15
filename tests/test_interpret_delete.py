from core.assistant import FridayAssistant

friday = FridayAssistant()

comandos = [
    "esqueça a ID 4 e ID 2",
    "esqueça IDs 4 e 2",
    "exclua 4 e 2",
    "esqueça a ID 4",
]

for comando in comandos:
    print(comando, "->", friday.interpret(comando))
