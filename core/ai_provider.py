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
        self.smtp_user = os.getenv("SMTP_USER", "")

    def available(self):
        return bool(self.api_key.strip())

    def _get_system_prompt(self):
        return """
Você é FRIDAY, a inteligência artificial pessoal do Chefe, inspirada no JARVIS.
Sua personalidade é formal, leal, proativa, precisa e levemente espirituosa.
Trate o usuário por "chefe" ou "senhor".
Responda sempre em português brasileiro.

REGRA DE OURO PARA FERRAMENTAS:
Você tem acesso a ferramentas reais no sistema. NUNCA finja que salvou, lembrou ou reservou algo sem executar a ferramenta.
1. Para lembrar de algo, use `save_memory`.
2. Para recordar, use `recall_memory`.
3. Para fatos recentes ou telefones/endereços, use `search_web`.
4. Para ler e-mails, use `read_emails`.
5. Para enviar e-mail, use `send_email`.

RACIOCÍNIO EM MÚLTIPLOS PASSOS:
Você pode e deve usar MÚLTIPLAS ferramentas em sequência se a tarefa for complexa.
Exemplo: Se o usuário disser "Veja se chegou e-mail do Render e pesquise como resolver", você deve PRIMEIRO usar `read_emails`. Depois de receber o resultado, você deve usar `search_web` para buscar a solução do erro. Só então dê a resposta final.

O seu próprio e-mail (do Chefe) é {self.smtp_user}. 
Sempre que o Chefe pedir para enviar um e-mail para ele mesmo (ex: "me mande um e-mail"), use este endereço no campo "to_email".
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

    def chat_with_full_history(self, messages, tools):
        """Permite à IA raciocinar em múltiplos passos (Agent Loop)."""
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

        full_messages = [{"role": "system", "content": self._get_system_prompt().strip()}]
        full_messages.extend(messages)

        payload = {
            "model": self.model,
            "messages": full_messages,
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
            logger.error(f"Erro no chat_with_full_history: {e}")
            return {"error": str(e)}

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
