import json
import logging
import os
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError

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
        self.api_key = os.getenv("GROQ_API_KEY", "").strip()
        self.base_url = "https://api.groq.com/openai/v1"
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.data_dir = os.getenv("DATA_DIR", ".")
        if os.path.exists("/data") and os.access("/data", os.W_OK):
            self.data_dir = "/data"
        self.db_path = os.path.join(self.data_dir, "friday.db")

    def available(self):
        return bool(self.api_key)

    def _get_model(self, is_vision=False):
        if is_vision:
            return "llama-3.2-11b-vision-preview"
        return "openai/gpt-oss-120b"

    def _get_current_time_context(self):
        try:
            now = datetime.now(ZoneInfo("America/Sao_Paulo")) if HAS_ZONEINFO else datetime.now(timezone(timedelta(hours=-3)))
        except Exception:
            now = datetime.now(timezone(timedelta(hours=-3)))
        dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
        return f"Hoje é {dias[now.weekday()]}, {now.strftime('%d/%m/%Y')} e agora são {now.strftime('%H:%M')} (Horário de Brasília)."

    def _get_system_prompt(self, history_context=""):
        return f"""
Você é FRIDAY, a IA pessoal do Chefe, inspirada no JARVIS.
Personalidade: formal, leal, proativa, precisa e levemente espirituosa.
Trate o usuário por "chefe" ou "senhor".
Responda SEMPRE em português brasileiro natural e fluido.

REGRAS:
1. Execute ferramentas quando forem apropriadas e autorizadas.
2. Comece resumindo o que o Chefe pediu.
3. Finalize oferecendo ajuda adicional de forma breve.
4. Não traduza expressões do inglês literalmente.
5. Analise imagens enviadas pelo Chefe.
6. Informe erros de ferramentas com clareza; nunca afirme sucesso se houve falha.

CONTEXTO DE TEMPO:
{self._get_current_time_context()}

CONTEXTO DA CONVERSA:
{history_context}
"""

    def _summarize_history(self, history):
        return history if len(history) <= 8 else history[-8:]

    def _build_messages(self, message, history=None):
        messages = [{"role": "system", "content": self._get_system_prompt().strip()}]
        for item in self._summarize_history(history or []):
            if isinstance(item, dict) and item.get("role") in ("user", "assistant") and isinstance(item.get("content"), str):
                messages.append({"role": item["role"], "content": item["content"][:4000]})
        messages.append({"role": "user", "content": message})
        return messages

    def chat_with_full_history_stream(self, messages, tools):
        if not self.available():
            yield {"error": "IA indisponível: configure GROQ_API_KEY."}
            return
        if not messages:
            yield {"error": "A conversa está vazia."}
            return

        last_msg = messages[-1].get("content", "")
        is_vision = isinstance(last_msg, list) and any(
            isinstance(item, dict) and item.get("type") == "image_url" for item in last_msg
        )
        formatted_tools = [
            {"type": "function", "function": {
                "name": name,
                "description": spec.get("description", ""),
                "parameters": spec.get("parameters", {})
            }}
            for name, spec in (tools or {}).items()
        ]
        if is_vision:
            formatted_tools = []

        payload = {
            "model": self._get_model(is_vision),
            "messages": [{"role": "system", "content": self._get_system_prompt().strip()}] + messages,
            "stream": True,
            "max_tokens": 4096
        }
        if formatted_tools:
            payload["tools"] = formatted_tools
            payload["tool_choice"] = "auto"

        req = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}", "User-Agent": "FRIDAY/1.0"},
            method="POST"
        )

        try:
            with urlopen(req, timeout=90) as response:
                while True:
                    line = response.readline()
                    if not line:
                        break
                    line = line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk_data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk_data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    if delta.get("content"):
                        yield {"text": delta["content"]}
                    for tool_call in delta.get("tool_calls") or []:
                        yield {"tool_call_chunk": tool_call}
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            logger.exception("Groq HTTP error %s", error.code)
            yield {"error": f"Groq HTTP {error.code}: {body[:500]}"}
        except Exception as error:
            logger.exception("Erro no streaming da FRIDAY")
            yield {"error": f"Erro no streaming: {str(error)[:300]}"}
