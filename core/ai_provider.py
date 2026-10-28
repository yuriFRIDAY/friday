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
        return "openai/gpt-oss-120b"

    def _get_current_time_context(self):
        try:
            if HAS_ZONEINFO:
                now = datetime.now(ZoneInfo("America/Sao_Paulo"))
            else:
                now = datetime.now(timezone(timedelta(hours=-3)))
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
        prompt = f"Resuma a conversa a seguir em um parágrafo curto e direto:\n\n{text_to_summarize}"
        
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
Responda SEMPRE em português brasileiro natural e fluido.

REGRAS DE COMPORTAMENTO (MUITO IMPORTANTE):
1. NUNCA peça permissão para usar ferramentas ou executar ações (ex: criar eventos, enviar e-mails). Execute-as imediatamente e apenas informe o que foi feito.
2. SEMPRE comece suas respostas resumindo o que o Chefe pediu. Exemplo: "Entendido, chefe. Você pediu para agendar [X] no dia [Y]..."
3. SEMPRE finalize CADA resposta oferecendo ajuda adicional proativamente. Exemplo: "Posso ajudar com mais alguma coisa?", "Deseja que eu verifique sua agenda para amanhã?", "Precisa de mais algum resumo?".
4. NUNCA traduza expressões do inglês de forma literal (ex: nunca use "assisti-lo" no sentido de ajudar).

REGRAS DE FERRAMENTAS:
- Se o chefe pedir para criar uma TAREFA, use `create_task`.
- Se pedir para marcar algo na AGENDA/CALENDÁRIO, use `create_calendar_event`.
- Se pedir para ver o calendário, use `get_calendar_events`.

CONTEXTO DE TEMPO:
{time_context}

CONTEXTO DA CONVERSA:
{history_context}

CONCISÃO:
Seja direto, mas cumpra as regras de resumo e oferta de ajuda.

FERRAMENTAS DISPONÍVEIS:
1. get_current_time, get_weather, get_system_status, get_crypto_price, get_news, search_web, deep_search, read_webpage
2. send_email, reply_to_email, read_emails, read_email_full, get_email_thread
3. create_task, list_tasks, complete_task
4. save_memory, recall_memory, delete_memory
5. play_music, get_morning_briefing
6. get_calendar_events, create_calendar_event, update_calendar_event, delete_calendar_event, find_free_slots

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
                    time.sleep(2)
                else:
                    break
            except Exception:
                time.sleep(1)
        return None

    def chat_with_full_history_stream(self, messages, tools):
        if not self.available(): 
            yield {"error": "IA indisponível. Configure a GROQ_API_KEY."}
            return

        formatted_tools = [{"type": "function", "function": {"name": n, "description": s.get("description", ""), "parameters": s.get("parameters", {})}} for n, s in tools.items()]
        full_messages = [{"role": "system", "content": self._get_system_prompt().strip()}] + messages
        model = self._get_model(4000)
        
        payload = {"model": model, "messages": full_messages, "tools": formatted_tools, "tool_choice": "auto", "stream": True, "max_tokens": 4096}
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        req = Request(f"{self.base_url}/chat/completions", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        
        try:
            with urlopen(req, timeout=60) as response:
                while True:
                    line = response.readline()
                    if not line:
                        break
                    line = line.decode("utf-8").strip()
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk_data = json.loads(data_str)
                            choices = chunk_data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                if "tool_calls" in delta:
                                    yield {"tool_call_chunk": delta["tool_calls"][0]}
                                elif "content" in delta and delta["content"]:
                                    yield {"text": delta["content"]}
                        except json.JSONDecodeError:
                            pass
        except HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            yield {"error": f"Groq HTTP {e.code}: {err_body[:200]}"}
        except Exception as e:
            yield {"error": f"Erro no streaming: {str(e)}"}

    def _send_raw_request(self, payload):
        request = Request(f"{self.base_url}/chat/completions", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}", "User-Agent": "Mozilla/5.0"}, method="POST")
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
