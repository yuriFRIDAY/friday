from core.assistant import FridayAssistant
from core.auth import FridayAuth


friday = FridayAssistant()
auth = FridayAuth()


print("================================")
print("        FRIDAY 0.0.5")
print("================================")
print("Sistema iniciando...")
print()


password = input("Autenticação FRIDAY: ")

if not auth.authenticate(password):

    print("FRIDAY: Acesso negado.")
    print("FRIDAY: Encerrando sistema.")

    exit()


print()
print("FRIDAY: Identidade confirmada.")
print("FRIDAY: Acesso autorizado.")
role = "owner"
print("FRIDAY online.")
print("Digite 'sair' para encerrar.")
print()


while True:

    command = input("Você: ")

    intention = friday.interpret(command)

    response = friday.execute(intention, command, role)

    if response is None:
        print("FRIDAY: Encerrando sistema.")
        auth.logout()
        break

    print(friday.format_response(response))