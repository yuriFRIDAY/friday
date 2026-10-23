import json
import logging
import os
import time
import sqlite3
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

try:
    from zoneinfo import ZoneInfo
    HAS_ZONEINFO = True
except ImportError:
    HAS_ZONEINFO = False

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
        # Modelo oficial da Groq que suporta function calling e visão
                self.model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        self.vision_model = "qwen/qwen3.8-27b"
        self.base_url = "https://api.groq.com/openai/v1"
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.data_dir = os.getenv("DATA_DIR", ".")
        if os.path.exists("/data") and os.access("/data", os.W_OK):
            self.data_dir = "/data"
        self.db_path = os.path.join(self.data_dir, "friday.db")

    def available(self):
        return bool(self.api_key.strip())

    def _get_current_time_context(self):
        try:
            now = datetime.now(ZoneInfo("America/Sao_Paulo")) if HAS_ZONEINFO else datetime.now(timezone(timedelta(hours=-3)))
        except Exception:
            now = datetime.now(timezone(timedelta(hours=-3)))
        dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
        return f"Hoje é {dias[now.weekday()]}, {now.strftime('%d/%m/%Y')} e agora são {now.strftime('%H:%M')} (Horário de Brasília)."

    def _get_recent_memories(self):
        try:
            if not os.path.exists(self.db_path):
                return ""
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS ai_memory (key TEXT PRIMARY KEY, value TEXT)")
                rows = conn.execute("SELECT key, value FROM ai_memory ORDER BY rowid DESC LIMIT 10").fetchall()
                if rows:
                    return "\nMemórias sobre o Chefe:\n" + "\n".join(f"- {r[0]}: {r[1]}" for r in rows)
        except Exception:
            pass
        return ""

    def _get_system_prompt(self):
        memories = self._get_recent_memories()
        time_context = self._get_current_time_context()
        return f"""
Você é FRIDAY, a IA pessoal do Chefe, inspirada no JARVIS.
Personalidade: formal, leal, proativa, precisa e levemente espirituosa.
Trate o usuário por "chefe" ou "senhor".
Responda em português brasileiro.
Seu e-mail (do Chefe) é {self.smtp_user}.

CONTEXTO DE TEMPO:
{time_context}
Use para calcular datas relativas. PREFIRA start_natural/end_natural em vez de ISO 8601.

CONCISÃO:
Seja direto. Máximo 2-3 frases, salvo quando pedirem resumo detalhado.

FERRAMENTAS:
Você DEVE usar EXATAMENTE os nomes das ferramentas fornecidas no schema JSON. 
NÃO invente nomes como 'websearch' ou 'weather'. Use 'search_web' e 'get_weather'.

1. `save_memory` / `recall_memory` / `delete_memory`
2. `read_emails`, `read_email_full`, `get_email_thread`
3. `reply_to_email`, `send_email`
4. `search_web`, `deep_search`, `read_webpage`
5. `get_calendar_events`, `create_calendar_event`, `update_calendar_event`, `delete_calendar_event`, `find_free_slots`
6. `play_music`

REGRA CRÍTICA DE AGENDA:
- Compromissos em MEMÓRIAS: responda direto da memória, sem consultar a agenda.
- Só use `get_calendar_events` quando o usuário pedir a agenda explicitamente.

{memories}
"""

    def _build_messages(self, message, history=None):
        messages = [{"role": "system", "content": self._get_system_prompt().strip()}]
        if history:
            for item in history[-10:]:
                if (isinstance(item, dict) and item.get("role") in ("user", "assistant")
                        and isinstance(item.get("content"), str)):
                    messages.append({"role": item["role"], "content": item["content"][:4000]})
        messages.append({"role": "user", "content": str(message)[:4000]})
        return messages

    def generate(self, message, history=None):
        if not self.available():
            return None
        messages = self._build_messages(message, history)
        payload = {"model": self.model, "messages": messages, "stream": False}
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
        payload = {"model": self.vision_model, "messages": messages, "stream": False}
        try:
            result = self._send_raw_request(payload)
            choices = result.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
            return "A IA não retornou uma descrição."
        except Exception as e:
            logger.error(f"Erro visão: {str(e)}")
            return f"Falha ao analisar imagem: {str(e)}"

    def chat_with_tools(self, user_message, history, tools):
        if not self.available():
            return {"error": "IA indisponível."}
        formatted_tools = [{
            "type": "function",
            "function": {"name": n, "description": s.get("description", ""), "parameters": s.get("parameters", {})}
        } for n, s in tools.items()]
        messages = self._build_messages(user_message, history)
        payload = {"model": self.model, "messages": messages, "tools": formatted_tools,
                   "tool_choice": "auto", "stream": False}
        try:
            result = self._send_raw_request(payload)
            choices = result.get("choices", [])
            if not choices:
                return {"text": "Não consegui obter uma resposta."}
            message = choices[0].get("message", {})
            if "tool_calls" in message and message["tool_calls"]:
                tc = message["tool_calls"][0]
                fd = tc.get("function", {})
                try:
                    args = json.loads(fd.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                return {"tool_call": {"id": tc.get("id"), "name": fd.get("name"),
                                      "arguments": args, "raw_arguments": fd.get("arguments", "{}")}}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return {"text": content.strip()}
            return {"text": "Comando processado."}
        except HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
            except:
                err_body = str(e)
            return {"error": f"HTTP {e.code}: {err_body}"}
        except Exception as e:
            return {"error": str(e)}

    def chat_with_tool_result(self, user_message, tool_name, tool_result, history, tool_call_id=None, raw_arguments="{}"):
        if not self.available():
            return None
        messages = self._build_messages(user_message, history)
        messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": tool_call_id or "call_unknown", "type": "function",
                            "function": {"name": tool_name, "arguments": raw_arguments}}]
        })
        messages.append({"role": "tool", "tool_call_id": tool_call_id or "call_unknown",
                         "content": json.dumps(tool_result, ensure_ascii=False)})
        payload = {"model": self.model, "messages": messages, "stream": False}
        return self._execute_request(payload)

    def chat_with_full_history(self, messages, tools):
        if not self.available():
            return {"error": "IA indisponível."}
        formatted_tools = [{
            "type": "function",
            "function": {"name": n, "description": s.get("description", ""), "parameters": s.get("parameters", {})}
        } for n, s in tools.items()]
        full_messages = [{"role": "system", "content": self._get_system_prompt().strip()}] + messages
        payload = {"model": self.model, "messages": full_messages, "tools": formatted_tools,
                   "tool_choice": "auto", "stream": False}
        try:
            result = self._send_raw_request(payload)
            choices = result.get("choices", [])
            if not choices:
                return {"text": "Não consegui obter uma resposta."}
            message = choices[0].get("message", {})
            if "tool_calls" in message and message["tool_calls"]:
                tc = message["tool_calls"][0]
                fd = tc.get("function", {})
                try:
                    args = json.loads(fd.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                return {"tool_call": {"id": tc.get("id"), "name": fd.get("name"),
                                      "arguments": args, "raw_arguments": fd.get("arguments", "{}")}}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return {"text": content.strip()}
            return {"text": "Comando processado."}
        except HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
            except:
                err_body = str(e)
            logger.error(f"Erro full history (HTTP {e.code}): {err_body}")
            return {"error": f"HTTP {e.code}: {err_body}"}
        except Exception as e:
            logger.error(f"Erro full history: {e}")
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
                logger.error(f"Erro HTTP {status_code} Groq: {error.read().decode('utf-8')}")
            except Exception:
                logger.error(f"Erro HTTP {status_code} Groq.")
            return None
        except Exception as error:
            outcome = "connection_error"
            logger.error(f"Falha conexão: {type(error).__name__} - {str(error)}")
            return None
        finally:
            elapsed = round((time.monotonic() - started_at) * 1000)
            logger.info(f"AI_REQUEST provider=groq outcome={outcome} duration_ms={elapsed}")
