import json
import logging
import os
import time
import sqlite3
from datetime import datetime
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
        self.vision_model = "llama-3.2-11b-vision-preview"
        self.base_url = "https://api.groq.com/openai/v1"
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.data_dir = os.getenv("DATA_DIR", ".")
        if os.path.exists("/data") and os.access("/data", os.W_OK):
            self.data_dir = "/data"
        self.db_path = os.path.join(self.data_dir, "friday.db")

    def available(self):
        return bool(self.api_key.strip())

    def _get_current_time_context(self):
        now = datetime.now()
        dias_semana = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
        dia_semana = dias_semana[now.weekday()]
        return f"Hoje é {dia_semana}, {now.strftime('%d/%m/%Y')} e agora são {now.strftime('%H:%M')}."

    def _get_recent_memories(self):
        try:
            if not os.path.exists(self.db_path):
                return ""
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS ai_memory (key TEXT PRIMARY KEY, value TEXT)")
                rows = conn.execute("SELECT key, value FROM ai_memory ORDER BY rowid DESC LIMIT 10").fetchall()
                if rows:
                    mem_text = "\nAqui estão as coisas que você lembra sobre o Chefe:\n"
                    for r in rows:
                        mem_text += f"- {r[0]}: {r[1]}\n"
                    return mem_text
        except Exception:
            pass
        return ""

    def _get_system_prompt(self):
        memories = self._get_recent_memories()
        time_context = self._get_current_time_context()
        return f"""
Você é FRIDAY, a inteligência artificial pessoal do Chefe, inspirada no JARVIS.
Sua personalidade é formal, leal, proativa, precisa e levemente espirituosa.
Trate o usuário por "chefe" ou "senhor".
Responda sempre em português brasileiro.
O seu próprio e-mail (do Chefe) é {self.smtp_user}. 

CONTEXTO DE TEMPO:
{time_context}
Use isso para calcular datas relativas (ex: "amanhã", "próxima sexta"). Se precisar usar a agenda, calcule o formato ISO 8601 com base nessa data.

REGRA DE OURO PARA CONCISÃO:
Seja direto e conciso. A menos que o usuário peça explicitamente um resumo detalhado ou uma explicação longa, limite suas respostas a no máximo 2 ou 3 frases curtas.

REGRA DE OURO PARA FERRAMENTAS:
Você tem acesso a ferramentas reais no sistema. NUNCA finja que salvou, lembrou ou reservou algo sem executar a ferramenta.
1. Para lembrar de algo, use `save_memory`.
2. Para consultar e-mails, use `read_emails`. Para ler a conversa inteira, use `get_email_thread`.
3. Para responder e-mails, use `reply_to_email`. O sistema vai redigir o corpo (body) da resposta automaticamente com base no contexto, mas você deve passar o assunto e o destinatário corretos.
4. Para fatos recentes, use `search_web`. Para ler o conteúdo completo de um link, use `read_webpage`.

RACIOCÍNIO EM MÚLTIPLOS PASSOS:
Você pode e deve usar MÚLTIPLAS ferramentas em sequência se a tarefa for complexa.
- Exemplo 1: "Veja se chegou e-mail do Render e pesquise como resolver". PRIMEIRO use `read_emails`. Depois use `search_web`.
- Exemplo 2 (Conflito): "Crie uma reunião amanhã às 15h". Passo 1: Use `get_calendar_events`. Passo 2: Verifique se há conflito. Passo 3: Avise ou crie.

{memories}
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

    def chat_with_vision(self, user_message, image_base64, history=None):
        if not self.available():
            return "A IA não está configurada."

        messages = [{"role": "system", "content": self._get_system_prompt().strip()}]
        
        if history:
            for item in history[-5:]:
                if isinstance(item, dict) and item.get("role") in ("user", "assistant"):
                    messages.append({"role": item["role"], "content": item["content"][:2000]})

        messages.append({"role": "user", "content": [
            {"type": "text", "text": user_message},
            {"type": "image_url", "image_url": {"url": image_base64}}
        ]})
        
        payload = {
            "model": self.vision_model,
            "messages": messages,
            "stream": False,
        }
        result = self._execute_request(payload)
        return result if result else "Não consegui analisar a imagem."

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
