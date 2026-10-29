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

    def _get_model(self, is_vision=False):
        if is_vision:
            return "llama-3.2-11b-vision-preview"
        return "openai/gpt-oss-120b"

    def _get_current_time_context(self):
        try:
            if HAS_ZONEINFO: now = datetime.now(ZoneInfo("America/Sao_Paulo"))
            else: now = datetime.now(timezone(timedelta(hours=-3)))
        except Exception: now = datetime.now(timezone(timedelta(hours=-3)))
        dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
        return f"Hoje é {dias[now.weekday()]}, {now.strftime('%d/%m/%Y')} e agora são {now.strftime('%H:%M')} (Horário de Brasília)."

        def _get_system_prompt(self, history_context=""):
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
5. Se o chefe enviar uma imagem, analise-a detalhadamente e responda sobre o conteúdo dela.
6. Se a ferramenta retornar um erro, avise o chefe sobre o erro de forma clara e NUNCA minta dizendo que a ação foi concluída com sucesso.

CONTEXTO DE TEMPO:
{time_context}

CONTEXTO DA CONVERSA:
{history_context}

FERRAMENTAS DISPONÍVEIS:
1. get_current_time, get_weather, get_system_status, get_crypto_price, get_news, search_web, deep_search, read_webpage
2. send_email, reply_to_email, read_emails, read_email_full, get_email_thread
3. create_task, list_tasks, complete_task
4. save_memory, recall_memory, delete_memory
5. play_music, get_morning_briefing
6. get_calendar_events, create_calendar_event, update_calendar_event, delete_calendar_event, find_free_slots
"""

    def _summarize_history(self, history):
        if len(history) <= 8: return history
        return history[-8:]

    def _build_messages(self, message, history=None):
        summary_context = ""
        if history:
            history = self._summarize_history(history)

        messages = [{"role": "system", "content": self._get_system_prompt(summary_context).strip()}]
        
        if history:
            for item in history[-8:]:
                if isinstance(item, dict) and item.get("role") in ("user", "assistant"):
                    if isinstance(item.get("content"), str):
                        messages.append({"role": item["role"], "content": item["content"][:4000]})
        
        messages.append({"role": "user", "content": message})
        return messages

    def chat_with_full_history_stream(self, messages, tools):
        if not self.available(): 
            yield {"error": "IA indisponível."}
            return

        # Detecta se há imagem (payload de visão)
        is_vision = False
        last_msg = messages[-1].get("content", "")
        if isinstance(last_msg, list) and any(m.get("type") == "image_url" for m in last_msg):
            is_vision = True

        formatted_tools = [{"type": "function", "function": {"name": n, "description": s.get("description", ""), "parameters": s.get("parameters", {})}} for n, s in tools.items()]
        
        # Remove ferramentas se for visão (modelos de visão da Groq não suportam tools ainda em stream)
        # Se quiser manter ferramentas com visão, use um modelo misto.
        if is_vision:
            formatted_tools = [] 

        full_messages = [{"role": "system", "content": self._get_system_prompt().strip()}] + messages
        model = self._get_model(is_vision)
        
        payload = {"model": model, "messages": full_messages, "stream": True, "max_tokens": 4096}
        if formatted_tools:
            payload["tools"] = formatted_tools
            payload["tool_choice"] = "auto"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "Mozilla/5.0"
        }
        req = Request(f"{self.base_url}/chat/completions", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        
        try:
            with urlopen(req, timeout=60) as response:
                while True:
                    line = response.readline()
                    if not line: break
                    line = line.decode("utf-8").strip()
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]": break
                        try:
                            chunk_data = json.loads(data_str)
                            choices = chunk_data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                if "tool_calls" in delta:
                                    yield {"tool_call_chunk": delta["tool_calls"][0]}
                                elif "content" in delta and delta["content"]:
                                    yield {"text": delta["content"]}
                        except json.JSONDecodeError: pass
        except HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            yield {"error": f"Groq HTTP {e.code}: {err_body[:200]}"}
        except Exception as e:
            yield {"error": f"Erro no streaming: {str(e)}"}
