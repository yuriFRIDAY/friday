import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

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

MAX_HISTORY_MESSAGES = 10 

class FridayAIProvider:
    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY", "").strip()
        self.base_url = "https://api.groq.com/openai/v1"
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
        return f"Hoje: {now.strftime('%d/%m/%Y')} - {now.strftime('%H:%M')} (Brasília)."

    def _get_system_prompt(self):
        return """
Você é a F.R.I.D.A.Y., IA pessoal do Chefe (inspirada no JARVIS).
Personalidade: Elegante, direta, leal e com humor sutil. Chame-o de "Chefe".
Seja concisa nas respostas. Não enrole.
Se uma ferramenta falhar, explique o motivo brevemente e ofereça uma alternativa.
Se o Chefe pedir para abrir o "Olho de Deus", use a ferramenta open_eye_of_god.
""".strip()

    def _trim_history(self, messages):
        if not messages:
            return messages
        if len(messages) <= MAX_HISTORY_MESSAGES:
            return messages
        tail = messages[-MAX_HISTORY_MESSAGES:]
        while tail and tail[0].get("role") == "tool":
            tail.pop(0)
        return tail

    def _inject_time_context(self, messages):
        if not messages:
            return messages
        msgs = list(messages)
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") == "user":
                content = msgs[i].get("content")
                if isinstance(content, str) and not content.startswith("[Hoje é"):
                    msgs[i] = {**msgs[i], "content": f"[{self._get_current_time_context()}]\n\n{content}"}
                return msgs
        return msgs

    def chat_with_full_history_stream(self, messages, tools):
        if not self.available():
            yield {"error": "IA indisponível: configure GROQ_API_KEY."}
            return
        if not messages:
            yield {"error": "A conversa está vazia."}
            return

        trimmed = self._trim_history(messages)
        last_msg = trimmed[-1].get("content", "")
        is_vision = isinstance(last_msg, list) and any(
            isinstance(item, dict) and item.get("type") == "image_url" for item in last_msg
        )

        if not is_vision:
            trimmed = self._inject_time_context(trimmed)

        formatted_tools = [
            {"type": "function", "function": {
                "name": name,
                "description": spec.get("description", ""),
                "parameters": spec.get("parameters", {}),
            }}
            for name, spec in (tools or {}).items()
        ]
        if is_vision:
            formatted_tools = []

        payload = {
            "model": self._get_model(is_vision),
            "messages": [{"role": "system", "content": self._get_system_prompt()}] + trimmed,
            "stream": True,
            "max_tokens": 2048, 
            "temperature": 0.6,
        }
        if formatted_tools:
            payload["tools"] = formatted_tools
            payload["tool_choice"] = "auto"

        body_bytes = json.dumps(payload).encode("utf-8")

        response = None
        last_err = None
        for attempt in range(3):
            req = Request(
                f"{self.base_url}/chat/completions",
                data=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "User-Agent": "FRIDAY/1.0",
                },
                method="POST",
            )
            try:
                response = urlopen(req, timeout=90)
                break
            except HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                logger.error(f"Groq HTTP {e.code}: {body[:200]}")
                last_err = f"Groq HTTP {e.code}: {body[:200]}"
                
                if e.code == 429:
                    retry_after = e.headers.get("retry-after")
                    wait_time = int(retry_after) if retry_after else (5 * (2 ** attempt))
                    logger.warning(f"Rate limit atingido. Aguardando {wait_time}s...")
                    time.sleep(wait_time)
                elif e.code < 500 or attempt == 2:
                    yield {"error": last_err}
                    return
                else:
                    time.sleep(2 ** attempt)
            except Exception as e:
                logger.exception(f"Erro conexão Groq")
                last_err = str(e)[:300]
                if attempt == 2:
                    yield {"error": f"Erro de conexão: {last_err}"}
                    return
                time.sleep(2 ** attempt)

        if response is None:
            yield {"error": last_err or "Falha desconhecida."}
            return

        try:
            with response:
                for line in response:
                    if not line: break
                    line = line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"): continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]": break
                    try:
                        chunk_data = json.loads(data_str)
                    except json.JSONDecodeError: continue
                    choices = chunk_data.get("choices") or []
                    if not choices: continue
                    delta = choices[0].get("delta") or {}
                    if delta.get("content"):
                        yield {"text": delta["content"]}
                    for tool_call in delta.get("tool_calls") or []:
                        yield {"tool_call_chunk": tool_call}
        except Exception as error:
            logger.exception("Erro no streaming da FRIDAY")
            yield {"error": f"Erro no streaming: {str(error)[:300]}"}
