print("================================")
print("        FRIDAY 0.0.2")
print("================================")
print("FRIDAY online.")
print("Digite 'sair' para encerrar.")
print()

while True:
    comando = input("Você: ")

    if comando.lower() == "sair":
        print("FRIDAY: Encerrando sistema.")
        break

    if comando.lower() == "olá":
        print("FRIDAY: Olá. Como posso ajudar?")

    elif comando.lower() == "status":
        print("FRIDAY: Todos os sistemas básicos estão operacionais.")

    elif comando.lower() == "quem é você":
        print("FRIDAY: Eu sou a primeira versão do seu assistente.")

    else:
        print("FRIDAY: Ainda não sei executar esse comando.")