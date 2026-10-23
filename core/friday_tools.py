# core/friday_tools.py
import json
import logging
import os
import shutil
import time
import urllib.parse
import smtplib
import re
from html.parser import HTMLParser
from email.mime.text import MIMEText
from email.utils import parseaddr
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

try:
    from zoneinfo import ZoneInfo
    HAS_ZONEINFO = True
except ImportError:
    HAS_ZONEINFO = False

logger = logging.getLogger("friday.tools")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# ========================================
# UTILITÁRIOS
# ========================================
def _get_tz():
    if HAS_ZONEINFO:
        return ZoneInfo("America/Sao_Paulo")
    return None

class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
        self.skip = False
        self.ignore_tags = {'script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript'}
    def handle_starttag(self, tag, attrs):
        if tag in self.ignore_tags:
            self.skip = True
    def handle_endtag(self, tag):
        if tag in self.ignore_tags:
            self.skip = False
    def handle_data(self, data):
        if not self.skip:
            text = data.strip()
            if text:
                self.result.append(text)
    def get_text(self):
        return " ".join(self.result)

def _truncate_text(text, max_len=5000):
    if len(text) <= max_len:
        return text
    # Corta na última palavra inteira antes do limite
    truncated = text[:max_len]
    last_space = truncated.rfind(' ')
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "..."

def _format_iso_to_br(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if HAS_ZONEINFO:
            dt = dt.astimezone(_get_tz())
        return dt.strftime("%d/%m/%Y às %H:%M")
    except Exception:
        return iso_str

# ========================================
# HANDLERS
# ========================================

def _handler_get_current_time():
    tz = _get_tz()
    now = datetime.now(tz) if tz else datetime.now()
    dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
    return now.strftime(f"Hoje é {dias[now.weekday()]}, %d/%m/%Y. Agora são %H:%M (Horário de Brasília).")

def _handler_get_weather(city="São Paulo"):
    city_encoded = urllib.parse.quote(city)
    url = f"https://wttr.in/{city_encoded}?format=j1&lang=pt"
    try:
        req = Request(url, headers={"User-Agent": "FRIDAY/1.0"})
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        current = data["current_condition"][0]
        temp = current["temp_C"]
        feels_like = current.get("FeelsLikeC", temp)
        humidity = current.get("humidity", "N/A")
        desc = current["lang_pt"][0]["value"] if "lang_pt" in current and current["lang_pt"] else current["weatherDesc"][0]["value"]
        
        return f"Em {city}: {desc}, {temp}°C (sensação de {feels_like}°C). Umidade: {humidity}%."
    except Exception as e:
        return {"error": f"Não foi possível obter o clima: {str(e)}"}

def _handler_get_system_status():
    try:
        # CPU
        try:
            load1, load5, load15 = os.getloadavg()
            cpu_info = f"CPU (carga 1m/5m/15m): {load1:.2f} / {load5:.2f} / {load15:.2f}"
        except OSError:
            cpu_info = "CPU: indisponível"
        
        # RAM
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
            mem_total = int(lines[0].split()[1])
            mem_free = int(lines[1].split()[1])
            mem_percent = round((mem_total - mem_free) / mem_total * 100, 1)
            mem_total_gb = round(mem_total / (1024**2), 2)
            mem_info = f"RAM: {mem_percent}% em uso (Total: {mem_total_gb} GB)"
        except Exception:
            mem_info = "RAM: indisponível"
        
        # Disco
        try:
            total, used, free = shutil.disk_usage("/")
            disk_percent = round((used / total) * 100, 1)
            disk_total_gb = round(total / (1024**3), 2)
            disk_info = f"Disco: {disk_percent}% em uso (Total: {disk_total_gb} GB)"
        except Exception:
            disk_info = "Disco: indisponível"
            
        return f"Status do Servidor:\n- {cpu_info}\n- {mem_info}\n- {disk_info}"
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
            results = [{"title": item.get("title", ""), "url": item.get("url", ""), "snippet": _truncate_text(item.get("content", ""), 500)} for item in data.get("results", [])[:max_results]]
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
            return {"query": query, "results": [{"title": data.get("Heading", "Resultado"), "url": data.get("AbstractURL", ""), "snippet": _truncate_text(abstract, 800)}], "source": "duckduckgo"}
        
        related = data.get("RelatedTopics", [])
        results = [{"title": "Tópico relacionado", "url": topic.get("FirstURL", ""), "snippet": _truncate_text(topic["Text"], 300)} for topic in related[:max_results] if isinstance(topic, dict) and "Text" in topic]
        if results:
            return {"query": query, "results": results, "source": "duckduckgo"}
        return {"error": "Nenhum resultado encontrado."}
    except Exception as e:
        return {"error": f"Falha na pesquisa web: {str(e)}"}

def _handler_read_webpage(url):
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8", errors="replace")
        parser = _HTMLTextExtractor()
        parser.feed(html)
        text = parser.get_text()
        return f"Conteúdo de {url}:\n\n{_truncate_text(text, 6000)}"
    except Exception as e:
        return {"error": f"Falha ao ler a página: {str(e)}"}

def _handler_send_email(to_email, subject, body):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")

    if not all([smtp_server, smtp_user, smtp_pass]):
        return {"error": "Credenciais de e-mail não configuradas."}

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = to_email

    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return f"E-mail enviado com sucesso para {to_email}."
    except Exception as e:
        return {"error": f"Falha ao enviar e-mail: {str(e)}"}

def _handler_reply_to_email(to_email, subject, in_reply_to="", body=None, original_body=""):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")

    if not all([smtp_server, smtp_user, smtp_pass]):
        return {"error": "Credenciais de e-mail não configuradas."}
        
    if not body:
        body = f"Olá,\n\nRespondendo sobre: {original_body[:200]}...\n\nAtenciosamente,\nChefe"

    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = to_email
    
    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
        msg['References'] = in_reply_to

    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return f"Resposta enviada com sucesso para {to_email}."
    except Exception as e:
        return {"error": f"Falha ao enviar resposta: {str(e)}"}

def _handler_read_emails(query="", max_results=5):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    if not callable(ctx):
        return {"error": "Integração com o Google indisponível."}
    
    try:
        access_token = ctx()
    except Exception as exc:
        return {"error": f"Falha ao obter token: {exc}"}
    
    if not access_token:
        return {"error": "Google não conectado."}

    params = {"maxResults": str(max_results), "labelIds": "INBOX"}
    if query:
        params["q"] = query

    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages?" + urllib.parse.urlencode(params)
    req = Request(url, headers={"Authorization": "Bearer " + access_token})
    
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 401:
            return {"error": "Token expirado. Reconecte o Google Calendar."}
        return {"error": f"Falha ao listar e-mails: {e}"}
    except Exception as e:
        return {"error": f"Falha na conexão com Gmail: {e}"}

    messages = data.get("messages", [])
    if not messages:
        return {"ok": True, "emails": [], "message": "Nenhum e-mail encontrado."}

    emails = []
    for msg in messages[:max_results]:
        msg_id = msg["id"]
        detail_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date&metadataHeaders=Message-ID"
        req_detail = Request(detail_url, headers={"Authorization": "Bearer " + access_token})
        try:
            with urlopen(req_detail, timeout=10) as resp:
                detail = json.loads(resp.read().decode("utf-8"))
                headers = detail.get("payload", {}).get("headers", [])
                subject = next((h["value"] for h in headers if h["name"] == "Subject"), "(sem assunto)")
                raw_from = next((h["value"] for h in headers if h["name"] == "From"), "")
                # Separa Nome <email@domain.com> para melhor visualização
                sender_name, sender_email = parseaddr(raw_from)
                sender = f"{sender_name} ({sender_email})" if sender_name else sender_email
                if not sender:
                    sender = "(remetente desconhecido)"
                    
                date = next((h["value"] for h in headers if h["name"] == "Date"), "")
                message_id = next((h["value"] for h in headers if h["name"] == "Message-ID"), "")
                snippet = _truncate_text(detail.get("snippet", ""), 300)
                thread_id = detail.get("threadId", "")
                
                emails.append({
                    "subject": subject, "from": sender, "date": date,
                    "snippet": snippet, "message_id": message_id, "thread_id": thread_id
                })
        except Exception:
            pass
    return {"ok": True, "emails": emails}

def _handler_get_email_thread(thread_id):
    if not thread_id or not isinstance(thread_id, str):
        return {"error": "ID da conversa inválido."}

    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    if not callable(ctx):
        return {"error": "Integração com o Google indisponível."}
    try:
        access_token = ctx()
    except Exception:
        return {"error": "Falha ao obter token."}
        
    if not access_token:
        return {"error": "Google não conectado."}

    url = f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date"
    req = Request(url, headers={"Authorization": "Bearer " + access_token})
    
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": f"Falha ao ler thread: {str(e)}"}

    messages = data.get("messages", [])
    thread_content = []
    for msg in messages:
        headers = msg.get("payload", {}).get("headers", [])
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "(sem assunto)")
        raw_from = next((h["value"] for h in headers if h["name"] == "From"), "")
        sender_name, sender_email = parseaddr(raw_from)
        sender = sender_name or sender_email or "Desconhecido"
        date = next((h["value"] for h in headers if h["name"] == "Date"), "")
        snippet = msg.get("snippet", "")
        thread_content.append(f"Data: {date}\nDe: {sender}\nAssunto: {subject}\nResumo: {snippet}\n")
        
    return {"thread_id": thread_id, "total_messages": len(messages), "history": "\n---\n".join(thread_content)}

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
    except Exception:
        pass

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
    except Exception:
        pass

    return {"error": "Não consegui encontrar o vídeo no YouTube."}

def _handler_get_morning_briefing(city="Criciúma"):
    briefing = []
    
    clima = _handler_get_weather(city)
    if isinstance(clima, str):
        briefing.append(clima)
    else:
        briefing.append("Clima indisponível.")
        
    agenda = _handler_get_calendar_events("today")
    if isinstance(agenda, dict) and agenda.get("events"):
        evts = agenda["events"]
        briefing.append(f"Agenda: {len(evts)} compromissos. O primeiro é '{evts[0]['summary']}' às {evts[0]['start'].split(' às ')[-1]}.")
    else:
        briefing.append("Sua agenda está livre hoje.")
            
    news = _handler_search_web("últimas notícias do mundo hoje", max_results=1)
    if isinstance(news, dict) and news.get("results"):
        snippet = _truncate_text(news["results"][0].get("snippet", ""), 200)
        briefing.append(f"Notícias: {snippet}")

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
        label = "nos próximos dias"

    if events is None:
        return {"error": "Não foi possível consultar o calendário."}

    formatted = []
    for evt in events:
        start_raw = evt.get("start", "")
        formatted.append({
            "id": evt.get("id", ""),
            "summary": evt.get("summary", "(sem título)"),
            "start": _format_iso_to_br(start_raw)
        })

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

    payload = {
        "summary": summary, 
        "start": {"dateTime": start_iso, "timeZone": "America/Sao_Paulo"}, 
        "end": {"dateTime": end_iso, "timeZone": "America/Sao_Paulo"}
    }
    if description: payload["description"] = description
    if location: payload["location"] = location

    result = create(access_token, payload)
    if result is None:
        return {"error": "O Google recusou a criação do evento."}
    return {"ok": True, "created": True, "summary": summary, "start": start_iso, "end": end_iso, "link": result.get("htmlLink", "")}

def _handler_delete_calendar_event(event_id):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    delete = _CALENDAR_CONTEXT.get("delete_event")
    if not callable(ctx) or not callable(delete):
        return {"error": "Integração indisponível."}
    try:
        access_token = ctx()
    except Exception as exc:
        return {"error": f"Falha no token: {exc}"}
    if not access_token:
        return {"error": "Calendário não conectado."}

    result = delete(access_token, event_id)
    if result is None:
        return {"error": "O Google recusou a exclusão do evento (verifique o ID)."}
    return {"ok": True, "deleted": True, "message": "Evento excluído com sucesso."}

def _handler_update_calendar_event(event_id, summary=None, start_iso=None, end_iso=None, description=None):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    update = _CALENDAR_CONTEXT.get("update_event")
    if not callable(ctx) or not callable(update):
        return {"error": "Integração indisponível."}
    try:
        access_token = ctx()
    except Exception as exc:
        return {"error": f"Falha no token: {exc}"}
    if not access_token:
        return {"error": "Calendário não conectado."}

    payload = {}
    if summary: payload["summary"] = summary
    if start_iso: payload["start"] = {"dateTime": start_iso, "timeZone": "America/Sao_Paulo"}
    if end_iso: payload["end"] = {"dateTime": end_iso, "timeZone": "America/Sao_Paulo"}
    if description: payload["description"] = description

    result = update(access_token, event_id, payload)
    if result is None:
        return {"error": "O Google recusou a atualização do evento."}
    return {"ok": True, "updated": True, "summary": summary, "start": start_iso, "end": end_iso}


# ========================================
# CONTEXTOS E REGISTRO
# ========================================
_CALENDAR_CONTEXT = {}
_MEMORY_CONTEXT = {}

def set_calendar_context(access_token_provider, events_query, today_events, create_event, delete_event, update_event):
    _CALENDAR_CONTEXT["access_token_provider"] = access_token_provider
    _CALENDAR_CONTEXT["events_query"] = events_query
    _CALENDAR_CONTEXT["today_events"] = today_events
    _CALENDAR_CONTEXT["create_event"] = create_event
    _CALENDAR_CONTEXT["delete_event"] = delete_event
    _CALENDAR_CONTEXT["update_event"] = update_event

def set_memory_context(save_func, recall_func):
    _MEMORY_CONTEXT["save"] = save_func
    _MEMORY_CONTEXT["recall"] = recall_func

TOOL_REGISTRY = {
    "get_current_time": {"description": "Retorna data e hora.", "parameters": {"type": "object", "properties": {}}, "requires_confirmation": False, "handler": _handler_get_current_time},
    "get_weather": {"description": "Consulta o clima.", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}, "requires_confirmation": False, "handler": _handler_get_weather},
    "get_system_status": {"description": "Status da CPU/RAM.", "parameters": {"type": "object", "properties": {}}, "requires_confirmation": False, "handler": _handler_get_system_status},
    "search_web": {"description": "Pesquisa na web.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, "requires_confirmation": False, "handler": _handler_search_web},
    "read_webpage": {"description": "Lê texto de uma URL.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}, "requires_confirmation": False, "handler": _handler_read_webpage},
    "send_email": {"description": "Envia e-mail.", "parameters": {"type": "object", "properties": {"to_email": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to_email", "subject", "body"]}, "requires_confirmation": True, "handler": _handler_send_email},
    "reply_to_email": {"description": "Responde e-mail mantendo thread.", "parameters": {"type": "object", "properties": {"to_email": {"type": "string"}, "subject": {"type": "string"}, "in_reply_to": {"type": "string"}, "body": {"type": "string"}, "original_body": {"type": "string"}}, "required": ["to_email", "subject"]}, "requires_confirmation": True, "handler": _handler_reply_to_email},
    "read_emails": {"description": "Lê e-mails recentes.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}}, "requires_confirmation": False, "handler": _handler_read_emails},
    "get_email_thread": {"description": "Lê toda a conversa de um e-mail (thread).", "parameters": {"type": "object", "properties": {"thread_id": {"type": "string"}}, "required": ["thread_id"]}, "requires_confirmation": False, "handler": _handler_get_email_thread},
    "save_memory": {"description": "Salva memória.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}, "requires_confirmation": False, "handler": _handler_save_memory},
    "recall_memory": {"description": "Recupera memória.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}, "requires_confirmation": False, "handler": _handler_recall_memory},
    "play_music": {"description": "Toca música.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, "requires_confirmation": False, "handler": _handler_play_music},
    "get_morning_briefing": {"description": "Gera briefing matinal.", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}, "requires_confirmation": False, "handler": _handler_get_morning_briefing},
    "get_calendar_events": {"description": "Consulta agenda.", "parameters": {"type": "object", "properties": {"range": {"type": "string", "enum": ["today", "upcoming"]}}, "required": ["range"]}, "requires_confirmation": False, "handler": _handler_get_calendar_events},
    "create_calendar_event": {"description": "Cria evento.", "parameters": {"type": "object", "properties": {"summary": {"type": "string"}, "start_iso": {"type": "string"}, "end_iso": {"type": "string"}, "description": {"type": "string"}, "location": {"type": "string"}}, "required": ["summary", "start_iso", "end_iso"]}, "requires_confirmation": True, "handler": _handler_create_calendar_event},
    "delete_calendar_event": {"description": "Exclui evento.", "parameters": {"type": "object", "properties": {"event_id": {"type": "string"}}, "required": ["event_id"]}, "requires_confirmation": True, "handler": _handler_delete_calendar_event},
    "update_calendar_event": {"description": "Atualiza evento.", "parameters": {"type": "object", "properties": {"event_id": {"type": "string"}, "summary": {"type": "string"}, "start_iso": {"type": "string"}, "end_iso": {"type": "string"}, "description": {"type": "string"}}, "required": ["event_id"]}, "requires_confirmation": True, "handler": _handler_update_calendar_event},
}

def public_tool_specs():
    return {name: {k: v for k, v in spec.items() if k != "handler"} for name, spec in TOOL_REGISTRY.items()}

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
        return {"status": "pending_confirmation", "tool": tool_name, "arguments": arguments, "message": f"A ferramenta '{tool_name}' tem efeito colateral. Confirma? (responda 'confirmar' ou 'cancelar')"}

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
