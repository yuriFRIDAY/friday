# core/friday_tools.py
import json
import logging
import os
import shutil
import time
import urllib.parse
import smtplib
import re
from email.mime.text import MIMEText
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

logger = logging.getLogger("friday.tools")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ========================================
# HANDLERS (implementações reais)
# ========================================

def _handler_get_current_time():
    return time.strftime("%d/%m/%Y %H:%M:%S")

def _handler_get_weather(city="São Paulo"):
    city_encoded = urllib.parse.quote(city)
    url = f"https://wttr.in/{city_encoded}?format=j1&lang=pt"
    try:
        req = Request(url, headers={"User-Agent": "FRIDAY/1.0"})
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        current = data["current_condition"][0]
        temp = current["temp_C"]
        desc = current["lang_pt"][0]["value"] if "lang_pt" in current and current["lang_pt"] else current["weatherDesc"][0]["value"]
        return f"A temperatura em {city} é de {temp}°C com {desc}."
    except Exception as e:
        return {"error": f"Não foi possível obter o clima: {str(e)}"}

def _handler_get_system_status():
    try:
        try:
            load1, load5, load15 = os.getloadavg()
            cpu_info = f"Carga da CPU (1min): {load1:.2f}"
        except OSError:
            cpu_info = "Carga da CPU: indisponível"
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
            mem_total = int(lines[0].split()[1])
            mem_free = int(lines[1].split()[1])
            mem_percent = round((mem_total - mem_free) / mem_total * 100, 1)
            mem_total_gb = round(mem_total / (1024**2), 2)
            mem_info = f"Memória RAM: {mem_percent}% em uso (Total: {mem_total_gb} GB)"
        except Exception:
            mem_info = "Memória RAM: indisponível"
        try:
            total, used, free = shutil.disk_usage("/")
            disk_percent = round((used / total) * 100, 1)
            disk_total_gb = round(total / (1024**3), 2)
            disk_info = f"Disco: {disk_percent}% em uso (Total: {disk_total_gb} GB)"
        except Exception:
            disk_info = "Disco: indisponível"
        return f"Status do Sistema:\n- {cpu_info}\n- {mem_info}\n- {disk_info}"
    except Exception as e:
        return {"error": f"Falha ao ler status do sistema: {str(e)}"}

def _handler_search_web(query, max_results=3):
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    if tavily_key:
        try:
            payload = json.dumps({"query": query, "search_depth": "basic", "max_results": max_results, "include_answer": False}).encode("utf-8")
            req = Request("https://api.tavily.com/search", data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {tavily_key}"}, method="POST")
            with urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
            results = [{"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("content", "")[:500]} for item in data.get("results", [])[:max_results]]
            if results:
                return {"query": query, "results": results, "source": "tavily"}
        except Exception:
            pass

    try:
        params = urllib.parse.urlencode({"q": query, "format": "json", "no_html": 1, "skip_disambig": 1})
        req = Request(f"https://api.duckduckgo.com/?{params}", headers={"User-Agent": "FRIDAY/1.0"})
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        abstract = data.get("AbstractText") or data.get("Answer")
        if abstract:
            return {"query": query, "results": [{"title": data.get("Heading", "Resultado"), "url": data.get("AbstractURL", ""), "snippet": abstract[:800]}], "source": "duckduckgo"}
        related = data.get("RelatedTopics", [])
        results = [{"title": "Tópico relacionado", "url": topic.get("FirstURL", ""), "snippet": topic["Text"][:300]} for topic in related[:max_results] if isinstance(topic, dict) and "Text" in topic]
        if results:
            return {"query": query, "results": results, "source": "duckduckgo"}
        return {"error": "Nenhum resultado encontrado."}
    except Exception as e:
        return {"error": f"Falha na pesquisa web: {str(e)}"}

def _handler_send_email(to_email, subject, body):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")

    if not all([smtp_server, smtp_user, smtp_pass]):
        return {"error": "Credenciais de e-mail não configuradas no sistema."}

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = to_email

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return f"E-mail enviado com sucesso para {to_email}."
    except Exception as e:
        return {"error": f"Falha ao enviar e-mail: {str(e)}"}

def _handler_save_memory(key, value):
    save_func = _MEMORY_CONTEXT.get("save")
    if not callable(save_func):
        return {"error": "Sistema de memória indisponível."}
    try:
        save_func(key, value)
        return f"Memória salva: {key} = {value}"
    except Exception as e:
        return {"error": f"Falha ao salvar memória: {str(e)}"}

def _handler_recall_memory(key):
    recall_func = _MEMORY_CONTEXT.get("recall")
    if not callable(recall_func):
        return {"error": "Sistema de memória indisponível."}
    try:
        value = recall_func(key)
        return value if value else {"error": f"Nenhuma memória encontrada para: {key}"}
    except Exception as e:
        return {"error": f"Falha ao recuperar memória: {str(e)}"}

def _handler_play_music(query):
    query_encoded = urllib.parse.quote(query)

    # TENTATIVA 1: API Piped (altamente confiável para achar vídeos)
    try:
        piped_url = f"https://pipedapi.kavin.rocks/search?q={query_encoded}&filter=videos"
        req = Request(piped_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        items = data.get("items", [])
        if items and "url" in items[0]:
            video_id = items[0]["url"].split("v=")[-1]
            embed_url = f"https://www.youtube.com/embed/{video_id}?autoplay=1"
            return {"ok": True, "action": "play_music", "url": embed_url, "query": query}
    except Exception as e:
        print(f"FRIDAY WEB: erro na API Piped: {e}")

    # TENTATIVA 2 (Fallback): Raspar o YouTube diretamente
    try:
        url = f"https://www.youtube.com/results?search_query={query_encoded}"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8")

        match = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
        if match:
            video_id = match.group(1)
            embed_url = f"https://www.youtube.com/embed/{video_id}?autoplay=1"
            return {"ok": True, "action": "play_music", "url": embed_url, "query": query}
    except Exception as e:
        print(f"FRIDAY WEB: erro ao raspar YouTube: {e}")

    return {"error": "Não consegui encontrar o vídeo no YouTube."}

def _handler_get_morning_briefing(city="Criciúma"):
    briefing = []

    clima = _handler_get_weather(city)
    if isinstance(clima, str):
        briefing.append(clima)
    elif isinstance(clima, dict) and "error" in clima:
        briefing.append("Não consegui obter a previsão do tempo agora.")

    agenda = _handler_get_calendar_events("today")
    if isinstance(agenda, dict) and "events" in agenda:
        evts = agenda["events"]
        if evts:
            briefing.append(f"Você tem {len(evts)} compromissos hoje. O primeiro é: {evts[0]['summary']} às {evts[0]['start'].split()[-1]}.")
        else:
            briefing.append("Sua agenda está livre hoje.")
    else:
        briefing.append("Não consegui acessar a sua agenda neste momento.")

    news = _handler_search_web("últimas notícias do mundo hoje", max_results=1)
    if isinstance(news, dict) and "results" in news and news["results"]:
        snippet = news["results"][0].get("snippet", "")
        if len(snippet) > 150:
            snippet = snippet[:147].rsplit(' ', 1)[0] + "..."
        briefing.append(f"Nas notícias: {snippet}")

    return " ".join(briefing)

def _handler_get_calendar_events(range_value="upcoming"):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    query = _CALENDAR_CONTEXT.get("events_query")
    today = _CALENDAR_CONTEXT.get("today_events")

    if not callable(ctx) or not callable(query):
        return {"error": "Integração de calendário indisponível."}

    try:
        access_token = ctx()
    except Exception as exc:
        return {"error": f"Falha ao obter token: {exc}"}
    if not access_token:
        return {"error": "Calendário não conectado."}

    if range_value == "today" and callable(today):
        events = today(access_token)
        label = "hoje"
    else:
        events = query(access_token, max_results=10)
        label = "próximos dias"

    if events is None:
        return {"error": "Não foi possível consultar o calendário."}

    formatted = []
    for evt in events:
        start_raw = evt.get("start", "")
        try:
            dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            start_label = dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            start_label = start_raw or "horário não definido"
        formatted.append({"summary": evt.get("summary", "(sem título)"), "start": start_label})

    return {"range": label, "events": formatted, "count": len(formatted)}

def _handler_create_calendar_event(summary, start_iso, end_iso, description="", location=""):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    create = _CALENDAR_CONTEXT.get("create_event")
    if not callable(ctx) or not callable(create):
        return {"error": "Integração indisponível."}
    try:
        access_token = ctx()
    except Exception as exc:
        return {"error": f"Falha no token: {exc}"}
    if not access_token:
        return {"error": "Calendário não conectado."}

    payload = {"summary": summary, "start": {"dateTime": start_iso, "timeZone": "America/Sao_Paulo"}, "end": {"dateTime": end_iso, "timeZone": "America/Sao_Paulo"}}
    if description: payload["description"] = description
    if location: payload["location"] = location

    result = create(access_token, payload)
    if result is None:
        return {"error": "O Google recusou a criação do evento."}

    return {"ok": True, "created": True, "summary": summary, "start": start_iso, "end": end_iso, "link": result.get("htmlLink", "")}


# ========================================
# CONTEXTOS INJETÁVEIS
# ========================================

_CALENDAR_CONTEXT = {}
_MEMORY_CONTEXT = {}

def set_calendar_context(access_token_provider, events_query, today_events, create_event):
    _CALENDAR_CONTEXT["access_token_provider"] = access_token_provider
    _CALENDAR_CONTEXT["events_query"] = events_query
    _CALENDAR_CONTEXT["today_events"] = today_events
    _CALENDAR_CONTEXT["create_event"] = create_event

def set_memory_context(save_func, recall_func):
    _MEMORY_CONTEXT["save"] = save_func
    _MEMORY_CONTEXT["recall"] = recall_func


# ========================================
# REGISTRO DE FERRAMENTAS
# ========================================

TOOL_REGISTRY = {
    "get_current_time": {
        "description": "Retorna a data e hora atuais do sistema.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "requires_confirmation": False, "allowed_roles": ["owner"], "handler": _handler_get_current_time,
    },
    "get_weather": {
        "description": "Consulta a previsão do tempo atual para uma cidade específica.",
        "parameters": {"type": "object", "properties": {"city": {"type": "string", "description": "Nome da cidade."}}, "required": ["city"], "additionalProperties": False},
        "requires_confirmation": False, "allowed_roles": ["owner"], "handler": _handler_get_weather,
    },
    "get_system_status": {
        "description": "Retorna o status de CPU, memória RAM e disco do servidor.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "requires_confirmation": False, "allowed_roles": ["owner"], "handler": _handler_get_system_status,
    },
    "search_web": {
        "description": "Pesquisa informações atualizadas na internet.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Termo a ser pesquisado."}}, "required": ["query"], "additionalProperties": False},
        "requires_confirmation": False, "allowed_roles": ["owner"], "handler": _handler_search_web,
    },
    "send_email": {
        "description": "Envia um e-mail em nome do usuário.",
        "parameters": {
            "type": "object",
            "properties": {
                "to_email": {"type": "string", "description": "Endereço de e-mail do destinatário."},
                "subject": {"type": "string", "description": "Assunto do e-mail."},
                "body": {"type": "string", "description": "Corpo do e-mail."}
            },
            "required": ["to_email", "subject", "body"], "additionalProperties": False,
        },
        "requires_confirmation": True, "allowed_roles": ["owner"], "handler": _handler_send_email,
    },
    "save_memory": {
        "description": "Salva uma informação importante de longo prazo sobre o usuário.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "A chave/assunto da memória."},
                "value": {"type": "string", "description": "O valor a ser salvo."}
            },
            "required": ["key", "value"], "additionalProperties": False,
        },
        "requires_confirmation": False, "allowed_roles": ["owner"], "handler": _handler_save_memory,
    },
    "recall_memory": {
        "description": "Recupera uma informação de longo prazo previamente salva.",
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "A chave da memória a ser recuperada."}},
            "required": ["key"], "additionalProperties": False,
        },
        "requires_confirmation": False, "allowed_roles": ["owner"], "handler": _handler_recall_memory,
    },
    "play_music": {
        "description": "Abre o YouTube para tocar uma música ou playlist.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Nome da música ou artista."}},
            "required": ["query"], "additionalProperties": False,
        },
        "requires_confirmation": False, "allowed_roles": ["owner"], "handler": _handler_play_music,
    },
    "get_morning_briefing": {
        "description": "Gera um resumo matinal completo com clima, agenda do dia e notícias.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "Cidade para o clima."}},
            "required": ["city"], "additionalProperties": False,
        },
        "requires_confirmation": False, "allowed_roles": ["owner"], "handler": _handler_get_morning_briefing,
    },
    "get_calendar_events": {
        "description": "Consulta os próximos compromissos ou os eventos de hoje na agenda.",
        "parameters": {"type": "object", "properties": {"range": {"type": "string", "enum": ["today", "upcoming"]}}, "required": ["range"], "additionalProperties": False},
        "requires_confirmation": False, "allowed_roles": ["owner"], "handler": _handler_get_calendar_events,
    },
    "create_calendar_event": {
        "description": "Cria um novo evento no Google Calendar. Requer confirmação.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Título do evento."},
                "start_iso": {"type": "string", "description": "Início em ISO 8601."},
                "end_iso": {"type": "string", "description": "Fim em ISO 8601."},
                "description": {"type": "string"}, "location": {"type": "string"},
            },
            "required": ["summary", "start_iso", "end_iso"], "additionalProperties": False,
        },
        "requires_confirmation": True, "allowed_roles": ["owner"], "handler": _handler_create_calendar_event,
    },
}

def public_tool_specs():
    return {name: {k: v for k, v in spec.items() if k != "handler"} for name, spec in TOOL_REGISTRY.items()}


# ========================================
# EXECUÇÃO CONTROLADA
# ========================================

def _validate_required(schema, arguments):
    required = schema.get("parameters", {}).get("required", [])
    for field in required:
        if field not in arguments:
            return f"Parâmetro obrigatório ausente: {field}"
    props = schema.get("parameters", {}).get("properties", {})
    for key in arguments.keys():
        if props and key not in props:
            return f"Parâmetro não permitido: {key}"
    return None

def execute_tool(tool_name, arguments, user_role="owner", session=None, confirmed=False):
    tool = TOOL_REGISTRY.get(tool_name)
    if not tool:
        return {"error": f"Ferramenta '{tool_name}' não autorizada."}
    if user_role not in tool.get("allowed_roles", ["owner"]):
        return {"error": "Permissão negada."}

    arguments = arguments or {}
    validation_error = _validate_required(tool, arguments)
    if validation_error:
        return {"error": validation_error}

    if tool.get("requires_confirmation") and not confirmed:
        return {"status": "pending_confirmation", "tool": tool_name, "arguments": arguments, "message": f"A ferramenta '{tool_name}' tem efeito colateral. Confirma a execução? (responda 'confirmar' ou 'cancelar')"}

    handler = tool.get("handler")
    if not callable(handler):
        return {"error": "Ferramenta indisponível."}

    try:
        result = handler(**arguments)
    except TypeError as exc:
        return {"error": f"Argumentos inválidos: {exc}"}
    except Exception as exc:
        return {"error": f"Falha ao executar ferramenta: {exc}"}

    return {"ok": True, "result": result}