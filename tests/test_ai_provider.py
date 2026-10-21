
from core.ai_provider import FridayAIProvider


def main():
    provider = FridayAIProvider()

    print("=== TESTE DA IA DA FRIDAY ===")
    print("Classe carregada:", type(provider).__name__)
    print("Modelo configurado:", provider.model)
    print("Chave configurada:", provider.available())

    if not provider.available():
        print("ERRO: configure OPENROUTER_API_KEY nos Secrets.")
        return

    print("\nEnviando pergunta ao modelo...")
    resposta = provider.generate(
        "Responda apenas: FRIDAY online."
    )

    if resposta:
        print("\nResposta da IA:", resposta)
        print("\nTESTE CONCLUÍDO COM SUCESSO.")
    else:
        print("\nA IA não retornou uma resposta.")
        print("Verifique a chave, o modelo e os limites da API.")


if __name__ == "__main__":
    main()