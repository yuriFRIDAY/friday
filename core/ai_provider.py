# core/ai_provider.py
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
    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY", "")
        self.base_url = "https://api.groq.com/openai/v1"
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.data_dir = os.getenv("DATA_DIR", ".")
        if os.path.exists("/data") and os.access("/data", os.W_OK):
            self.data_dir = "/data"
        self.db_path = os.path.join(self.data_dir, "friday.db")

    def available(self):
        return bool(self.api_key.strip())

    def _get_model(self, prompt_length):
        # Roteamento: Se for muito longo ou complexo, usa o 70B. Senão, 8B.
        return "llama-3.3-70b-versatile" if prompt_length > 1500 else "llama-3.1-8b-instant"

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
                rows = conn.execute("SELECT key, value FROM ai_memory ORDER BY rowid DESC LIMIT 5").fetchall()
                if rows:
                    return "\nMemórias sobre o Chefe:\n" + "\n".join(f"- {r[0]}: {r[1]}" for r in rows)
        except Exception:
            pass
        return ""

    def _summarize_history(self, history):
        if len(history) <= 8:
            return history
        
        old_messages = history[:-4]
        recent_messages = history[-4:]
        
        text_to_summarize = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in old_messages if isinstance(m.get('content'), str)])
        
        prompt = f"Resuma a conversa a seguir em um parágrafo curto e direto, focando no que o Chefe pediu e nas decisões tomadas:\n\n{text_to_summarize}"
        
        try:
            summary = self.generate(prompt)
            if summary:
                new_history = [{"role": "system", "content": f"Resumo do início da conversa: {summary}"}]
                new_history.extend(recent_messages)
                return new_history
        except Exception:
            pass
        
        return recent_messages

    def _get_system_prompt(self, history_context=""):
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

CONTEXTO DA CONVERSA:
{history_context}

CONCISÃO:
Seja direto. Máximo 2-3 frases, salvo quando pedirem resumo detalhado.

FERRAMENTAS DISPONÍVEIS:
Você TEM acesso a TODAS as ferramentas listadas abaixo. NUNCA diga que não possui uma ferramenta se ela estiver nesta lista.
1. get_current_time (data e hora)
2. get_weather (clima e previsão do tempo)
3. get_system_status (status do servidor)
4. get_crypto_price (cotações de cripto)
5. get_news (últimas notícias)
6. search_web (pesquisa na web)
7. deep_search (pesquisa e leitura de página)
8. read_webpage (ler URL)
9. send_email, reply_to_email, read_emails, read_email_full, get_email_thread
10. create_task, list_tasks, complete_task
11. save_memory, recall_memory, delete_memory
12. play_music (tocar, pausar, continuar)
13. get_morning_briefing
14. get_calendar_events, create_calendar_event, update_calendar_event, delete_calendar_event, find_free_slots

NUNCA diga que não possui uma ferramenta, pois você tem todas.
Se o usuário perguntar do clima, chame get_weather.

{memories}
"""

    def _build_messages(self, message, history=None):
        summary_context = ""
        if history:
            history = self._summarize_history(history)
            if history and history[0].get("role") == "system" and "Resumo" in history[0].get("content", ""):
                summary_context = history[0]["content"]
                history = history[1:]

        messages = [{"role": "system", "content": self._get_system_prompt(summary_context).strip()}]
        
        if history:
            for item in history[-8:]:
                if isinstance(item, dict) and item.get("role") in ("user", "assistant"):
                    if isinstance(item.get("content"), str):
                        messages.append({"role": item["role"], "content": item["content"][:4000]})
        
        messages.append({"role": "user", "content": str(message)[:4000]})
        return messages

    def generate(self, message, history=None):
        if not self.available():
            return None
        messages = self._build_messages(message, history)
        model = self._get_model(len(str(message)))
        payload = {"model": model, "messages": messages, "stream": False, "max_tokens": 4096}
        return self._execute_with_retry(payload)

    def chat_with_vision(self, user_message, image_base64, history=None):
        return "Desculpe, chefe. Meu módulo de visão está desativado no momento."

    def chat_with_tools(self, user_message, history, tools):
        if not self.available():
            return {"error": "IA indisponível."}
        formatted_tools = [{"type": "function", "function": {"name": n, "description": s.get("description", ""), "parameters": s.get("parameters", {})}} for n, s in tools.items()]
        messages = self._build_messages(user_message, history)
        model = self._get_model(len(str(user_message)))
        payload = {"model": model, "messages": messages, "tools": formatted_tools, "tool_choice": "auto", "stream": False, "max_tokens": 4096}
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
                except Exception:
                    args = {}
                return {"tool_call": {"id": tc.get("id"), "name": fd.get("name"), "arguments": args, "raw_arguments": fd.get("arguments", "{}")}}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return {"text": content.strip()}
            return {"text": "Comando processado."}
        except HTTPError as e:
            try: err_body = e.read().decode("utf-8")
            except Exception: err_body = str(e)
            return {"error": f"HTTP {e.code}: {err_body[:200]}"}
        except Exception as e:
            return {"error": str(e)}

    def chat_with_tool_result(self, user_message, tool_name, tool_result, history, tool_call_id=None, raw_arguments="{}"):
        if not self.available():
            return None
        messages = self._build_messages(user_message, history)
        messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": tool_call_id or "call_unknown", "type": "function", "function": {"name": tool_name, "arguments": raw_arguments}}]})
        messages.append({"role": "tool", "tool_call_id": tool_call_id or "call_unknown", "content": json.dumps(tool_result, ensure_ascii=False)})
        model = self._get_model(len(str(messages)))
        payload = {"model": model, "messages": messages, "stream": False, "max_tokens": 4096}
        return self._execute_with_retry(payload)

    def chat_with_full_history(self, messages, tools):
        if not self.available():
            return {"error": "IA indisponível."}
        formatted_tools = [{"type": "function", "function": {"name": n, "description": s.get("description", ""), "parameters": s.get("parameters", {})}} for n, s in tools.items()]
        full_messages = [{"role": "system", "content": self._get_system_prompt().strip()}] + messages
        total_len = sum(len(str(m.get('content', ''))) for m in full_messages)
        model = self._get_model(total_len)
        
        payload = {"model": model, "messages": full_messages, "tools": formatted_tools, "tool_choice": "auto", "stream": False, "max_tokens": 4096}
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
                except Exception:
                    args = {}
                return {"tool_call": {"id": tc.get("id"), "name": fd.get("name"), "arguments": args, "raw_arguments": fd.get("arguments", "{}")}}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return {"text": content.strip()}
            return {"text": "Comando processado."}
        except HTTPError as e:
            try: err_body = e.read().decode("utf-8")
            except Exception: err_body = str(e)
            logger.error(f"Erro full history (HTTP {e.code}): {err_body}")
            return {"error": f"HTTP {e.code}: {err_body[:150]}"}
        except URLError as e:
            logger.error(f"Erro de rede Groq: {e.reason}")
            return {"error": f"Falha de rede com a Groq. Tente novamente."}
        except Exception as e:
            logger.error(f"Erro full history: {e}")
            return {"error": str(e)}

    def _send_raw_request(self, payload):
        request = Request(f"{self.base_url}/chat/completions", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}", "User-Agent": "Mozilla/5.0"}, method="POST")
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def _execute_with_retry(self, payload):
        for attempt in range(3):
            try:
                result = self._send_raw_request(payload)
                choices = result.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                return None
            except HTTPError as error:
                if error.code == 429:
                    time.sleep(2) # Rate limit, aguarda e tenta de novo
                else:
                    try: logger.error(f"Erro HTTP {error.code}: {error.read().decode('utf-8')[:200]}")
                    except Exception: pass
                    break
            except Exception as error:
                logger.error(f"Falha conexão: {type(error).__name__} - {str(error)}")
                time.sleep(1)
        return None
