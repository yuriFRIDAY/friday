# core/friday_tools.py
import json
import logging
import os
import shutil
import time
import urllib.parse
import smtplib
import re
import base64
import threading
import sqlite3
import xml.etree.ElementTree as ET
import io
from html.parser import HTMLParser
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.utils import parseaddr
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

try:
    from zoneinfo import ZoneInfo
    HAS_ZONEINFO = True
except ImportError:
    HAS_ZONEINFO = False

try:
    import dateparser
    HAS_DATEPARSER = True
except ImportError:
    HAS_DATEPARSER = False

try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False

logger = logging.getLogger("friday.tools")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

DATA_DIR = os.getenv("DATA_DIR", ".")
if os.path.exists("/data") and os.access("/data", os.W_OK): 
    DATA_DIR = "/data"
DB_PATH = os.path.join(DATA_DIR, "friday.db")

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ========================================
# CONTEXTOS
# ========================================
_CALENDAR_CONTEXT = {}
_MEMORY_CONTEXT = {}

def set_calendar_context(access_token_provider, events_query, today_events, create_event, delete_event, update_event):
    _CALENDAR_CONTEXT.update({
        "access_token_provider": access_token_provider, 
        "events_query": events_query, 
        "today_events": today_events, 
        "create_event": create_event, 
        "delete_event": delete_event, 
        "update_event": update_event
    })

def set_memory_context(save_func, recall_func, delete_func=None):
    _MEMORY_CONTEXT.update({"save": save_func, "recall": recall_func, "delete": delete_func})

# ========================================
# CONEXÃO DB LOCAL PARA AS TAREFAS
# ========================================
def _get_db():
    if DATABASE_URL and HAS_PSYCOPG2:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect(DB_PATH)

# ========================================
# CACHE SEGURO PARA MULTI-THREAD
# ========================================
_CACHE = {}
_CACHE_LOCK = threading.Lock()

def _get_cache(key):
    with _CACHE_LOCK:
        if key in _CACHE:
            data, expires = _CACHE[key]
            if time.time() < expires: 
                return data
            del _CACHE[key]
    return None

def _set_cache(key, data, ttl_seconds=300):
    with _CACHE_LOCK:
        _CACHE[key] = (data, time.time() + ttl_seconds)

# ========================================
# UTILITÁRIOS
# ========================================
def _get_tz():
    if HAS_ZONEINFO:
        return ZoneInfo("America/Sao_Paulo")
    return timezone(timedelta(hours=-3))

def _parse_iso(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None: 
            dt = dt.replace(tzinfo=_get_tz())
        return dt
    except Exception: 
        return None

def _parse_natural_date(text):
    if not HAS_DATEPARSER: 
        return None
    try:
        now = datetime.now(_get_tz())
        return dateparser.parse(
            text, languages=['pt', 'en'], 
            settings={
                'TIMEZONE': 'America/Sao_Paulo', 
                'RETURN_AS_TIMEZONE_AWARE': True, 
                'PREFER_DATES_FROM': 'future', 
                'RELATIVE_BASE': now
            }
        )
    except Exception: 
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
    if not text: 
        return ""
    text = str(text)
    if len(text) <= max_len: 
        return text
    truncated = text[:max_len]
    last_space = truncated.rfind(' ')
    if last_space > 0: 
        truncated = truncated[:last_space]
    return truncated + "..."

def _format_iso_to_br(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(_get_tz())
        return dt.strftime("%d/%m/%Y às %H:%M")
    except Exception: 
        return iso_str

def _extract_email_body(payload):
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        body_data = payload.get("body", {}).get("data", "")
        if body_data:
            try:
                padded = body_data + "=" * (-len(body_data) % 4)
                return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
            except Exception: 
                return ""
    elif mime == "text/html":
        body_data = payload.get("body", {}).get("data", "")
        if body_data:
            try:
                padded = body_data + "=" * (-len(body_data) % 4)
                html = base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
                parser = _HTMLTextExtractor()
                parser.feed(html)
                return parser.get_text()
            except Exception: 
                return ""
    parts = payload.get("parts", [])
    return "\n".join(t for t in (_extract_email_body(p) for p in parts) if t)

# ========================================
# HANDLERS: SISTEMA E WEB
# ========================================
def _handler_get_current_time():
    now = datetime.now(_get_tz())
    dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
    return now.strftime(f"Hoje é {dias[now.weekday()]}, %d/%m/%Y. Agora são %H:%M (Horário de Brasília).")

def _handler_get_system_status():
    try:
        try:
            load1, load5, load15 = os.getloadavg()
            cpu_info = f"CPU (carga 1m/5m/15m): {load1:.2f} / {load5:.2f} / {load15:.2f}"
        except OSError: 
            cpu_info = "CPU: indisponível"
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
        try:
            total, used, free = shutil.disk_usage("/")
            disk_percent = round((used / total) * 100, 1)
            disk_total_gb = round(total / (1024**3), 2)
            disk_info = f"Disco: {disk_percent}% em uso (Total: {disk_total_gb} GB)"
        except Exception: 
            disk_info = "Disco: indisponível"
        return f"Status do Servidor:\n- {cpu_info}\n- {mem_info}\n- {disk_info}"
    except Exception as e: 
        return {"error": f"Falha ao ler status: {str(e)}"}

def _handler_get_weather(city="São Paulo"):
    cache_key = f"weather_{city}"
    cached = _get_cache(cache_key)
    if cached: 
        return cached
    
    city_encoded = urllib.parse.quote(city)
    try:
        req = Request(f"https://wttr.in/{city_encoded}?format=j1&lang=pt", headers={"User-Agent": "FRIDAY/1.0"})
        with urlopen(req, timeout=10) as response: 
            data = json.loads(response.read().decode("utf-8"))
        
        current = data["current_condition"][0]
        temp = current["temp_C"]
        desc = current["lang_pt"][0]["value"] if "lang_pt" in current and current["lang_pt"] else current["weatherDesc"][0]["value"]
        today = (data.get("weather") or [{}])[0]
        
        result = f"Em {city}: {desc}, {temp}°C. Máx {today.get('maxtempC', '?')}°C / Mín {today.get('mintempC', '?')}°C."
        _set_cache(cache_key, result, ttl_seconds=1800)
        return result
    except Exception as e: 
        return {"error": f"Falha no clima: {str(e)}"}

def _handler_get_crypto_price(coin="bitcoin"):
    cache_key = f"crypto_{coin}"
    cached = _get_cache(cache_key)
    if cached: 
        return cached
    
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin.lower()}&vs_currencies=usd,brl&include_24hr_change=true"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as response: 
            data = json.loads(response.read().decode("utf-8"))
        
        if coin.lower() not in data: 
            return {"error": f"Moeda '{coin}' não encontrada."}
        
        info = data[coin.lower()]
        change = info.get('usd_24h_change', 0)
        direction = "📈" if change > 0 else "📉"
        result = f"{coin.capitalize()}: US$ {info['usd']:,.2f} | R$ {info['brl']:,.2f} ({direction} {change:.1f}% 24h)."
        
        _set_cache(cache_key, result, ttl_seconds=300)
        return result
    except Exception as e: 
        return {"error": f"Falha ao buscar cotação: {str(e)}"}

def _handler_get_news(category="mundo"):
    cache_key = f"news_{category}"
    cached = _get_cache(cache_key)
    if cached: 
        return cached
    
    try:
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(category)}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as response: 
            xml = response.read().decode("utf-8")
        
        root = ET.fromstring(xml)
        items = root.findall(".//item")[:3]
        
        if not items: 
            return "Nenhuma notícia encontrada."
        
        news_list = [f"- {item.find('title').text}" for item in items]
        result = "Últimas notícias:\n" + "\n".join(news_list)
        
        _set_cache(cache_key, result, ttl_seconds=3600)
        return result
    except Exception as e: 
        return {"error": f"Falha ao buscar notícias: {str(e)}"}

def _handler_search_web(query, max_results=3):
    cache_key = f"search_{query}_{max_results}"
    cached = _get_cache(cache_key)
    if cached: 
        return cached
    
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    if tavily_key:
        try:
            payload = json.dumps({"query": query, "search_depth": "basic", "max_results": max_results, "include_answer": False}).encode("utf-8")
            req = Request("https://api.tavily.com/search", data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {tavily_key}"}, method="POST")
            with urlopen(req, timeout=15) as response: 
                data = json.loads(response.read().decode("utf-8"))
            
            results = [{"title": item.get("title", ""), "url": item.get("url", ""), "snippet": _truncate_text(item.get("content", ""), 500)} for item in data.get("results", [])[:max_results]]
            if results:
                final = {"query": query, "results": results, "source": "tavily"}
                _set_cache(cache_key, final, ttl_seconds=600)
                return final
        except Exception: 
            pass
    
    try:
        params = urllib.parse.urlencode({"q": query, "format": "json", "no_html": 1, "skip_disambig": 1})
        req = Request(f"https://api.duckduckgo.com/?{params}", headers={"User-Agent": "FRIDAY/1.0"})
        with urlopen(req, timeout=10) as response: 
            data = json.loads(response.read().decode("utf-8"))
        
        abstract = data.get("AbstractText") or data.get("Answer")
        if abstract:
            final = {"query": query, "results": [{"title": data.get("Heading", "Resultado"), "url": data.get("AbstractURL", ""), "snippet": _truncate_text(abstract, 800)}], "source": "duckduckgo"}
            _set_cache(cache_key, final, ttl_seconds=600)
            return final
        
        related = data.get("RelatedTopics", [])
        results = [{"title": "Tópico relacionado", "url": t.get("FirstURL", ""), "snippet": _truncate_text(t["Text"], 300)} for t in related[:max_results] if isinstance(t, dict) and "Text" in t]
        if results:
            final = {"query": query, "results": results, "source": "duckduckgo"}
            _set_cache(cache_key, final, ttl_seconds=600)
            return final
            
        return {"error": "Nenhum resultado encontrado."}
    except Exception as e: 
        return {"error": f"Falha na pesquisa: {str(e)}"}

def _handler_deep_search(query):
    search = _handler_search_web(query, max_results=3)
    if "error" in search: 
        return search
    results = search.get("results", [])
    if not results: 
        return {"error": "Nenhum resultado encontrado."}
    
    top = results[0]
    top_url = top.get("url", "")
    if not top_url: 
        return {"query": query, "results": results, "note": "Sem URL para leitura."}
    
    page = _handler_read_webpage(top_url)
    content = page if isinstance(page, str) else ""
    return {"query": query, "top_result": {"title": top.get("title", ""), "url": top_url, "content": content}, "other_results": results[1:]}

def _handler_read_webpage(url):
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urlopen(req, timeout=15) as response:
            parser = _HTMLTextExtractor()
            parser.feed(response.read().decode("utf-8", errors="replace"))
            return f"Conteúdo de {url}:\n\n{_truncate_text(parser.get_text(), 6000)}"
    except Exception as e: 
        return {"error": f"Falha ao ler a página: {str(e)}"}

# ========================================
# HANDLERS: E-MAIL E ANEXOS
# ========================================
def _handler_send_email(to_email, subject, body, attachment_base64=None, attachment_name=None):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    
    if not all([smtp_server, smtp_user, smtp_pass]): 
        return {"error": "Credenciais ausentes."}
    
    if attachment_base64 and attachment_name:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        try:
            data = attachment_base64.split(",", 1)[1] if "," in attachment_base64 else attachment_base64
            raw = base64.b64decode(data)
            part = MIMEApplication(raw)
            part.add_header('Content-Disposition', 'attachment', filename=attachment_name)
            msg.attach(part)
        except Exception as e: 
            return {"error": f"Falha no anexo: {e}"}
    else: 
        msg = MIMEText(body, 'plain', 'utf-8')
    
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = to_email
    
    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return f"E-mail enviado para {to_email}."
    except Exception as e: 
        return {"error": f"Falha ao enviar: {str(e)}"}

def _handler_reply_to_email(to_email, subject, in_reply_to="", body=None, original_body=""):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    
    if not all([smtp_server, smtp_user, smtp_pass]): 
        return {"error": "Credenciais ausentes."}
    
    msg = MIMEText(body or f"Respondendo sobre: {original_body[:200]}...", 'plain', 'utf-8')
    msg['Subject'] = f"Re: {subject}" if not subject.lower().startswith("re:") else subject
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
        return f"Resposta enviada para {to_email}."
    except Exception as e: 
        return {"error": f"Falha ao responder: {str(e)}"}

def _handler_read_emails(query="", max_results=5):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    if not callable(ctx): 
        return {"error": "Google não conectado."}
        
    access_token = ctx()
    if not access_token: 
        return {"error": "Google não conectado."}
    
    params = {"maxResults": str(max_results), "labelIds": "INBOX"}
    if query: 
        params["q"] = query
        
    try:
        req = Request("https://gmail.googleapis.com/gmail/v1/users/me/messages?" + urllib.parse.urlencode(params), headers={"Authorization": "Bearer " + access_token})
        with urlopen(req, timeout=15) as resp: 
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e: 
        return {"error": f"Falha Gmail: {e}"}
    
    messages = data.get("messages", [])
    if not messages: 
        return {"ok": True, "emails": [], "message": "Nenhum e-mail encontrado."}
        
    emails = []
    for msg in messages[:max_results]:
        try:
            req_d = Request(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date&metadataHeaders=Message-ID", headers={"Authorization": "Bearer " + access_token})
            with urlopen(req_d, timeout=10) as resp_d:
                d = json.loads(resp_d.read().decode("utf-8"))
                h = d.get("payload", {}).get("headers", [])
                subject = next((x["value"] for x in h if x["name"] == "Subject"), "(sem assunto)")
                sender = next((x["value"] for x in h if x["name"] == "From"), "")
                date = next((x["value"] for x in h if x["name"] == "Date"), "")
                attachments = [p.get("filename") for p in d.get("payload", {}).get("parts", []) if p.get("filename")]
                emails.append({"id": msg['id'], "subject": subject, "from": sender, "date": date, "thread_id": d.get("threadId"), "attachments": attachments})
        except Exception: 
            pass
            
    return {"ok": True, "emails": emails}

def _handler_read_email_full(message_id):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    if not callable(ctx): 
        return {"error": "Google não conectado."}
        
    access_token = ctx()
    if not access_token: 
        return {"error": "Google não conectado."}
        
    try:
        req = Request(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}?format=full", headers={"Authorization": "Bearer " + access_token})
        with urlopen(req, timeout=15) as resp: 
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e: 
        return {"error": f"Falha e-mail: {e}"}
    
    headers = data.get("payload", {}).get("headers", [])
    subject = next((x["value"] for x in headers if x["name"] == "Subject"), "(sem assunto)")
    sender = next((x["value"] for x in headers if x["name"] == "From"), "")
    body_text = _extract_email_body(data.get("payload", {}))
    attachments_text = ""
    
    if HAS_PYPDF2:
        for part in data.get("payload", {}).get("parts", []):
            if part.get("mimeType") == "application/pdf":
                att_id = part.get("body", {}).get("attachmentId")
                if att_id:
                    try:
                        req_a = Request(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/attachments/{att_id}", headers={"Authorization": "Bearer " + access_token})
                        with urlopen(req_a, timeout=15) as resp_a:
                            att_data = json.loads(resp_a.read().decode("utf-8"))
                            raw_b64 = att_data.get("data").replace("-", "+").replace("_", "/")
                            reader = PyPDF2.PdfReader(io.BytesIO(base64.b64decode(raw_b64)))
                            attachments_text += f"\n\n--- Anexo PDF ({part.get('filename')}) ---\n"
                            attachments_text += "\n".join((p.extract_text() or "") for p in reader.pages[:5])
                    except Exception as e: 
                        attachments_text += f"\n[Falha ao ler anexo: {e}]"
                    
    return {"subject": subject, "from": sender, "body": _truncate_text(body_text + attachments_text, 15000)}

def _handler_get_email_thread(thread_id):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    if not callable(ctx): 
        return {"error": "Google não conectado."}
        
    access_token = ctx()
    if not access_token: 
        return {"error": "Google não conectado."}
        
    try:
        req = Request(f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}?format=metadata", headers={"Authorization": "Bearer " + access_token})
        with urlopen(req, timeout=15) as resp: 
            data = json.loads(resp.read().decode("utf-8"))
        msgs = data.get("messages", [])
        return {"thread_id": thread_id, "history": "\n".join([f"Data: {m.get('snippet')}" for m in msgs])}
    except Exception as e: 
        return {"error": str(e)}

# ========================================
# HANDLERS: TAREFAS (POSTGRESQL / SQLITE)
# ========================================
def _handler_create_task(description):
    conn = _get_db()
    cursor = conn.cursor()
    try:
        if DATABASE_URL and HAS_PSYCOPG2:
            cursor.execute("INSERT INTO tasks (description, status) VALUES (%s, 'pending')", (description,))
        else:
            cursor.execute("INSERT INTO tasks (description, status) VALUES (?, 'pending')", (description,))
        conn.commit()
        return f"Tarefa criada: {description}"
    except Exception as e: 
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()

def _handler_list_tasks():
    conn = _get_db()
    cursor = conn.cursor()
    try:
        if DATABASE_URL and HAS_PSYCOPG2:
            cursor.execute("SELECT id, description FROM tasks WHERE status = 'pending'")
        else:
            cursor.execute("SELECT id, description FROM tasks WHERE status = 'pending'")
        rows = cursor.fetchall()
        if not rows: 
            return "Nenhuma tarefa pendente."
        return "Tarefas pendentes:\n" + "\n".join(f"[{r[0]}] {r[1]}" for r in rows)
    except Exception as e: 
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()

def _handler_complete_task(task_id):
    conn = _get_db()
    cursor = conn.cursor()
    try:
        if DATABASE_URL and HAS_PSYCOPG2:
            cursor.execute("UPDATE tasks SET status = 'done' WHERE id = %s", (task_id,))
        else:
            cursor.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))
        conn.commit()
        return f"Tarefa {task_id} concluída."
    except Exception as e: 
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()

# ========================================
# HANDLERS: MEMÓRIA, MÚSICA E CALENDÁRIO
# ========================================
def _handler_save_memory(key, value):
    save_func = _MEMORY_CONTEXT.get("save")
    if not callable(save_func): 
        return {"error": "Memória indisponível."}
    save_func(key, value)
    return f"Memória salva: {key} = {value}"

def _handler_recall_memory(key):
    recall = _MEMORY_CONTEXT.get("recall")
    if not callable(recall): 
        return {"error": "Memória indisponível."}
    return recall(key) or "Nada encontrado."

def _handler_delete_memory(key):
    del_func = _MEMORY_CONTEXT.get("delete")
    if not callable(del_func): 
        return {"error": "Memória indisponível."}
    return "Removida." if del_func(key) else "Não encontrada."

def _handler_play_music(query):
    if query.lower() in ["pause", "pausar", "para"]: 
        return {"action": "pause"}
    if query.lower() in ["play", "tocar", "continuar"]: 
        return {"action": "play"}
    
    try:
        req = Request(f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}", headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as response:
            m = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', response.read().decode("utf-8"))
        if m: 
            return {"action": "play_new", "url": f"https://www.youtube.com/embed/{m.group(1)}?autoplay=1&enablejsapi=1"}
        return {"error": "Vídeo não encontrado."}
    except Exception as e: 
        return {"error": str(e)}

def _handler_get_morning_briefing(city="Criciúma"):
    briefing = [_handler_get_weather(city)]
    agenda = _handler_get_calendar_events("today")
    if isinstance(agenda, dict) and agenda.get("events"):
        briefing.append(f"Agenda: {len(agenda['events'])} compromissos. Primeiro: '{agenda['events'][0]['summary']}'.")
    else: 
        briefing.append("Agenda livre.")
    briefing.append(_handler_get_news())
    return " ".join(briefing)

def _handler_get_calendar_events(range_value="upcoming"):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    if not callable(ctx): 
        return {"error": "Calendar indisponível."}
        
    access_token = ctx()
    if not access_token: 
        return {"error": "Calendar indisponível."}
        
    query = _CALENDAR_CONTEXT.get("events_query")
    today = _CALENDAR_CONTEXT.get("today_events")
    
    if range_value == "today" and callable(today):
        events = today(access_token)
    elif callable(query):
        events = query(access_token, max_results=10)
    else:
        events = None
        
    if not events: 
        return {"events": []}
        
    return {"range": range_value, "events": [{"id": e.get("id"), "summary": e.get("summary"), "start": _format_iso_to_br(e.get("start")), "hangoutLink": e.get("hangoutLink")} for e in events]}

def _handler_create_calendar_event(summary, start_natural, end_natural=None):
    start_dt = _parse_natural_date(start_natural)
    if not start_dt: 
        return {"error": "Data de início inválida."}
        
    end_dt = _parse_natural_date(end_natural) if end_natural else start_dt + timedelta(hours=1)
    
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    create = _CALENDAR_CONTEXT.get("create_event")
    
    if not callable(ctx) or not callable(create): 
        return {"error": "Calendar indisponível."}
        
    access_token = ctx()
    if not access_token: 
        return {"error": "Calendar indisponível (Token inválido ou expirado)."}
        
    payload = {"summary": summary, "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Sao_Paulo"}, "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Sao_Paulo"}}
    res = create(access_token, payload)
    
    if res:
        return {"ok": True, "summary": summary, "start": start_dt.isoformat()}
    return {"error": "O Google recusou a criação do evento."}

def _handler_delete_calendar_event(event_id):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    delete = _CALENDAR_CONTEXT.get("delete_event")
    
    if not callable(ctx) or not callable(delete): 
        return {"error": "Calendar indisponível."}
        
    access_token = ctx()
    if not access_token: 
        return {"error": "Calendar indisponível."}
        
    if delete(access_token, event_id):
        return {"ok": True}
    return {"error": "Falha ao deletar."}

def _handler_update_calendar_event(event_id, summary=None, start_natural=None, end_natural=None, description=None):
    start_dt = _parse_natural_date(start_natural) if start_natural else None
    end_dt = _parse_natural_date(end_natural) if end_natural else None
    
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    update = _CALENDAR_CONTEXT.get("update_event")
    
    if not callable(ctx) or not callable(update): 
        return {"error": "Calendar indisponível."}
        
    access_token = ctx()
    if not access_token: 
        return {"error": "Calendar indisponível."}
        
    payload = {}
    if summary: 
        payload["summary"] = summary
    if start_dt: 
        payload["start"] = {"dateTime": start_dt.isoformat(), "timeZone": "America/Sao_Paulo"}
    if end_dt: 
        payload["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "America/Sao_Paulo"}
    if description: 
        payload["description"] = description
        
    if not payload: 
        return {"error": "Nenhum campo para atualizar."}
        
    res = update(access_token, event_id, payload)
    
    if res:
        return {"ok": True, "updated": True}
    return {"error": "Falha ao atualizar."}

def _handler_find_free_slots(date_iso):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    query = _CALENDAR_CONTEXT.get("events_query")
    
    if not callable(ctx) or not callable(query): 
        return {"error": "Calendar indisponível."}
        
    access_token = ctx()
    if not access_token: 
        return {"error": "Calendar indisponível."}
        
    target = _parse_iso(date_iso) or _parse_natural_date(date_iso)
    if not target: 
        return {"error": f"Data inválida: {date_iso}"}
        
    target = target.astimezone(_get_tz())
    start_of_day = target.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)
    
    events = query(access_token, time_min=start_of_day.isoformat(), time_max=end_of_day.isoformat(), max_results=50)
    if events is None: 
        return {"error": "Falha ao consultar agenda."}
        
    work_start = start_of_day.replace(hour=8)
    work_end = start_of_day.replace(hour=20)
    
    busy = []
    for evt in events:
        try: 
            busy.append((_parse_iso(evt["start"]).astimezone(_get_tz()), _parse_iso(evt["end"]).astimezone(_get_tz())))
        except Exception: 
            continue
            
    busy.sort(key=lambda x: x[0])
    duration_minutes = 60
    limit = 3
    slots = []
    current = work_start
    
    for s, e in busy:
        if s > current and (s - current).total_seconds() >= duration_minutes * 60:
            slots.append(current)
            if len(slots) >= limit: 
                break
        if e > current: 
            current = e
            
    if len(slots) < limit and current < work_end and (work_end - current).total_seconds() >= duration_minutes * 60:
        slots.append(current)
        
    return {"date": start_of_day.strftime("%Y-%m-%d"), "free_slots": [s.strftime("%H:%M") for s in slots[:limit]]}

# ========================================
# REGISTRO DE FERRAMENTAS
# ========================================
TOOL_REGISTRY = {
    "get_current_time": {
        "description": "Retorna data e hora.", 
        "handler": _handler_get_current_time, 
        "parameters": {"type": "object", "properties": {}}
    },
    "get_system_status": {
        "description": "Status da CPU/RAM/Disco.", 
        "handler": _handler_get_system_status, 
        "parameters": {"type": "object", "properties": {}}
    },
    "get_weather": {
        "description": "Consulta o clima.", 
        "handler": _handler_get_weather, 
        "parameters": {
            "type": "object", 
            "properties": {"city": {"type": "string", "description": "Nome da cidade"}}, 
            "required": ["city"]
        }
    },
    "get_crypto_price": {
        "description": "Cotações de cripto (bitcoin, ethereum).", 
        "handler": _handler_get_crypto_price, 
        "parameters": {
            "type": "object", 
            "properties": {"coin": {"type": "string", "description": "Nome da criptomoeda"}}, 
            "required": ["coin"]
        }
    },
    "get_news": {
        "description": "Lê as últimas notícias (mundo, tecnologia, etc).", 
        "handler": _handler_get_news, 
        "parameters": {
            "type": "object", 
            "properties": {"category": {"type": "string", "description": "Categoria da notícia"}}, 
            "required": ["category"]
        }
    },
    "search_web": {
        "description": "Pesquisa na web.", 
        "handler": _handler_search_web, 
        "parameters": {
            "type": "object", 
            "properties": {"query": {"type": "string", "description": "Termo de pesquisa"}}, 
            "required": ["query"]
        }
    },
    "deep_search": {
        "description": "Pesquisa e lê o conteúdo completo do primeiro resultado.", 
        "handler": _handler_deep_search, 
        "parameters": {
            "type": "object", 
            "properties": {"query": {"type": "string", "description": "Termo de pesquisa"}}, 
            "required": ["query"]
        }
    },
    "read_webpage": {
        "description": "Lê uma URL.", 
        "handler": _handler_read_webpage, 
        "parameters": {
            "type": "object", 
            "properties": {"url": {"type": "string", "description": "URL da página"}}, 
            "required": ["url"]
        }
    },
    "send_email": {
        "description": "Envia e-mail.", 
        "handler": _handler_send_email, 
        "parameters": {
            "type": "object", 
            "properties": {
                "to_email": {"type": "string", "description": "E-mail do destinatário"},
                "subject": {"type": "string", "description": "Assunto do e-mail"},
                "body": {"type": "string", "description": "Corpo do e-mail"}
            }, 
            "required": ["to_email", "subject", "body"]
        }, 
        "requires_confirmation": True
    },
    "reply_to_email": {
        "description": "Responde e-mail.", 
        "handler": _handler_reply_to_email, 
        "parameters": {
            "type": "object", 
            "properties": {
                "to_email": {"type": "string", "description": "E-mail do destinatário"},
                "subject": {"type": "string", "description": "Assunto do e-mail"},
                "body": {"type": "string", "description": "Corpo da resposta"}
            }, 
            "required": ["to_email", "subject", "body"]
        }, 
        "requires_confirmation": True
    },
    "read_emails": {
        "description": "Lê e-mails recentes.", 
        "handler": _handler_read_emails, 
        "parameters": {
            "type": "object", 
            "properties": {
                "query": {"type": "string", "description": "Filtro de busca"},
                "max_results": {"type": "integer", "description": "Número máximo de e-mails"}
            }
        }
    },
    "read_email_full": {
        "description": "Lê o corpo e anexos de um e-mail.", 
        "handler": _handler_read_email_full, 
        "parameters": {
            "type": "object", 
            "properties": {"message_id": {"type": "string", "description": "ID da mensagem"}}, 
            "required": ["message_id"]
        }
    },
    "get_email_thread": {
        "description": "Lê a conversa de e-mail.", 
        "handler": _handler_get_email_thread, 
        "parameters": {
            "type": "object", 
            "properties": {"thread_id": {"type": "string", "description": "ID da thread"}}, 
            "required": ["thread_id"]
        }
    },
    "create_task": {
        "description": "Cria uma tarefa (to-do) na lista de tarefas.", 
        "handler": _handler_create_task, 
        "parameters": {
            "type": "object", 
            "properties": {"description": {"type": "string", "description": "Descrição da tarefa"}}, 
            "required": ["description"]
        }
    },
    "list_tasks": {
        "description": "Lista tarefas pendentes.", 
        "handler": _handler_list_tasks, 
        "parameters": {"type": "object", "properties": {}}
    },
    "complete_task": {
        "description": "Conclui uma tarefa.", 
        "handler": _handler_complete_task, 
        "parameters": {
            "type": "object", 
            "properties": {"task_id": {"type": "integer", "description": "ID da tarefa"}}, 
            "required": ["task_id"]
        }
    },
    "save_memory": {
        "description": "Salva memória.", 
        "handler": _handler_save_memory, 
        "parameters": {
            "type": "object", 
            "properties": {
                "key": {"type": "string", "description": "Chave da memória"},
                "value": {"type": "string", "description": "Valor da memória"}
            }, 
            "required": ["key", "value"]
        }
    },
    "recall_memory": {
        "description": "Recupera memória.", 
        "handler": _handler_recall_memory, 
        "parameters": {
            "type": "object", 
            "properties": {"key": {"type": "string", "description": "Chave da memória"}}, 
            "required": ["key"]
        }
    },
    "delete_memory": {
        "description": "Apaga memória.", 
        "handler": _handler_delete_memory, 
        "parameters": {
            "type": "object", 
            "properties": {"key": {"type": "string", "description": "Chave da memória"}}, 
            "required": ["key"]
        }, 
        "requires_confirmation": True
    },
    "play_music": {
        "description": "Toca ou controla música (pause, play, nome da música).", 
        "handler": _handler_play_music, 
        "parameters": {
            "type": "object", 
            "properties": {"query": {"type": "string", "description": "Nome da música ou comando"}}, 
            "required": ["query"]
        }
    },
    "get_morning_briefing": {
        "description": "Resumo matinal.", 
        "handler": _handler_get_morning_briefing, 
        "parameters": {
            "type": "object", 
            "properties": {"city": {"type": "string", "description": "Nome da cidade"}}, 
            "required": ["city"]
        }
    },
    "get_calendar_events": {
        "description": "Consulta agenda.", 
        "handler": _handler_get_calendar_events, 
        "parameters": {
            "type": "object", 
            "properties": {"range": {"type": "string", "enum": ["today", "upcoming"]}}, 
            "required": ["range"]
        }
    },
    "create_calendar_event": {
        "description": "Cria um evento na agenda do Google Calendar.", 
        "handler": _handler_create_calendar_event, 
        "parameters": {
            "type": "object", 
            "properties": {
                "summary": {"type": "string", "description": "Título do evento"},
                "start_natural": {"type": "string", "description": "Data/hora de início (ex: 'amanhã às 14h')"},
                "end_natural": {"type": "string", "description": "Data/hora de término"}
            }, 
            "required": ["summary", "start_natural"]
        }, 
        "requires_confirmation": True
    },
    "delete_calendar_event": {
        "description": "Exclui evento do calendário.", 
        "handler": _handler_delete_calendar_event, 
        "parameters": {
            "type": "object", 
            "properties": {"event_id": {"type": "string", "description": "ID do evento"}}, 
            "required": ["event_id"]
        }, 
        "requires_confirmation": True
    },
    "update_calendar_event": {
        "description": "Atualiza evento do calendário.", 
        "handler": _handler_update_calendar_event, 
        "parameters": {
            "type": "object", 
            "properties": {
                "event_id": {"type": "string", "description": "ID do evento"},
                "summary": {"type": "string", "description": "Novo título"},
                "start_natural": {"type": "string", "description": "Nova data de início"},
                "end_natural": {"type": "string", "description": "Nova data de término"}
            }, 
            "required": ["event_id"]
        }, 
        "requires_confirmation": True
    },
    "find_free_slots": {
        "description": "Encontra horários livres na agenda.", 
        "handler": _handler_find_free_slots, 
        "parameters": {
            "type": "object", 
            "properties": {"date_iso": {"type": "string", "description": "Data (YYYY-MM-DD)"}}, 
            "required": ["date_iso"]
        }
    },
}

def public_tool_specs():
    return {name: {k: v for k, v in spec.items() if k != "handler"} for name, spec in TOOL_REGISTRY.items()}

def execute_tool(tool_name, arguments, user_role="owner", session=None, confirmed=False):
    tool = TOOL_REGISTRY.get(tool_name)
    if not tool: 
        return {"error": "Ferramenta não encontrada."}
        
    if tool.get("requires_confirmation") and not confirmed:
        return {"status": "pending_confirmation", "message": f"Confirma a execução de '{tool_name}'?"}
        
    try:
        return {"ok": True, "result": tool["handler"](**arguments)}
    except Exception as e:
        return {"error": f"Falha: {str(e)}"}
