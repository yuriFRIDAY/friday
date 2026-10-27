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
from html.parser import HTMLParser
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.utils import parseaddr
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

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
    return ZoneInfo("America/Sao_Paulo") if HAS_ZONEINFO else timezone(timedelta(hours=-3))

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
                'RELATIVE_BASE': now,
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
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        dt = dt.astimezone(_get_tz())
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
# HANDLERS
# ========================================

def _handler_get_current_time():
    now = datetime.now(_get_tz())
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

        today = (data.get("weather") or [{}])[0]
        max_temp = today.get("maxtempC", "?")
        min_temp = today.get("mintempC", "?")
        max_rain = 0
        for h in today.get("hourly", []):
            try:
                max_rain = max(max_rain, int(h.get("chanceofrain", 0)))
            except (TypeError, ValueError):
                pass

        result = f"Em {city}: {desc}, {temp}°C (sensação de {feels_like}°C). Umidade: {humidity}%."
        result += f" Previsão de hoje: máx {max_temp}°C / mín {min_temp}°C."
        if max_rain > 0:
            result += f" Chance de chuva: {max_rain}%."
        return result
    except Exception as e:
        return {"error": f"Não foi possível obter o clima: {str(e)}"}

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
        results = [{"title": "Tópico relacionado", "url": t.get("FirstURL", ""), "snippet": _truncate_text(t["Text"], 300)} for t in related[:max_results] if isinstance(t, dict) and "Text" in t]
        if results:
            return {"query": query, "results": results, "source": "duckduckgo"}
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
    return {
        "query": query,
        "top_result": {"title": top.get("title", ""), "url": top_url, "content": content},
        "other_results": results[1:],
    }

def _handler_read_webpage(url):
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8", errors="replace")
        parser = _HTMLTextExtractor()
        parser.feed(html)
        return f"Conteúdo de {url}:\n\n{_truncate_text(parser.get_text(), 6000)}"
    except Exception as e:
        return {"error": f"Falha ao ler a página: {str(e)}"}

def _handler_send_email(to_email, subject, body, attachment_base64=None, attachment_name=None):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")

    if not all([smtp_server, smtp_user, smtp_pass]):
        return {"error": "Credenciais de e-mail não configuradas."}

    if attachment_base64 and attachment_name:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        try:
            data = attachment_base64
            if "," in data and "base64" in data[:100]:
                data = data.split(",", 1)[1]
            raw = base64.b64decode(data)
            part = MIMEApplication(raw)
            part.add_header('Content-Disposition', 'attachment', filename=attachment_name)
            msg.attach(part)
        except Exception as e:
            return {"error": f"Falha ao anexar arquivo: {str(e)}"}
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
                sender_name, sender_email = parseaddr(raw_from)
                sender = f"{sender_name} ({sender_email})" if sender_name else sender_email or "(desconhecido)"
                date = next((h["value"] for h in headers if h["name"] == "Date"), "")
                message_id = next((h["value"] for h in headers if h["name"] == "Message-ID"), "")
                snippet = _truncate_text(detail.get("snippet", ""), 300)
                thread_id = detail.get("threadId", "")
                attachments = []
                for part in detail.get("payload", {}).get("parts", []):
                    fname = part.get("filename")
                    if fname:
                        attachments.append(fname)
                emails.append({
                    "subject": subject, "from": sender, "date": date,
                    "snippet": snippet, "message_id": message_id,
                    "thread_id": thread_id, "attachments": attachments,
                })
        except Exception:
            pass
    return {"ok": True, "emails": emails}

def _handler_read_email_full(message_id):
    if not message_id:
        return {"error": "ID da mensagem não fornecido."}
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    if not callable(ctx):
        return {"error": "Integração indisponível."}
    try:
        access_token = ctx()
    except Exception:
        return {"error": "Falha ao obter token."}
    if not access_token:
        return {"error": "Google não conectado."}

    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}?format=full"
    req = Request(url, headers={"Authorization": "Bearer " + access_token})
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": f"Falha ao ler e-mail: {str(e)}"}

    headers = data.get("payload", {}).get("headers", [])
    subject = next((h["value"] for h in headers if h["name"] == "Subject"), "(sem assunto)")
    sender = next((h["value"] for h in headers if h["name"] == "From"), "")
    body_text = _extract_email_body(data.get("payload", {}))
    return {"subject": subject, "from": sender, "body": _truncate_text(body_text, 10000)}

def _handler_get_email_thread(thread_id):
    if not thread_id or not isinstance(thread_id, str):
        return {"error": "ID da conversa inválido."}
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    if not callable(ctx):
        return {"error": "Integração indisponível."}
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
        return {"error": f"Falha ao salvar: {str(e)}"}

def _handler_recall_memory(key):
    recall_func = _MEMORY_CONTEXT.get("recall")
    if not callable(recall_func):
        return {"error": "Sistema de memória indisponível."}
    try:
        value = recall_func(key)
        return value if value else {"error": f"Nenhuma memória encontrada para: {key}"}
    except Exception as e:
        return {"error": f"Falha ao recuperar: {str(e)}"}

def _handler_delete_memory(key):
    delete_func = _MEMORY_CONTEXT.get("delete")
    if not callable(delete_func):
        return {"error": "Sistema de memória indisponível para exclusão."}
    try:
        deleted = delete_func(key)
        return f"Memória '{key}' removida." if deleted else f"Nenhuma memória encontrada para '{key}'."
    except Exception as e:
        return {"error": f"Falha ao deletar: {str(e)}"}

def _handler_play_music(query, is_playlist=False):
    query_encoded = urllib.parse.quote(query)

    if is_playlist:
        try:
            url = f"https://pipedapi.kavin.rocks/search?q={query_encoded}&filter=playlists"
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
            items = data.get("items", [])
            if items and "url" in items[0]:
                qs = urllib.parse.parse_qs(urllib.parse.urlsplit(items[0]["url"]).query)
                pid = qs.get("list", [None])[0]
                if pid:
                    embed_url = f"https://www.youtube.com/embed/videoseries?list={pid}&autoplay=1"
                    return {"ok": True, "action": "play_music", "url": embed_url, "query": query, "type": "playlist"}
        except Exception:
            pass

    try:
        piped_url = f"https://pipedapi.kavin.rocks/search?q={query_encoded}&filter=videos"
        req = Request(piped_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        items = data.get("items", [])
        if items and "url" in items[0]:
            vid = items[0]["url"].split("v=")[-1]
            return {"ok": True, "action": "play_music", "url": f"https://www.youtube.com/embed/{vid}?autoplay=1", "query": query, "type": "video"}
    except Exception:
        pass

    try:
        url = f"https://www.youtube.com/results?search_query={query_encoded}"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8")
        m = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
        if m:
            vid = m.group(1)
            return {"ok": True, "action": "play_music", "url": f"https://www.youtube.com/embed/{vid}?autoplay=1", "query": query, "type": "video"}
    except Exception:
        pass
    return {"error": "Não consegui encontrar o vídeo."}

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
        label = "próximos dias"
    if events is None:
        return {"error": "Não foi possível consultar o calendário."}

    formatted = []
    for evt in events:
        formatted.append({
            "id": evt.get("id", ""),
            "summary": evt.get("summary", "(sem título)"),
            "start": _format_iso_to_br(evt.get("start", "")),
            "hangoutLink": evt.get("hangoutLink", ""),
            "location": evt.get("location", ""),
        })
    return {"range": label, "events": formatted, "count": len(formatted)}

def _handler_create_calendar_event(summary, start_iso=None, end_iso=None, start_natural=None,
                                   end_natural=None, description="", location=""):
    if start_iso:
        start_dt = _parse_iso(start_iso)
    elif start_natural:
        start_dt = _parse_natural_date(start_natural)
    else:
        return {"error": "Forneça start_iso ou start_natural."}
    if not start_dt:
        return {"error": f"Não consegui interpretar: {start_iso or start_natural}"}

    if end_iso:
        end_dt = _parse_iso(end_iso)
    elif end_natural:
        end_dt = _parse_natural_date(end_natural)
    else:
        end_dt = start_dt + timedelta(hours=1)
    if not end_dt:
        return {"error": "Não consegui interpretar o fim do evento."}

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
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Sao_Paulo"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Sao_Paulo"},
    }
    if description: payload["description"] = description
    if location: payload["location"] = location

    result = create(access_token, payload)
    if result is None:
        return {"error": "O Google recusou a criação do evento."}
    return {"ok": True, "created": True, "summary": summary,
            "start": start_dt.isoformat(), "end": end_dt.isoformat(),
            "link": result.get("htmlLink", "")}

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
        return {"error": "O Google recusou a exclusão (verifique o ID)."}
    return {"ok": True, "deleted": True, "message": "Evento excluído."}

def _handler_update_calendar_event(event_id, summary=None, start_iso=None, end_iso=None,
                                   start_natural=None, end_natural=None, description=None):
    start_dt = _parse_iso(start_iso) if start_iso else (_parse_natural_date(start_natural) if start_natural else None)
    end_dt = _parse_iso(end_iso) if end_iso else (_parse_natural_date(end_natural) if end_natural else None)

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
    if start_dt: payload["start"] = {"dateTime": start_dt.isoformat(), "timeZone": "America/Sao_Paulo"}
    if end_dt: payload["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "America/Sao_Paulo"}
    if description: payload["description"] = description

    if not payload:
        return {"error": "Nenhum campo para atualizar."}

    result = update(access_token, event_id, payload)
    if result is None:
        return {"error": "O Google recusou a atualização."}
    return {"ok": True, "updated": True,
            "summary": summary,
            "start": start_dt.isoformat() if start_dt else None,
            "end": end_dt.isoformat() if end_dt else None}

def _handler_find_free_slots(date_iso):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    query = _CALENDAR_CONTEXT.get("events_query")
    if not callable(ctx) or not callable(query):
        return {"error": "Integração indisponível."}
    try:
        access_token = ctx()
    except Exception:
        return {"error": "Falha ao obter token."}
    if not access_token:
        return {"error": "Calendário não conectado."}

    target = _parse_iso(date_iso) or _parse_natural_date(date_iso)
    if not target:
        return {"error": f"Data inválida: {date_iso}"}
    target = target.astimezone(_get_tz())

    start_of_day = target.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)

    events = query(access_token, time_min=start_of_day.isoformat(),
                   time_max=end_of_day.isoformat(), max_results=50)
    if events is None:
        return {"error": "Falha ao consultar agenda."}

    work_start = start_of_day.replace(hour=8)
    work_end = start_of_day.replace(hour=20)

    busy = []
    for evt in events:
        try:
            s = _parse_iso(evt["start"]).astimezone(_get_tz())
            e = _parse_iso(evt["end"]).astimezone(_get_tz())
            busy.append((s, e))
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

    return {
        "date": start_of_day.strftime("%Y-%m-%d"),
        "duration_minutes": duration_minutes,
        "free_slots": [s.strftime("%H:%M") for s in slots[:limit]],
    }

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

def set_memory_context(save_func, recall_func, delete_func=None):
    _MEMORY_CONTEXT["save"] = save_func
    _MEMORY_CONTEXT["recall"] = recall_func
    if delete_func:
        _MEMORY_CONTEXT["delete"] = delete_func

TOOL_REGISTRY = {
    "get_current_time": {"description": "Retorna data e hora.", "parameters": {"type": "object", "properties": {}}, "requires_confirmation": False, "handler": _handler_get_current_time},
    "get_weather": {"description": "Consulta o clima e previsão do tempo.", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}, "requires_confirmation": False, "handler": _handler_get_weather},
    "get_system_status": {"description": "Status da CPU/RAM.", "parameters": {"type": "object", "properties": {}}, "requires_confirmation": False, "handler": _handler_get_system_status},
    "search_web": {"description": "Pesquisa na web.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, "requires_confirmation": False, "handler": _handler_search_web},
    "deep_search": {"description": "Pesquisa e lê o conteúdo completo do primeiro resultado.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, "requires_confirmation": False, "handler": _handler_deep_search},
    "read_webpage": {"description": "Lê o texto de uma URL.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}, "requires_confirmation": False, "handler": _handler_read_webpage},
    "send_email": {
        "description": "Envia e-mail.",
        "parameters": {
            "type": "object",
            "properties": {
                "to_email": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"}
            },
            "required": ["to_email", "subject", "body"]
        },
        "requires_confirmation": True,
        "handler": _handler_send_email,
    },
    "reply_to_email": {"description": "Responde e-mail mantendo thread.", "parameters": {"type": "object", "properties": {"to_email": {"type": "string"}, "subject": {"type": "string"}, "in_reply_to": {"type": "string"}, "body": {"type": "string"}, "original_body": {"type": "string"}}, "required": ["to_email", "subject"]}, "requires_confirmation": True, "handler": _handler_reply_to_email},
    "read_emails": {"description": "Lê e-mails recentes.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}}, "requires_confirmation": False, "handler": _handler_read_emails},
    "read_email_full": {"description": "Lê o CORPO COMPLETO de um e-mail (use o message_id de read_emails).", "parameters": {"type": "object", "properties": {"message_id": {"type": "string"}}, "required": ["message_id"]}, "requires_confirmation": False, "handler": _handler_read_email_full},
    "get_email_thread": {"description": "Lê toda a conversa de um e-mail (thread).", "parameters": {"type": "object", "properties": {"thread_id": {"type": "string"}}, "required": ["thread_id"]}, "requires_confirmation": False, "handler": _handler_get_email_thread},
    "save_memory": {"description": "Salva memória.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}, "requires_confirmation": False, "handler": _handler_save_memory},
    "recall_memory": {"description": "Recupera memória.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}, "requires_confirmation": False, "handler": _handler_recall_memory},
    "delete_memory": {"description": "Apaga uma memória.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}, "requires_confirmation": True, "handler": _handler_delete_memory},
    "play_music": {
        "description": "Toca música ou playlist no YouTube.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"]
        },
        "requires_confirmation": False,
        "handler": _handler_play_music,
    },
    "get_morning_briefing": {"description": "Gera briefing matinal.", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}, "requires_confirmation": False, "handler": _handler_get_morning_briefing},
    "get_calendar_events": {"description": "Consulta agenda.", "parameters": {"type": "object", "properties": {"range": {"type": "string", "enum": ["today", "upcoming"]}}, "required": ["range"]}, "requires_confirmation": False, "handler": _handler_get_calendar_events},
    "create_calendar_event": {"description": "Cria evento.", "parameters": {"type": "object", "properties": {"summary": {"type": "string"}, "start_iso": {"type": "string"}, "start_natural": {"type": "string"}, "end_iso": {"type": "string"}, "end_natural": {"type": "string"}, "description": {"type": "string"}, "location": {"type": "string"}}, "required": ["summary"]}, "requires_confirmation": True, "handler": _handler_create_calendar_event},
    "delete_calendar_event": {"description": "Exclui evento.", "parameters": {"type": "object", "properties": {"event_id": {"type": "string"}}, "required": ["event_id"]}, "requires_confirmation": True, "handler": _handler_delete_calendar_event},
    "update_calendar_event": {"description": "Atualiza evento.", "parameters": {"type": "object", "properties": {"event_id": {"type": "string"}, "summary": {"type": "string"}, "start_iso": {"type": "string"}, "start_natural": {"type": "string"}, "end_iso": {"type": "string"}, "end_natural": {"type": "string"}, "description": {"type": "string"}}, "required": ["event_id"]}, "requires_confirmation": True, "handler": _handler_update_calendar_event},
    "find_free_slots": {
        "description": "Encontra horários livres na agenda em um dia.",
        "parameters": {
            "type": "object",
            "properties": {"date_iso": {"type": "string"}},
            "required": ["date_iso"]
        },
        "requires_confirmation": False,
        "handler": _handler_find_free_slots,
    },
}

def public_tool_specs():
    return {name: {k: v for k, v in spec.items() if k != "handler"} for name, spec in TOOL_REGISTRY.items()}

def _validate_required(schema, arguments):
    required = schema.get("parameters", {}).get("required", [])
    for field in required:
        if field not in arguments:
            return f"Parâmetro obrigatório ausente: {field}"
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
        return {
            "status": "pending_confirmation",
            "tool": tool_name,
            "arguments": arguments,
            "message": f"A ferramenta '{tool_name}' tem efeito colateral. Confirma? (responda 'confirmar' ou 'cancelar')",
        }

    handler = tool.get("handler")
    if not callable(handler):
        return {"error": "Ferramenta indisponível."}
    try:
        result = handler(**arguments)
    except TypeError as exc:
        return {"error": f"Argumentos inválidos: {exc}"}
    except Exception as exc:
        return {"error": f"Falha ao executar: {exc}"}
    return {"ok": True, "result": result}
