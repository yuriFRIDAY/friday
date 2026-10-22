import json
import logging
import os
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)

logger.propagate = False


class FridayAIProvider:
    """Conecta a FRIDAY aos modelos de IA da Groq."""

    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY", "")
        self.model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        self.base_url = "https://api.groq.com/openai/v1"

    def available(self):
        return bool(self.api_key.strip())

    def _get_system_prompt(self):
        return """
    Você é FRIDAY, a inteligência artificial pessoal do Chefe, inspirada no JARVIS.
    Sua personalidade é formal, leal, proativa, precisa e levemente espirituosa quando apropriado.
    Trate o usuário por "chefe" ou "senhor".
    Mantenha respostas concisas e acionáveis. Responda sempre em português brasileiro.

    REGRA DE OURO PARA FERRAMENTAS (MUITO IMPORTANTE):
    Você tem acesso a ferramentas reais no sistema. NUNCA finja que salvou ou lembrou de algo usando apenas texto.
    1. SEMPRE que o usuário pedir para você lembrar ou salvar algo, use `save_memory`.
    2. SEMPRE que o usuário perguntar sobre algo que você deveria lembrar, use `recall_memory`.
    3. Para notícias, clima ou fatos recentes, use `search_web`.

    COMPORTAMENTO PROATIVO E ANTECIPAÇÃO:
    Você não é apenas uma executora, você é uma assistente de inteligência.
    Sempre que você executar uma ação (como salvar uma memória) ou o usuário mencionar algo novo (como um restaurante favorito, um filme ou um evento), ANTECIPE as necessidades dele.
    Sugira ações lógicas que você pode realizar usando as ferramentas disponíveis.
    Exemplos de proatividade:
    - Se o usuário salvar o nome de uma pizzaria, diga: "Registrado. Deseja que eu pesquise os horários de funcionamento ou faça uma reserva para hoje?"
    - Se o usuário falar de um evento, diga: "Anotado. Quer que eu crie um lembrete na sua agenda para não esquecer?"
    - Se o usuário mencionar um destino, diga: "Interessante. Quer que eu verifique a previsão do tempo lá para os próximos dias?"
    Seja sempre útil, mas educada. Termine suas respostas com uma sugestão de próximo passo quando fizer sentido.
    """

    def _build_messages(self, message, history=None):
        messages = [{"role": "system", "content": self._get_system_prompt().strip()}]

        if history:
            for item in history[-10:]:
                if (
                    isinstance(item, dict)
                    and item.get("role") in ("user", "assistant")
                    and isinstance(item.get("content"), str)
                ):
                    messages.append({
                        "role": item["role"],
                        "content": item["content"][:4000],
                    })

        messages.append({
            "role": "user",
            "content": str(message)[:4000],
        })
        return messages

    def generate(self, message, history=None):
        """Gera uma resposta simples sem ferramentas (usada pelo core/assistant.py e como fallback)."""
        if not self.available():
            logger.warning("IA indisponível: chave de API não configurada.")
            return None

        messages = self._build_messages(message, history)
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        return self._execute_request(payload)

    def chat_with_tools(self, user_message, history, tools):
        """Gera uma resposta com suporte a ferramentas (Function Calling)."""
        if not self.available():
            return {"error": "IA indisponível."}

        formatted_tools = []
        for name, spec in tools.items():
            formatted_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec.get("description", ""),
                    "parameters": spec.get("parameters", {})
                }
            })

        messages = self._build_messages(user_message, history)
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": formatted_tools,
            "tool_choice": "auto",
            "stream": False,
        }

        try:
            result = self._send_raw_request(payload)
            choices = result.get("choices", [])
            if not choices:
                return {"text": "Não consegui obter uma resposta."}

            message = choices[0].get("message", {})

            if "tool_calls" in message and message["tool_calls"]:
                tool_call = message["tool_calls"][0]
                function_data = tool_call.get("function", {})

                try:
                    args = json.loads(function_data.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                return {
                    "tool_call": {
                        "id": tool_call.get("id"),
                        "name": function_data.get("name"),
                        "arguments": args,
                        "raw_arguments": function_data.get("arguments", "{}")
                    }
                }

            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return {"text": content.strip()}

            return {"text": "Comando processado."}

        except Exception as e:
            logger.error(f"Erro no chat_with_tools: {e}")
            return {"error": str(e)}

    def chat_with_tool_result(self, user_message, tool_name, tool_result, history, tool_call_id=None, raw_arguments="{}"):
        """Envia o resultado da ferramenta de volta para a IA formatar a resposta final."""
        if not self.available():
            return None

        messages = self._build_messages(user_message, history)

        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tool_call_id or "call_unknown",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": raw_arguments
                    }
                }
            ]
        })

        result_str = json.dumps(tool_result, ensure_ascii=False)
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id or "call_unknown",
            "content": result_str
        })

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }

        return self._execute_request(payload)

    def _send_raw_request(self, payload):
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
            method="POST",
        )
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def _execute_request(self, payload):
        started_at = time.monotonic()
        outcome = "unknown"
        status_code = None

        try:
            result = self._send_raw_request(payload)
            choices = result.get("choices", [])

            if choices:
                content = choices[0].get("message", {}).get("content")
                if isinstance(content, str) and content.strip():
                    outcome = "success"
                    return content.strip()

            outcome = "empty_response"
            return None

        except HTTPError as error:
            status_code = error.code
            outcome = "http_error"
            try:
                error_body = error.read().decode('utf-8')
                logger.error(f"FRIDAY AI: Erro HTTP {status_code} da Groq: {error_body}")
            except Exception:
                logger.error(f"FRIDAY AI: Erro HTTP {status_code} sem detalhes.")
            return None
        except Exception as error:
            outcome = "connection_error"
            logger.error(f"FRIDAY AI: Falha de conexão: {type(error).__name__} - {str(error)}")
            return None
        finally:
            elapsed_ms = round((time.monotonic() - started_at) * 1000)
            logger.info(f"AI_REQUEST provider=groq outcome={outcome} duration_ms={elapsed_ms}")