from core.assistant import FridayAssistant


friday = FridayAssistant()

print("================================")
print("        FRIDAY 0.0.3")
print("================================")
print("FRIDAY online.")
print("Digite 'sair' para encerrar.")
print()

while True:

    command = input("Você: ")

    intention = friday.interpret(command)

    response = friday.execute(intention)

    if response is None:
        print("FRIDAY: Encerrando sistema.")
        break

    print("FRIDAY:", response)