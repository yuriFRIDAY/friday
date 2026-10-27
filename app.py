import os
import sys
import json
import time
import fcntl
import hmac
import hashlib
import base64
import io
import re
import threading
import sqlite3
import secrets
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urlsplit, parse_qs, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False

from core.assistant import FridayAssistant
from core.auth import FridayAuth
from core.sessions import SessionStore
from core.friday_tools import (
    TOOL_REGISTRY, execute_tool, public_tool_specs,
    set_calendar_context, set_memory_context,
)

SESSION_COOKIE = "friday_session"
MAX_BODY = 10 * 1024 * 1024
MAX_HISTORY_MESSAGES = 10
DATA_DIR = os.getenv("DATA_DIR", ".")
if os.path.exists("/data") and os.access("/data", os.W_OK):
    DATA_DIR = "/data"
DB_PATH = os.path.join(DATA_DIR, "friday.db")

STATUS_QUEUE = []
QUEUE_LOCK = threading.Lock()

def _push_status(text):
    global STATUS_QUEUE
    with QUEUE_LOCK:
        STATUS_QUEUE.append(text)
        if len(STATUS_QUEUE) > 20:
            STATUS_QUEUE.pop(0)

def _heartbeat_worker():
    print("FRIDAY WEB: Heartbeat iniciado.")
    time.sleep(15)
    while True:
        try:
            time.sleep(600)
        except Exception as e:
            print(f"Heartbeat erro: {e}")
            time.sleep(60)

# ========================================
# GOOGLE CALENDAR / GMAIL — OAUTH
# ========================================
GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/gmail.readonly"
OAUTH_STATE_TTL_SECONDS = 600
GOOGLE_OWNER_KEY = "owner"
GOOGLE_TOKEN_FILE = os.getenv("FRIDAY_GOOGLE_TOKEN_FILE", "").strip() or os.path.join(DATA_DIR, ".friday_google_tokens.json")
_TOKEN_MEMORY_ONLY = False

class GoogleOAuthError(Exception):
    def __init__(self, code):
        super().__init__(code or "oauth_error")
        self.code = code or "oauth_error"

def _token_file_candidates():
    return list(dict.fromkeys([GOOGLE_TOKEN_FILE, "/tmp/friday_google_tokens.json"]))

def _load_google_token_store():
    for path in _token_file_candidates():
        try:
            with open(path, "r", encoding="utf-8") as h:
                data = json.load(h)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        record = data.get(GOOGLE_OWNER_KEY)
        if not isinstance(record, dict):
            continue
        access_token = record.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            continue
        try:
            expires_at = float(record.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        return {GOOGLE_OWNER_KEY: {
            "access_token": access_token,
            "refresh_token": record.get("refresh_token") if isinstance(record.get("refresh_token"), str) and record.get("refresh_token") else None,
            "expires_at": expires_at,
            "scope": record.get("scope") if isinstance(record.get("scope"), str) else "",
        }}
    return {}

def _persist_google_token_store():
    global _TOKEN_MEMORY_ONLY
    record = GOOGLE_TOKEN_STORE.get(GOOGLE_OWNER_KEY)
    payload = {}
    if isinstance(record, dict):
        payload[GOOGLE_OWNER_KEY] = {
            "access_token": record.get("access_token"),
            "refresh_token": record.get("refresh_token"),
            "expires_at": record.get("expires_at"),
            "scope": record.get("scope") or "",
        }
    for path in _token_file_candidates():
        lock_path = path + ".lock"
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(lock_path, "a", encoding="utf-8") as lh:
                fcntl.flock(lh.fileno(), fcntl.LOCK_EX)
                try:
                    tmp = path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as h:
                        json.dump(payload, h)
                        h.write("\n")
                    try: os.chmod(tmp, 0o600)
                    except OSError: pass
                    os.replace(tmp, path)
                    try: os.chmod(path, 0o600)
                    except OSError: pass
                finally:
                    fcntl.flock(lh.fileno(), fcntl.LOCK_UN)
            _TOKEN_MEMORY_ONLY = False
            return True
        except OSError:
            continue
    _TOKEN_MEMORY_ONLY = True
    print("FRIDAY WEB: não foi possível gravar tokens.")
    return False

GOOGLE_TOKEN_STORE = _load_google_token_store()

def _owner_calendar_record():
    if not _TOKEN_MEMORY_ONLY:
        loaded = _load_google_token_store()
        memory = GOOGLE_TOKEN_STORE.get(GOOGLE_OWNER_KEY)
        disk = loaded.get(GOOGLE_OWNER_KEY) if loaded else None
        if disk or not isinstance(memory, dict):
            GOOGLE_TOKEN_STORE.clear()
            GOOGLE_TOKEN_STORE.update(loaded)
    return GOOGLE_TOKEN_STORE.get(GOOGLE_OWNER_KEY)

def _forget_owner_calendar_tokens():
    GOOGLE_TOKEN_STORE.pop(GOOGLE_OWNER_KEY, None)
    _persist_google_token_store()

def _oauth_state_secret():
    raw = os.getenv("FRIDAY_PASSWORD", "").strip() or GOOGLE_OAUTH_CLIENT_SECRET
    if not raw:
        raise RuntimeError("Segredo ausente para OAuth.")
    return raw.encode("utf-8")

def _make_oauth_state(session_token):
    expiry = int(time.time()) + OAUTH_STATE_TTL_SECONDS
    binding = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    payload = f"v1.{expiry}.{binding}.{secrets.token_urlsafe(16)}"
    sig = hmac.new(_oauth_state_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"

def _session_matches_oauth_state(state, session_token):
    if not isinstance(state, str) or not isinstance(session_token, str):
        return False
    parts = state.split(".")
    if len(parts) != 5 or parts[0] != "v1":
        return False
    payload = ".".join(parts[:4])
    expected = hmac.new(_oauth_state_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(parts[4], expected):
        return False
    try:
        if int(parts[1]) < int(time.time()):
            return False
    except ValueError:
        return False
    binding = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(parts[2], binding)

def _sign(payload):
    return hmac.new(_oauth_state_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()

def _make_session_cookie():
    expiry = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"s1.{expiry}.{secrets.token_urlsafe(32)}"
    token = f"{payload}.{_sign(payload)}"
    _restore_session(token)
    return token

def _signed_session_is_valid(token):
    if not isinstance(token, str):
        return False
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "s1":
        return False
    payload = ".".join(parts[:3])
    if not hmac.compare_digest(parts[3], _sign(payload)):
        return False
    try:
        return int(parts[1]) >= int(time.time())
    except ValueError:
        return False

def _read_session_cookie(header_value):
    cookie = SimpleCookie()
    try:
        cookie.load(header_value or "")
    except Exception:
        return None
    morsel = cookie.get(SESSION_COOKIE)
    return morsel.value if morsel and morsel.value else None

def _restore_session(session_token):
    if SESSION_STORE.get(session_token) is not None:
        return True
    try:
        from core.sessions import Session
        SESSION_STORE._sessions[session_token] = Session(
            token=session_token,
            expires_at=SESSION_STORE._clock() + SESSION_STORE.ttl_seconds,
        )
    except Exception:
        return False
    return SESSION_STORE.get(session_token) is not None

def _calendar_is_connected():
    record = _owner_calendar_record()
    if not record:
        return False
    if isinstance(record.get("refresh_token"), str) and record["refresh_token"]:
        return True
    try:
        expires_at = float(record.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    return isinstance(record.get("access_token"), str) and time.time() < expires_at - 30

def _save_owner_calendar_tokens(access_token, refresh_token, expires_in, scope):
    previous = _owner_calendar_record() or {}
    kept = refresh_token if isinstance(refresh_token, str) and refresh_token else previous.get("refresh_token")
    try:
        lifetime = int(expires_in)
    except (TypeError, ValueError):
        lifetime = 3600
    GOOGLE_TOKEN_STORE[GOOGLE_OWNER_KEY] = {
        "access_token": access_token,
        "refresh_token": kept,
        "expires_at": time.time() + max(60, lifetime),
        "scope": scope or "",
    }
    _persist_google_token_store()

def _google_error_code(exc):
    if not isinstance(exc, HTTPError):
        return ""
    try:
        data = json.loads(exc.read(4096).decode("utf-8", "replace"))
    except Exception:
        return ""
    code = data.get("error") if isinstance(data, dict) else ""
    return code if code in {"redirect_uri_mismatch", "invalid_client", "invalid_grant", "invalid_request", "unauthorized_client"} else ""

def _exchange_google_token(form, clear_on_invalid_grant=False):
    request = Request(
        GOOGLE_OAUTH_TOKEN_URL,
        data=urlencode(form).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read(65536).decode("utf-8"))
    except HTTPError as exc:
        code = _google_error_code(exc)
        if clear_on_invalid_grant and code == "invalid_grant":
            _forget_owner_calendar_tokens()
        raise GoogleOAuthError(code or "token_exchange") from None
    access_token = result.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise GoogleOAuthError("token_exchange")
    _save_owner_calendar_tokens(access_token, result.get("refresh_token"), result.get("expires_in", 3600), result.get("scope", ""))
    return access_token

def _usable_calendar_access_token():
    record = _owner_calendar_record()
    if not record:
        return None
    token = record.get("access_token")
    try:
        expires_at = float(record.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if isinstance(token, str) and token and time.time() < expires_at - 60:
        return token
    refresh_token = record.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        return None
    if not (GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET):
        return None
    return _exchange_google_token({
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }, clear_on_invalid_grant=True)

def _calendar_events_query(access_token, time_min=None, time_max=None, max_results=10):
    params = {"maxResults": str(max_results), "singleEvents": "true", "orderBy": "startTime"}
    params["timeMin"] = time_min or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if time_max:
        params["timeMax"] = time_max
    request = Request(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events?" + urlencode(params),
        headers={"Accept": "application/json", "Authorization": "Bearer " + access_token},
        method="GET",
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read(262144).decode("utf-8"))
    except Exception:
        return None
    events = []
    for item in (result.get("items") or [])[:max_results]:
        if not isinstance(item, dict):
            continue
        start = item.get("start") or {}
        end = item.get("end") or {}
        events.append({
            "id": item.get("id", ""),
            "summary": item.get("summary") or "(sem título)",
            "start": start.get("dateTime") or start.get("date") or "",
            "end": end.get("dateTime") or end.get("date") or "",
            "hangoutLink": item.get("hangoutLink") or "",
            "location": item.get("location") or "",
        })
    return events

def _calendar_today_events(access_token):
    now = time.localtime()
    return _calendar_events_query(
        access_token,
        time_min=time.strftime("%Y-%m-%dT00:00:00", now) + "Z",
        time_max=time.strftime("%Y-%m-%dT23:59:59", now) + "Z",
        max_results=20,
    )

def _calendar_create_event(access_token, payload):
    request = Request(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json", "Authorization": "Bearer " + access_token},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read(262144).decode("utf-8"))
    except Exception:
        return None

def _calendar_delete_event(access_token, event_id):
    request = Request(
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        headers={"Accept": "application/json", "Authorization": "Bearer " + access_token},
        method="DELETE",
    )
    try:
        with urlopen(request, timeout=15):
            return {"ok": True}
    except Exception:
        return None

def _calendar_update_event(access_token, event_id, payload):
    request = Request(
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json", "Authorization": "Bearer " + access_token},
        method="PATCH",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read(262144).decode("utf-8"))
    except Exception:
        return None

set_calendar_context(
    access_token_provider=_usable_calendar_access_token,
    events_query=_calendar_events_query,
    today_events=_calendar_today_events,
    create_event=_calendar_create_event,
    delete_event=_calendar_delete_event,
    update_event=_calendar_update_event,
)

GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
GOOGLE_OAUTH_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()

SESSION_TTL_SECONDS = 1800
COOKIE_SECURE_MODE = os.getenv("FRIDAY_COOKIE_SECURE", "auto").strip().lower()
if COOKIE_SECURE_MODE not in {"auto", "always", "never"}:
    raise RuntimeError("FRIDAY_COOKIE_SECURE inválido.")

auth = FridayAuth()
SESSION_STORE = SessionStore(ttl_seconds=SESSION_TTL_SECONDS)
friday = FridayAssistant()

# --- SQLite: memória com delete ---
def _db_save_memory(key, value):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ai_memory (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO ai_memory (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

def _db_recall_memory(key):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ai_memory (key TEXT PRIMARY KEY, value TEXT)")
        term = f"%{key}%"
        row = conn.execute("SELECT key, value FROM ai_memory WHERE key LIKE ? OR value LIKE ?", (term, term)).fetchone()
        if row:
            return f"{row[0]}: {row[1]}"
        rows = conn.execute("SELECT key, value FROM ai_memory ORDER BY rowid DESC LIMIT 5").fetchall()
        if rows:
            return "Memórias recentes:\n" + "\n".join(f"- {r[0]}: {r[1]}" for r in rows)
        return None

def _db_delete_memory(key):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ai_memory (key TEXT PRIMARY KEY, value TEXT)")
        cur = conn.execute("DELETE FROM ai_memory WHERE key LIKE ?", (f"%{key}%",))
        conn.commit()
        return cur.rowcount > 0

set_memory_context(save_func=_db_save_memory, recall_func=_db_recall_memory, delete_func=_db_delete_memory)

HTML_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

def get_html():
    try:
        with open(HTML_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Erro: index.html não encontrado.</h1>"


class FridayHandler(BaseHTTPRequestHandler):
    def send_json(self, status_code, data, cookie=None, clear_cookie=False):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        secure = self.cookie_is_secure()
        if cookie:
            ct = f"{SESSION_COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL_SECONDS}"
            if secure: ct += "; Secure"
            self.send_header("Set-Cookie", ct)
        elif clear_cookie:
            ct = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
            if secure: ct += "; Secure"
            self.send_header("Set-Cookie", ct)
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, data_dict):
        msg = f"data: {json.dumps(data_dict, ensure_ascii=False)}\n\n"
        try:
            self.wfile.write(msg.encode("utf-8"))
            self.wfile.flush()
        except Exception:
            pass

    def serve_static_file(self, filename, content_type):
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_json(404, {"error": f"Arquivo {filename} não encontrado."})

    def redirect_to(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def handle_tts(self):
        query = parse_qs(urlsplit(self.path).query)
        text = query.get("text", [""])[0]
        if not text:
            return self.send_json(400, {"error": "Texto vazio."})
        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not api_key:
            return self.send_json(500, {"error": "Chave ElevenLabs ausente."})
        voice_id = "21m00Tcm4TlvDq8ikWAM"
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
        payload = json.dumps({
            "text": text,
            "model_id": "eleven_flash_v2_5",
            "voice_settings": {"stability": 0.3, "similarity_boost": 0.85, "style": 0.0, "use_speaker_boost": True}
        }).encode("utf-8")
        req = Request(url, data=payload, headers={"Accept": "audio/mpeg", "Content-Type": "application/json", "xi-api-key": api_key}, method="POST")
        try:
            with urlopen(req, timeout=30) as response:
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(f"{len(chunk):X}\r\n".encode("utf-8"))
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
        except Exception:
            pass

    def get_session(self):
        token = _read_session_cookie(self.headers.get("Cookie", ""))
        if not token:
            return None
        if not _signed_session_is_valid(token) and SESSION_STORE.get(token) is None:
            return None
        if SESSION_STORE.get(token) is None and not _restore_session(token):
            return None
        return token

    def request_scheme(self):
        fwd = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
        return fwd if fwd in {"http", "https"} else "http"

    def cookie_is_secure(self):
        if COOKIE_SECURE_MODE == "always": return True
        if COOKIE_SECURE_MODE == "never": return False
        return self.request_scheme() == "https"

    @staticmethod
    def normalize_origin(origin):
        if not isinstance(origin, str): return None
        try:
            parsed = urlsplit(origin)
            if (parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc
                    or parsed.username or parsed.password
                    or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
                return None
            parsed.port
        except ValueError: return None
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"

    def valid_origin(self):
        origin = self.headers.get("Origin")
        normalized = self.normalize_origin(origin)
        configured = os.getenv("FRIDAY_ALLOWED_ORIGINS", "").strip()
        allowed = {self.normalize_origin(v.strip()) for v in configured.split(",") if v.strip()}
        allowed.discard(None)
        if not normalized: return False
        if configured: return normalized in allowed
        host = self.headers.get("Host", "").strip()
        expected = self.normalize_origin(f"{self.request_scheme()}://{host}")
        return bool(expected and normalized == expected)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY:
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def do_GET(self):
        try:
            self._handle_get_impl()
        except Exception:
            traceback.print_exc()
            try: self.send_json(500, {"error": "Erro interno."})
            except: pass

    def _handle_get_impl(self):
        path = urlsplit(self.path).path
        if path in {"/health", "/ping"}:
            return self.send_json(200, {"status": "ok", "service": "FRIDAY"})
        if path == "/manifest.json":
            return self.serve_static_file("manifest.json", "application/json")
        if path == "/sw.js":
            return self.serve_static_file("sw.js", "application/javascript")
        if path == "/tts":
            return self.handle_tts()
            
        # ROTA DOS GRÁFICOS (MÉTRICAS)
        if path == "/system_metrics":
            token = self.get_session()
            if not token:
                return self.send_json(401, {"error": "Não autorizado"})
            try:
                load1, _, _ = os.getloadavg()
                cpu_percent = min(100, round(load1 * 10, 1))
                with open('/proc/meminfo', 'r') as f:
                    lines = f.readlines()
                mem_total = int(lines[0].split()[1])
                mem_free = int(lines[1].split()[1])
                ram_percent = round((mem_total - mem_free) / mem_total * 100, 1)
                return self.send_json(200, {"cpu": cpu_percent, "ram": ram_percent})
            except Exception:
                return self.send_json(200, {"cpu": 0, "ram": 0})

        if path == "/status":
            token = self.get_session()
            if not token:
                return self.send_json(401, {"error": "Não autorizado"})
            global STATUS_QUEUE
            with QUEUE_LOCK:
                msgs = list(STATUS_QUEUE)
                STATUS_QUEUE.clear()
            return self.send_json(200, {"status": msgs})
        if path == "/notifications":
            token = self.get_session()
            if not token:
                return self.send_json(401, {"error": "Não autorizado"})
            global NOTIFICATION_QUEUE
            with QUEUE_LOCK:
                if NOTIFICATION_QUEUE:
                    msgs = list(NOTIFICATION_QUEUE)
                    NOTIFICATION_QUEUE.clear()
                    return self.send_json(200, {"notifications": msgs})
            return self.send_json(200, {"notifications": []})
        if path == "/":
            body = get_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/session":
            token = self.get_session()
            return self.send_json(200, {"authenticated": token is not None}, clear_cookie=token is None)
        if path == "/calendar/status":
            token = self.get_session()
            if not token:
                return self.send_json(401, {"error": "Sessão inválida.", "connected": False}, clear_cookie=True)
            return self.send_json(200, {"connected": _calendar_is_connected()})
        if path == "/calendar/events":
            return self.handle_calendar_events()
        if path == "/calendar/connect":
            token = self.get_session()
            if not token:
                return self.redirect_to("/?calendar=nosession")
            if _calendar_is_connected():
                return self.redirect_to("/?calendar=connected")
            if not (GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI):
                return self.redirect_to("/?calendar=config")
            try:
                state = _make_oauth_state(token)
            except RuntimeError:
                return self.redirect_to("/?calendar=config")
            params = {
                "client_id": GOOGLE_OAUTH_CLIENT_ID, "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
                "response_type": "code", "scope": GOOGLE_SCOPES, "state": state,
                "access_type": "offline", "include_granted_scopes": "true", "prompt": "consent",
            }
            return self.redirect_to(f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{urlencode(params)}")
        if path == "/oauth/google/callback":
            return self.handle_google_callback()
        self.send_json(404, {"error": "Página não encontrada."})

    def handle_google_callback(self):
        query = parse_qs(urlsplit(self.path).query)
        state = query.get("state", [""])[0]
        code = query.get("code", [""])[0]
        err = query.get("error", [""])[0]
        if err: return self.redirect_to("/?calendar=denied")
        if not state or not code: return self.redirect_to("/?calendar=incomplete")
        session_token = _read_session_cookie(self.headers.get("Cookie", ""))
        if not session_token or not (_signed_session_is_valid(session_token) or SESSION_STORE.get(session_token)):
            return self.redirect_to("/?calendar=nosession")
        if not isinstance(state, str) or not state.startswith("v1."):
            return self.redirect_to("/?calendar=old")
        if not _session_matches_oauth_state(state, session_token):
            if _calendar_is_connected(): return self.redirect_to("/?calendar=connected")
            return self.redirect_to("/?calendar=state")
        if not _restore_session(session_token): return self.redirect_to("/?calendar=nosession")
        if not (GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI):
            return self.redirect_to("/?calendar=config")
        oauth_err = None
        for attempt in range(3):
            try:
                _exchange_google_token({
                    "code": code, "client_id": GOOGLE_OAUTH_CLIENT_ID, "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                    "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI, "grant_type": "authorization_code",
                })
                oauth_err = None
                break
            except GoogleOAuthError as exc:
                oauth_err = exc
                if _calendar_is_connected(): return self.redirect_to("/?calendar=connected")
                if attempt < 2 and exc.code == "invalid_grant":
                    time.sleep(0.4)
                    continue
                break
            except Exception:
                return self.redirect_to("/?calendar=token")
        if oauth_err is not None:
            reason = {"redirect_uri_mismatch": "redirect", "invalid_client": "client", "unauthorized_client": "client"}.get(oauth_err.code, "token")
            if _calendar_is_connected(): reason = "connected"
            return self.redirect_to("/?calendar=" + reason)
        return self.redirect_to("/?calendar=connected")

    def handle_calendar_events(self):
        token = self.get_session()
        if not token: return self.send_json(401, {"error": "Sessão inválida."}, clear_cookie=True)
        if not _calendar_is_connected(): return self.send_json(409, {"error": "Calendar não conectado."})
        try:
            access_token = _usable_calendar_access_token()
        except GoogleOAuthError:
            return self.send_json(409, {"error": "Conexão expirou. Reconecte."})
        except Exception:
            return self.send_json(502, {"error": "Falha ao renovar conexão."})
        if not access_token: return self.send_json(409, {"error": "Conexão expirou."})
        query = parse_qs(urlsplit(self.path).query)
        rng = query.get("range", ["upcoming"])[0]
        events = _calendar_today_events(access_token) if rng == "today" else _calendar_events_query(access_token, max_results=10)
        if events is None: return self.send_json(502, {"error": "Falha ao consultar."})
        self.send_json(200, {"ok": True, "events": events, "range": rng})

    def do_POST(self):
        try:
            self._handle_post_impl()
        except Exception:
            traceback.print_exc()
            try: self.send_json(500, {"error": "Erro interno no servidor."})
            except Exception: pass

    def _handle_post_impl(self):
        path = urlsplit(self.path).path
        if not self.valid_origin():
            return self.send_json(403, {"error": "Origem não autorizada."})
        data = self.read_json()
        if data is None:
            return self.send_json(400, {"error": "Solicitação inválida ou grande."})

        if path == "/login":
            password = data.get("password")
            key = self.client_address[0]
            if not (isinstance(password, str) and auth.authenticate(password, client_key=key)):
                remaining = auth.remaining_lockout(key)
                locked = remaining > 0
                return self.send_json(429 if locked else 401, {"error": "Bloqueado." if locked else "Autenticação negada.", "locked": locked, "remaining": remaining})
            token = _make_session_cookie()
            return self.send_json(200, {"ok": True}, cookie=token)

        token = self.get_session()
        if not token:
            return self.send_json(401, {"error": "Sessão inválida."}, clear_cookie=True)

        if path == "/logout":
            SESSION_STORE.invalidate(token)
            return self.send_json(200, {"ok": True}, clear_cookie=True)

        if path == "/chat":
            command = data.get("command")
            pdf = data.get("pdf")
            pdf_name = data.get("pdf_name") or "documento.pdf"

            if not command or not isinstance(command, str) or len(command) > 2000:
                return self.send_json(400, {"error": "Comando inválido."})

            history = SESSION_STORE.get_history(token)
            session = SESSION_STORE.get(token)
            if history is None or session is None:
                return self.send_json(401, {"error": "Sessão inválida."}, clear_cookie=True)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            try:
                self._stream_chat_logic(command.strip(), history, session, pdf, pdf_name)
            except Exception as e:
                self._send_sse({"error": f"Erro crítico: {str(e)}"})
            return

        self.send_json(404, {"error": "Página não encontrada."})

    def _extract_pdf(self, pdf_b64):
        if not HAS_PYPDF2:
            return "[Módulo PDF indisponível no servidor]"
        try:
            if "," in pdf_b64:
                pdf_b64 = pdf_b64.split(",", 1)[1]
            reader = PyPDF2.PdfReader(io.BytesIO(base64.b64decode(pdf_b64)))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
            return text[:30000]
        except Exception as e:
            return f"[Erro ao ler PDF: {e}]"

    def _stream_chat_logic(self, command, history, session, pdf_b64=None, pdf_name="documento.pdf"):
        from core.ai_provider import FridayAIProvider
        provider = FridayAIProvider()
        
        if not provider.available():
            self._send_sse({"error": "IA não configurada (Groq API Key)."})
            return

        normalized = command.casefold()
        
        # 1. Checa Confirmação de Ferramenta Pendente
        pending = getattr(session, "pending_tool", None)
        if pending:
            if normalized in {"confirmar", "confirmo", "sim", "pode executar"}:
                tool_name, tool_args = pending
                session.pending_tool = None
                _push_status(f"Executando {tool_name}...")
                exec_res = execute_tool(tool_name, tool_args, user_role="owner", session=session, confirmed=True)
                reply = exec_res.get("result") if exec_res.get("ok") else exec_res.get("error")
                self._send_sse({"text": f"Comando executado: {reply}"})
                history.append({"role": "user", "content": command})
                history.append({"role": "assistant", "content": str(reply)})
                history[:] = history[-MAX_HISTORY_MESSAGES:]
                return
            elif normalized in {"cancelar", "cancela", "não", "nao", "abortar"}:
                session.pending_tool = None
                self._send_sse({"text": "Operação cancelada, chefe."})
                return

        # 2. Checa Intenção de Música Direta
        if any(k in normalized for k in ["toque", "música", "musica", "play", "pause", "pausar", "continuar"]):
            query = command
            for word in ["FRIDAY,", "FRIDAY", "toque", "a música", "a musica", "música", "musica", "coloque", "play"]:
                query = query.replace(word, "").strip()
            exec_res = execute_tool("play_music", {"query": query or normalized}, user_role="owner")
            if exec_res.get("ok"):
                r = exec_res.get("result", {})
                if r.get("action") == "pause":
                    self._send_sse({"text": "[MUSIC_ACTION:pause] Música pausada, chefe."})
                    return
                if r.get("action") == "play":
                    self._send_sse({"text": "[MUSIC_ACTION:play] Retomando a música."})
                    return
                if r.get("action") == "play_new":
                    self._send_sse({"text": f"[MUSIC_PLAY:{r.get('url')}] Tocando {query} no YouTube, chefe."})
                    return
            self._send_sse({"text": "Não consegui controlar a música."})
            return

        # 3. Processamento de PDF
        if pdf_b64:
            self._send_sse({"text": "Lendo documento PDF... "})
            text = self._extract_pdf(pdf_b64)
            command = f"Analise o documento a seguir:\n{text}\n\nComando do chefe: {command}"

        current_messages = list(history)
        current_messages.append({"role": "user", "content": command})

        # 4. Loop do Stream da IA
        for iteration in range(5):
            stream_gen = provider.chat_with_full_history_stream(current_messages, public_tool_specs())
            tool_call_accumulator = {}
            has_tool_call = False
            full_text = ""

            for chunk in stream_gen:
                if "error" in chunk:
                    self._send_sse({"error": chunk["error"]})
                    return
                if "text" in chunk:
                    full_text += chunk["text"]
                    self._send_sse({"text": chunk["text"]})
                if "tool_call_chunk" in chunk:
                    has_tool_call = True
                    tc = chunk["tool_call_chunk"]
                    idx = tc.get("index", 0)
                    if idx not in tool_call_accumulator:
                        tool_call_accumulator[idx] = {"id": tc.get("id", ""), "name": "", "arguments": ""}
                    if "function" in tc:
                        if "name" in tc["function"]:
                            tool_call_accumulator[idx]["name"] += tc["function"]["name"]
                        if "arguments" in tc["function"]:
                            tool_call_accumulator[idx]["arguments"] += tc["function"]["arguments"]

            if has_tool_call:
                self._send_sse({"text": "\n\n*[Consultando base de dados...]*\n\n"})
                tc_data = tool_call_accumulator[0]
                name = tc_data["name"]
                try:
                    args = json.loads(tc_data["arguments"])
                except Exception:
                    args = {}

                _push_status(f"Usando ferramenta: {name}...")
                exec_res = execute_tool(name, args, user_role="owner", session=session)

                if exec_res.get("status") == "pending_confirmation":
                    session.pending_tool = (name, args)
                    self._send_sse({"text": exec_res.get("message", f"Confirma '{name}'?")})
                    return

                result = exec_res.get("result") if exec_res.get("ok") else {"error": exec_res.get("error")}

                current_messages.append({
                    "role": "assistant", "content": None if not full_text else full_text,
                    "tool_calls": [{"id": tc_data["id"], "type": "function", "function": {"name": name, "arguments": tc_data["arguments"]}}]
                })
                current_messages.append({
                    "role": "tool", "tool_call_id": tc_data["id"], "content": json.dumps(result, ensure_ascii=False)
                })
                continue
            else:
                history.append({"role": "user", "content": command})
                history.append({"role": "assistant", "content": full_text})
                history[:] = history[-MAX_HISTORY_MESSAGES:]
                break

    def log_message(self, format, *args):
        pass


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    if not os.getenv("FRIDAY_PASSWORD"):
        print("ERRO: Configure FRIDAY_PASSWORD.")
        sys.exit(1)

    threading.Thread(target=_heartbeat_worker, daemon=True).start()

    port = int(os.environ.get("PORT", "10000"))
    server = ReusableThreadingHTTPServer(("0.0.0.0", port), FridayHandler)
    print(f"FRIDAY online em http://0.0.0.0:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nFRIDAY encerrada.")
        server.server_close()
