from http.server import BaseHTTPRequestHandler, HTTPServer
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit, parse_qs, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import hashlib
import hmac
import json
import os
import secrets
import time
import fcntl
import sqlite3
import base64
import io
import PyPDF2
import threading

from core.assistant import FridayAssistant
from core.auth import FridayAuth
from core.sessions import SessionStore
from core.friday_tools import (
    TOOL_REGISTRY,
    execute_tool,
    public_tool_specs,
    set_calendar_context,
    set_memory_context,
)

SESSION_COOKIE = "friday_session"
MAX_BODY = 20 * 1024 * 1024
MAX_HISTORY_MESSAGES = 10

# ========================================
# DIRETÓRIO PERSISTENTE (Render Disk)
# ========================================
DATA_DIR = os.getenv("DATA_DIR", ".")
if os.path.exists("/data") and os.access("/data", os.W_OK):
    DATA_DIR = "/data"

DB_PATH = os.path.join(DATA_DIR, "friday.db")

# ========================================
# PROATIVIDADE (HEARTBEAT)
# ========================================
NOTIFICATION_QUEUE = []

def _heartbeat_worker():
    """Verifica e-mails em background a cada 10 minutos."""
    print("FRIDAY WEB: Heartbeat thread iniciada.")
    time.sleep(30) # Espera inicial para o app subir
    while True:
        try:
            # Aqui o código pode chamar a Gmail API para checar novos e-mails
            # e injetar na NOTIFICATION_QUEUE se for urgente.
            print("FRIDAY WEB: Heartbeat checando e-mails...")
            time.sleep(600) # 10 minutos
        except Exception as e:
            print(f"FRIDAY WEB: Erro no heartbeat: {e}")
            time.sleep(60)

threading.Thread(target=_heartbeat_worker, daemon=True).start()

# ========================================
# GOOGLE CALENDAR / GMAIL — OAUTH
# ========================================
GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

GOOGLE_SCOPES = "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/gmail.readonly"

OAUTH_STATE_TTL_SECONDS = 600
GOOGLE_OWNER_KEY = "owner"
GOOGLE_TOKEN_FILE = os.getenv("FRIDAY_GOOGLE_TOKEN_FILE", "").strip() or os.path.join(
    DATA_DIR, ".friday_google_tokens.json"
)
_TOKEN_MEMORY_ONLY = False

class GoogleOAuthError(Exception):
    def __init__(self, code):
        super().__init__(code or "oauth_error")
        self.code = code or "oauth_error"

def _token_file_candidates():
    candidates = []
    for path in (GOOGLE_TOKEN_FILE, "/tmp/friday_google_tokens.json"):
        if path and path not in candidates:
            candidates.append(path)
    return candidates

def _load_google_token_store():
    for path in _token_file_candidates():
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue

        if not isinstance(data, dict):
            continue

        record = data.get(GOOGLE_OWNER_KEY)
        if not isinstance(record, dict):
            continue

        access_token = record.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            continue

        refresh_token = record.get("refresh_token")
        scope = record.get("scope")
        try:
            expires_at = float(record.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0

        return {
            GOOGLE_OWNER_KEY: {
                "access_token": access_token,
                "refresh_token": refresh_token if isinstance(refresh_token, str) and refresh_token else None,
                "expires_at": expires_at,
                "scope": scope if isinstance(scope, str) else "",
            }
        }
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
            directory = os.path.dirname(path) or "."
            os.makedirs(directory, exist_ok=True)
            with open(lock_path, "a", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    temporary = path + ".tmp"
                    with open(temporary, "w", encoding="utf-8") as handle:
                        json.dump(payload, handle)
                        handle.write("\n")
                    try:
                        os.chmod(temporary, 0o600)
                    except OSError:
                        pass
                    os.replace(temporary, path)
                    try:
                        os.chmod(path, 0o600)
                    except OSError:
                        pass
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            _TOKEN_MEMORY_ONLY = False
            return True
        except OSError:
            continue

    _TOKEN_MEMORY_ONLY = True
    print("FRIDAY WEB: não foi possível gravar os tokens do calendário em disco.")
    return False

GOOGLE_TOKEN_STORE = _load_google_token_store()

def _owner_calendar_record():
    if not _TOKEN_MEMORY_ONLY:
        loaded = _load_google_token_store()
        memory = GOOGLE_TOKEN_STORE.get(GOOGLE_OWNER_KEY)
        disk_record = loaded.get(GOOGLE_OWNER_KEY) if loaded else None
        if disk_record or not isinstance(memory, dict):
            GOOGLE_TOKEN_STORE.clear()
            GOOGLE_TOKEN_STORE.update(loaded)
    record = GOOGLE_TOKEN_STORE.get(GOOGLE_OWNER_KEY)
    return record if isinstance(record, dict) else None

def _forget_owner_calendar_tokens():
    GOOGLE_TOKEN_STORE.pop(GOOGLE_OWNER_KEY, None)
    _persist_google_token_store()

def _oauth_state_secret():
    raw = os.getenv("FRIDAY_PASSWORD", "").strip() or GOOGLE_OAUTH_CLIENT_SECRET
    if not raw:
        raise RuntimeError("Segredo ausente para assinar o estado OAuth.")
    return raw.encode("utf-8")

def _make_oauth_state(session_token):
    expiry = int(time.time()) + OAUTH_STATE_TTL_SECONDS
    nonce = secrets.token_urlsafe(16)
    binding = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    payload = f"v1.{expiry}.{binding}.{nonce}"
    signature = hmac.new(
        _oauth_state_secret(),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"

def _session_matches_oauth_state(state, session_token):
    if not isinstance(state, str) or not isinstance(session_token, str):
        return False
    parts = state.split(".")
    if len(parts) != 5 or parts[0] != "v1":
        return False
    payload = ".".join(parts[:4])
    signature = parts[4]
    expected = hmac.new(
        _oauth_state_secret(),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        expiry = int(parts[1])
    except ValueError:
        return False
    if expiry < int(time.time()):
        return False
    binding = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(parts[2], binding)

def _sign(payload):
    return hmac.new(
        _oauth_state_secret(),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

def _make_session_cookie():
    expiry = int(time.time()) + SESSION_TTL_SECONDS
    nonce = secrets.token_urlsafe(32)
    payload = f"s1.{expiry}.{nonce}"
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
        expiry = int(parts[1])
    except ValueError:
        return False
    return expiry >= int(time.time())

def _read_session_cookie(header_value):
    cookie = SimpleCookie()
    try:
        cookie.load(header_value or "")
    except (CookieError, TypeError, ValueError):
        return None
    morsel = cookie.get(SESSION_COOKIE)
    if morsel is None or not morsel.value:
        return None
    return morsel.value

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

    refresh_token = record.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token:
        return True

    access_token = record.get("access_token")
    try:
        expires_at = float(record.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0

    return (
        isinstance(access_token, str)
        and bool(access_token)
        and time.time() < expires_at - 30
    )

def _save_owner_calendar_tokens(access_token, refresh_token, expires_in, scope):
    previous = _owner_calendar_record() or {}
    if isinstance(refresh_token, str) and refresh_token:
        kept_refresh = refresh_token
    else:
        kept_refresh = previous.get("refresh_token")

    try:
        lifetime = int(expires_in)
    except (TypeError, ValueError):
        lifetime = 3600

    GOOGLE_TOKEN_STORE[GOOGLE_OWNER_KEY] = {
        "access_token": access_token,
        "refresh_token": kept_refresh if isinstance(kept_refresh, str) and kept_refresh else None,
        "expires_at": time.time() + max(60, lifetime),
        "scope": scope if isinstance(scope, str) else "",
    }
    _persist_google_token_store()

def _google_error_code(exc):
    if not isinstance(exc, HTTPError):
        return ""
    try:
        raw = exc.read(4096)
        data = json.loads(raw.decode("utf-8", "replace"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return ""
    code = data.get("error") if isinstance(data, dict) else ""
    if code in {
        "redirect_uri_mismatch",
        "invalid_client",
        "invalid_grant",
        "invalid_request",
        "unauthorized_client",
    }:
        return code
    return ""

def _exchange_google_token(form, clear_on_invalid_grant=False):
    request = Request(
        GOOGLE_OAUTH_TOKEN_URL,
        data=urlencode(form).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
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

    _save_owner_calendar_tokens(
        access_token,
        result.get("refresh_token"),
        result.get("expires_in", 3600),
        result.get("scope", ""),
    )
    return access_token

def _usable_calendar_access_token():
    record = _owner_calendar_record()
    if not record:
        return None

    access_token = record.get("access_token")
    try:
        expires_at = float(record.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0

    if isinstance(access_token, str) and access_token and time.time() < expires_at - 60:
        return access_token

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


# ========================================
# CONSULTA DE EVENTOS (usada pelas ferramentas)
# ========================================

def _calendar_events_query(access_token, time_min=None, time_max=None, max_results=10):
    params = {
        "maxResults": str(max_results),
        "singleEvents": "true",
        "orderBy": "startTime",
    }
    if time_min:
        params["timeMin"] = time_min
    else:
        params["timeMin"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if time_max:
        params["timeMax"] = time_max

    query = urlencode(params)
    request = Request(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events?" + query,
        headers={"Accept": "application/json", "Authorization": "Bearer " + access_token},
        method="GET",
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read(262144).decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None

    events = []
    for item in (result.get("items") or [])[:max_results]:
        if not isinstance(item, dict):
            continue
        start = item.get("start") if isinstance(item.get("start"), dict) else {}
        end = item.get("end") if isinstance(item.get("end"), dict) else {}
        events.append({
            "id": item.get("id", ""),
            "summary": item.get("summary") or "(sem título)",
            "start": start.get("dateTime") or start.get("date") or "",
            "end": end.get("dateTime") or end.get("date") or "",
        })
    return events

def _calendar_today_events(access_token):
    now = time.localtime()
    start_of_day = time.strftime("%Y-%m-%dT00:00:00", now)
    end_of_day = time.strftime("%Y-%m-%dT23:59:59", now)
    return _calendar_events_query(
        access_token,
        time_min=start_of_day + "Z",
        time_max=end_of_day + "Z",
        max_results=20,
    )

def _calendar_create_event(access_token, payload):
    request = Request(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "Authorization": "Bearer " + access_token,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read(262144).decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None

def _calendar_delete_event(access_token, event_id):
    request = Request(
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer " + access_token,
        },
        method="DELETE",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return {"ok": True}
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None

def _calendar_update_event(access_token, event_id, payload):
    request = Request(
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "Authorization": "Bearer " + access_token,
        },
        method="PATCH",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read(262144).decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
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
    raise RuntimeError("FRIDAY_COOKIE_SECURE deve ser auto, always ou never.")

auth = FridayAuth()
SESSION_STORE = SessionStore(ttl_seconds=SESSION_TTL_SECONDS)
friday = FridayAssistant()

# --- SISTEMA DE MEMÓRIA SQLITE ---
def _db_save_memory(key, value):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ai_memory (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO ai_memory (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

def _db_recall_memory(key):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ai_memory (key TEXT PRIMARY KEY, value TEXT)")
        search_term = f"%{key}%"
        row = conn.execute("SELECT key, value FROM ai_memory WHERE key LIKE ? OR value LIKE ?", (search_term, search_term)).fetchone()
        if row:
            return f"{row[0]}: {row[1]}"
        
        rows = conn.execute("SELECT key, value FROM ai_memory ORDER BY rowid DESC LIMIT 5").fetchall()
        if rows:
            return "Não encontrei correspondência exata, mas aqui estão as memórias mais recentes:\n" + "\n".join([f"- {r[0]}: {r[1]}" for r in rows])
        return None

set_memory_context(save_func=_db_save_memory, recall_func=_db_recall_memory)
# ----------------------------------

# ========================================
# LEITURA DO ARQUIVO HTML
# ========================================
HTML_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

def get_html():
    try:
        with open(HTML_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Erro: O arquivo index.html não foi encontrado na raiz do projeto.</h1>"


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
            cookie_text = (
                f"{SESSION_COOKIE}={cookie}; Path=/; HttpOnly; "
                f"SameSite=Lax; Max-Age={SESSION_TTL_SECONDS}"
            )
            if secure:
                cookie_text += "; Secure"
            self.send_header("Set-Cookie", cookie_text)
        elif clear_cookie:
            cookie_text = (
                f"{SESSION_COOKIE}=; Path=/; HttpOnly; "
                "SameSite=Lax; Max-Age=0"
            )
            if secure:
                cookie_text += "; Secure"
            self.send_header("Set-Cookie", cookie_text)

        self.end_headers()
        self.wfile.write(body)

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
            self.send_json(400, {"error": "Texto vazio."})
            return

        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not api_key:
            self.send_json(500, {"error": "Chave da ElevenLabs não configurada."})
            return

        voice_id = "21m00Tcm4TlvDq8ikWAM" 
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"

        payload = json.dumps({
            "text": text,
            "model_id": "eleven_flash_v2_5",
            "voice_settings": {
                "stability": 0.3,          
                "similarity_boost": 0.85,  
                "style": 0.0,              
                "use_speaker_boost": True  
            }
        }).encode("utf-8")

        req = Request(url, data=payload, headers={
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": api_key
        }, method="POST")

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
        except Exception as e:
            print(f"FRIDAY WEB: Falha na ElevenLabs: {e}")
            try:
                self.send_json(500, {"error": f"Falha na ElevenLabs: {str(e)}"})
            except:
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
        forwarded = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
        if forwarded in {"http", "https"}:
            return forwarded
        return "http"

    def cookie_is_secure(self):
        if COOKIE_SECURE_MODE == "always":
            return True
        if COOKIE_SECURE_MODE == "never":
            return False
        return self.request_scheme() == "https"

    @staticmethod
    def normalize_origin(origin):
        if not isinstance(origin, str):
            return None
        try:
            parsed = urlsplit(origin)
            if (
                parsed.scheme.lower() not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                return None
            parsed.port
        except ValueError:
            return None
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"

    def valid_origin(self):
        origin = self.headers.get("Origin")
        normalized_origin = self.normalize_origin(origin)
        configured_origins = os.getenv("FRIDAY_ALLOWED_ORIGINS", "").strip()

        allowed_origins = {
            self.normalize_origin(value.strip())
            for value in configured_origins.split(",")
            if value.strip()
        }
        allowed_origins.discard(None)

        if not normalized_origin:
            return False

        if configured_origins:
            return normalized_origin in allowed_origins

        host = self.headers.get("Host", "").strip()
        expected_origin = self.normalize_origin(f"{self.request_scheme()}://{host}")
        return bool(expected_origin and normalized_origin == expected_origin)

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
        except (ValueError, UnicodeDecodeError):
            return None

    def do_GET(self):
        path = urlsplit(self.path).path

        if path == "/tts":
            self.handle_tts()
            return

        if path == "/notifications":
            token = self.get_session()
            if not token:
                return self.send_json(401, {"error": "Não autorizado"})
            global NOTIFICATION_QUEUE
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
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/session":
            token = self.get_session()
            self.send_json(200, {"authenticated": token is not None}, clear_cookie=token is None)
            return

        if path == "/calendar/status":
            token = self.get_session()
            if not token:
                self.send_json(401, {"error": "Sessão inválida. Entre novamente.", "connected": False}, clear_cookie=True)
                return
            self.send_json(200, {"connected": _calendar_is_connected()})
            return

        if path == "/calendar/events":
            self.handle_calendar_events()
            return

        if path == "/calendar/connect":
            token = self.get_session()

            if not token:
                self.redirect_to("/?calendar=nosession")
                return

            if _calendar_is_connected():
                self.redirect_to("/?calendar=connected")
                return

            if not (GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI):
                self.redirect_to("/?calendar=config")
                return

            try:
                state = _make_oauth_state(token)
            except RuntimeError:
                self.redirect_to("/?calendar=config")
                return

            params = {
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
                "response_type": "code",
                "scope": GOOGLE_SCOPES,
                "state": state,
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
            }

            authorization_url = f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"
            self.redirect_to(authorization_url)
            return

        if path == "/oauth/google/callback":
            self.handle_google_callback()
            return

        self.send_json(404, {"error": "Página não encontrada."})

    def handle_google_callback(self):
        query = parse_qs(urlsplit(self.path).query)
        state = query.get("state", [""])[0]
        code = query.get("code", [""])[0]
        google_error = query.get("error", [""])[0]

        if google_error:
            self.redirect_to("/?calendar=denied")
            return

        if not state or not code:
            self.redirect_to("/?calendar=incomplete")
            return

        session_token = _read_session_cookie(self.headers.get("Cookie", ""))
        if not session_token or not (_signed_session_is_valid(session_token) or SESSION_STORE.get(session_token)):
            self.redirect_to("/?calendar=nosession")
            return

        if not isinstance(state, str) or not state.startswith("v1."):
            self.redirect_to("/?calendar=old")
            return

        if not _session_matches_oauth_state(state, session_token):
            if _calendar_is_connected():
                self.redirect_to("/?calendar=connected")
                return
            self.redirect_to("/?calendar=state")
            return

        if not _restore_session(session_token):
            self.redirect_to("/?calendar=nosession")
            return

        if not (GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI):
            self.redirect_to("/?calendar=config")
            return

        oauth_error = None
        for attempt in range(3):
            try:
                _exchange_google_token({
                    "code": code,
                    "client_id": GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                    "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
                    "grant_type": "authorization_code",
                })
                oauth_error = None
                break
            except GoogleOAuthError as exc:
                oauth_error = exc
                if _calendar_is_connected():
                    self.redirect_to("/?calendar=connected")
                    return
                if attempt < 2 and exc.code == "invalid_grant":
                    time.sleep(0.4)
                    continue
                break
            except (URLError, TimeoutError, ValueError, json.JSONDecodeError):
                self.redirect_to("/?calendar=token")
                return
            except Exception:
                self.redirect_to("/?calendar=token")
                return

        if oauth_error is not None:
            reason = {
                "redirect_uri_mismatch": "redirect",
                "invalid_client": "client",
                "unauthorized_client": "client",
            }.get(oauth_error.code, "token")
            if _calendar_is_connected():
                reason = "connected"
            self.redirect_to("/?calendar=" + reason)
            return

        self.redirect_to("/?calendar=connected")

    def handle_calendar_events(self):
        token = self.get_session()
        if not token:
            self.send_json(401, {"error": "Sessão inválida. Entre novamente."}, clear_cookie=True)
            return

        if not _calendar_is_connected():
            self.send_json(409, {"error": "Google Calendar ainda não está conectado."})
            return

        try:
            access_token = _usable_calendar_access_token()
        except GoogleOAuthError:
            self.send_json(409, {"error": "A conexão do Google Calendar expirou. Conecte novamente."})
            return
        except (URLError, TimeoutError, ValueError, json.JSONDecodeError):
            self.send_json(502, {"error": "Não foi possível renovar a conexão do Google Calendar."})
            return

        if not access_token:
            self.send_json(409, {"error": "A conexão do Google Calendar expirou. Conecte novamente."})
            return

        query = parse_qs(urlsplit(self.path).query)
        time_range = query.get("range", ["upcoming"])[0]

        if time_range == "today":
            events = _calendar_today_events(access_token)
        else:
            events = _calendar_events_query(access_token, max_results=10)

        if events is None:
            self.send_json(502, {"error": "Não foi possível consultar os eventos do calendário."})
            return

        self.send_json(200, {"ok": True, "events": events, "range": time_range})

    def _handle_pending_tool_confirmation(self, session, command, normalized):
        pending = getattr(session, "pending_tool", None)
        if not pending:
            return False, ""

        if normalized in {"confirmar", "confirmo", "sim", "pode executar"}:
            tool_name, tool_args = pending
            session.pending_tool = None
            result = execute_tool(tool_name, tool_args, user_role="owner", session=session, confirmed=True)
            
            if result.get("ok"):
                payload = result.get("result")
                
                if isinstance(payload, dict) and "error" in payload:
                    return True, payload["error"]
                
                if tool_name == "create_calendar_event" and isinstance(payload, dict):
                    return True, f"Evento criado, chefe: '{payload.get('summary')}' de {payload.get('start')} até {payload.get('end')}."
                if tool_name == "delete_calendar_event":
                    return True, "Evento excluído com sucesso, chefe."
                if tool_name == "update_calendar_event":
                    return True, "Evento atualizado com sucesso, chefe."
                if tool_name in ("send_email", "reply_to_email"):
                    return True, str(payload)
                    
                return True, f"Ferramenta '{tool_name}' executada com sucesso."
            return True, result.get("error", "Não foi possível executar a ferramenta.")

        if normalized in {"cancelar", "cancela", "não", "nao", "abortar"}:
            session.pending_tool = None
            return True, "Operação cancelada, chefe."

        return False, ""

    def _try_direct_calendar_action(self, command, normalized, session):
        if not _calendar_is_connected():
            return False, ""

        is_delete = any(t in normalized for t in ["cancele", "cancele o", "apague", "delete", "deletar", "remova"])
        if not is_delete:
            return False, ""

        try:
            access_token = _usable_calendar_access_token()
        except Exception:
            return True, "Não consegui renovar o acesso ao calendário, chefe."

        if not access_token:
            return True, "O calendário não está conectado no momento."

        events = _calendar_events_query(access_token, max_results=20)
        if not events:
            return True, "Não encontrei nenhum evento na sua agenda para alterar ou excluir."

        ignore_words = ["friday", "friday,", "cancele", "cancele o", "apague", "delete", "deletar", "remova", "a", "o", "reunião", "reuniao", "de", "da", "do", "meu", "minha"]
        query_words = [w for w in normalized.replace(",", "").replace(".", "").split() if w not in ignore_words]
        
        target_event = None
        for evt in events:
            evt_name = evt.get("summary", "").casefold()
            if evt_name and any(w in evt_name for w in query_words):
                target_event = evt
                break
        
        if not target_event:
            for evt in events:
                evt_name = evt.get("summary", "").casefold()
                if evt_name and evt_name in normalized:
                    target_event = evt
                    break

        if target_event:
            session.pending_tool = ("delete_calendar_event", {"event_id": target_event["id"]})
            return True, f"Chefe, encontrei o evento '{target_event['summary']}'. Deseja confirmar a exclusão? (responda 'confirmar' ou 'cancelar')"
        
        return True, "Não consegui identificar qual evento você quer cancelar. Por favor, verifique o nome na sua agenda."

    def _try_direct_calendar_intent(self, command, normalized, session):
        if not _calendar_is_connected():
            return False, ""

        action_handled, action_reply = self._try_direct_calendar_action(command, normalized, session)
        if action_handled:
            return True, action_reply

        triggers_today = ("agenda de hoje", "o que tenho hoje", "compromissos de hoje", "eventos de hoje", "tenho algo hoje")
        triggers_upcoming = ("próximos compromissos", "proximos compromissos", "próximos eventos", "proximos eventos", "minha agenda", "o que está agendado")

        is_today = any(t in normalized for t in triggers_today)
        is_upcoming = any(t in normalized for t in triggers_upcoming)

        if not (is_today or is_upcoming):
            return False, ""

        try:
            access_token = _usable_calendar_access_token()
        except Exception:
            return True, "Não consegui renovar o acesso ao calendário, chefe."

        if not access_token:
            return True, "O calendário não está conectado no momento."

        if is_today:
            events = _calendar_today_events(access_token)
            label = "hoje"
        else:
            events = _calendar_events_query(access_token, max_results=5)
            label = "nos próximos dias"

        if events is None:
            return True, "Não consegui consultar sua agenda agora, chefe."

        if not events:
            return True, f"Nenhum compromisso agendado para {label}, chefe."

        from datetime import datetime as _dt
        lines = [f"Seus compromissos para {label}:"]
        for evt in events[:7]:
            start_raw = evt.get("start", "")
            try:
                dt = _dt.fromisoformat(start_raw.replace("Z", "+00:00"))
                when = dt.strftime("%d/%m às %H:%M")
            except Exception:
                when = start_raw or "horário não definido"
            lines.append(f"• {evt['summary']} — {when}")
        return True, "\n".join(lines)

    def _handle_pdf(self, pdf_base64, command, history):
        if "," in pdf_base64:
            pdf_base64 = pdf_base64.split(",")[1]
            
        pdf_bytes = base64.b64decode(pdf_base64)
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        
        text = ""
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
            
        from core.ai_provider import FridayAIProvider
        provider = FridayAIProvider()
        
        # Map-Reduce para PDFs Gigantes
        if len(text) > 20000:
            chunk_size = 15000
            chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
            
            summaries = []
            for i, chunk in enumerate(chunks):
                print(f"FRIDAY WEB: Resumindo parte {i+1} de {len(chunks)} do PDF...")
                prompt = f"Resuma o seguinte trecho de um documento longo de forma clara:\n\n{chunk}"
                res = provider.generate(message=prompt)
                if res:
                    summaries.append(res)
            
            combined_summary = "\n\n".join(summaries)
            context_msg = f"O usuário anexou um PDF muito longo. Aqui está o resumo de cada parte:\n\n{combined_summary}\n\nComando do usuário: {command}"
            return provider.generate(message=context_msg) or "Não consegui ler o PDF completo."
        else:
            context_msg = f"O usuário anexou um PDF. Aqui está o conteúdo extraído:\n\n{text}\n\nComando do usuário: {command}"
            return provider.generate(message=context_msg) or "Não consegui ler o PDF."

    def _handle_vision(self, image_base64, command, history):
        from core.ai_provider import FridayAIProvider
        provider = FridayAIProvider()
        return provider.chat_with_vision(command, image_base64, history=history)

    def _handle_chat_with_tools(self, clean_command, history, session):
        normalized = clean_command.casefold()
        response = None

        if "briefing" in normalized or "resumo matinal" in normalized:
            exec_res = execute_tool("get_morning_briefing", {"city": "Criciúma"}, user_role="owner")
            if exec_res.get("ok"):
                return str(exec_res.get("result"))
            else:
                return "Não consegui acessar os dados para o briefing matinal, chefe."

        if "toque" in normalized or "música" in normalized or "musica" in normalized or "play" in normalized:
            query = clean_command
            for word in ["FRIDAY,", "FRIDAY", "toque", "a música", "a musica", "música", "musica", "coloque", "play"]:
                query = query.replace(word, "").strip()
            
            if query:
                exec_res = execute_tool("play_music", {"query": query}, user_role="owner")
                if exec_res.get("ok"):
                    url = exec_res.get("result", {}).get("url", "")
                    return f"[PLAY_MUSIC:{url}] Tocando {query} no YouTube, chefe."
                else:
                    return exec_res.get("error", "Não consegui tocar a música.")
            return "Qual música deseja ouvir, chefe?"

        pending_handled, pending_reply = self._handle_pending_tool_confirmation(session, clean_command, normalized)
        if pending_handled:
            return pending_reply

        calendar_handled, calendar_reply = self._try_direct_calendar_intent(clean_command, normalized, session)
        if calendar_handled:
            return calendar_reply

        force_llm = any(t in normalized for t in ["lembre-se", "salve na memória", "memorize", "qual é minha", "o que você sabe sobre", "reserva", "reserve", "email", "e-mail", "caixa de entrada", "cancela", "cancele", "muda", "altere", "remarque", "apaga", "deleta", "pesquise", "leia", "resuma", "artigo", "site"])
        
        if not force_llm:
            intention = friday.interpret(clean_command)
            if session.pending_action is not None:
                if normalized == "atualizar lembrança":
                    intention = "confirm_memory_update"
                elif normalized == "manter ambas":
                    intention = "confirm_memory_keep_both"
                elif normalized == "cancelar operação":
                    intention = "cancel_pending"

            response = friday.execute(
                intention,
                clean_command,
                "owner",
                history=history,
                session=session,
            )

            if response is None:
                return None

            if intention != "unknown":
                return str(response)

        from core.ai_provider import FridayAIProvider
        provider = FridayAIProvider()
        if not provider.available():
            return str(response) if response else "A IA não está configurada."

        current_messages = list(history)
        current_messages.append({"role": "user", "content": clean_command})
        
        max_iterations = 5
        
        for iteration in range(max_iterations):
            try:
                ai_result = provider.chat_with_full_history(current_messages, public_tool_specs())
            except Exception as e:
                print(f"FRIDAY WEB: Erro no loop de IA: {e}")
                break
                
            if not ai_result or "error" in ai_result:
                break
                
            if "tool_call" in ai_result:
                tool_name = ai_result["tool_call"].get("name")
                tool_args = ai_result["tool_call"].get("arguments", {})
                raw_args = ai_result["tool_call"].get("raw_arguments", "{}")
                tool_id = ai_result["tool_call"].get("id", "call_unknown")
                
                exec_result = execute_tool(tool_name, tool_args, user_role="owner", session=session)
                
                if exec_result.get("status") == "pending_confirmation":
                    session.pending_tool = (tool_name, tool_args)
                    return exec_result.get("message", f"Preciso de confirmação para '{tool_name}'.")

                if not exec_result.get("ok") or (isinstance(exec_result.get("result"), dict) and "error" in exec_result["result"]):
                    error_msg = exec_result.get("error") or exec_result["result"].get("error")
                    tool_result = {"error": f"Falha na ferramenta: {error_msg}. Tente novamente ou use outra."}
                else:
                    tool_result = exec_result.get("result")

                current_messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_id,
                        "type": "function",
                        "function": {"name": tool_name, "arguments": raw_args}
                    }]
                })
                current_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": json.dumps(tool_result, ensure_ascii=False)
                })
                
            elif "text" in ai_result:
                final_text = ai_result["text"]
                history.append({"role": "user", "content": clean_command})
                history.append({"role": "assistant", "content": final_text})
                history[:] = history[-MAX_HISTORY_MESSAGES:]
                return final_text
            else:
                break

        history.append({"role": "user", "content": clean_command})
        fallback_text = provider.generate(message=clean_command, history=history)
        if fallback_text:
            history.append({"role": "assistant", "content": fallback_text})
            history[:] = history[-MAX_HISTORY_MESSAGES:]
            return fallback_text

        return "Desculpe, chefe. Não consegui concluir o raciocínio complexo desta vez."

    def do_POST(self):
        path = urlsplit(self.path).path

        if not self.valid_origin():
            self.send_json(403, {"error": "Origem não autorizada."})
            return

        data = self.read_json()

        if data is None:
            self.send_json(400, {"error": "Solicitação inválida ou muito grande (limite 20MB)."})
            return

        if path == "/login":
            password = data.get("password")
            client_key = self.client_address[0]

            authenticated = (isinstance(password, str) and auth.authenticate(password, client_key=client_key))

            if not authenticated:
                remaining = auth.remaining_lockout(client_key)
                locked = remaining > 0
                self.send_json(
                    429 if locked else 401,
                    {
                        "error": ("Acesso temporariamente bloqueado." if locked else "Autenticação negada."),
                        "locked": locked,
                        "remaining": remaining,
                    },
                )
                return

            token = _make_session_cookie()
            self.send_json(200, {"ok": True}, cookie=token)
            return

        token = self.get_session()

        if not token:
            self.send_json(401, {"error": "Sessão inválida. Entre novamente."}, clear_cookie=True)
            return

        if path == "/logout":
            SESSION_STORE.invalidate(token)
            self.send_json(200, {"ok": True}, clear_cookie=True)
            return

        if path == "/chat":
            command = data.get("command")
            image = data.get("image")
            pdf = data.get("pdf")

            if not command or not isinstance(command, str) or len(command) > 2000:
                self.send_json(400, {"error": "Comando vazio ou muito longo."})
                return

            try:
                clean_command = command.strip()
                history = SESSION_STORE.get_history(token)
                session = SESSION_STORE.get(token)

                if history is None or session is None:
                    self.send_json(401, {"error": "Sessão inválida. Entre novamente."}, clear_cookie=True)
                    return

                if image:
                    response_text = self._handle_vision(image, clean_command, history)
                elif pdf:
                    response_text = self._handle_pdf(pdf, clean_command, history)
                else:
                    response_text = self._handle_chat_with_tools(clean_command, history, session)

                if response_text is None:
                    SESSION_STORE.invalidate(token)
                    self.send_json(200, {"response": "FRIDAY encerrada.", "closed": True}, clear_cookie=True)
                    return

                self.send_json(200, {"response": str(response_text), "closed": False})

            except Exception:
                import traceback
                traceback.print_exc()
                self.send_json(500, {"error": "Erro interno ao processar comando."})
            return

        self.send_json(404, {"error": "Página não encontrada."})

    def log_message(self, format, *args):
        safe_args = []
        for arg in args:
            if isinstance(arg, str) and "?" in arg:
                head, _, tail = arg.partition("?")
                http_bits = tail.partition(" HTTP/")
                arg = head + (" HTTP/" + http_bits[2] if http_bits[1] else "")
            safe_args.append(arg)
        try:
            print("FRIDAY WEB:", format % tuple(safe_args))
        except Exception:
            print("FRIDAY WEB: requisição")


if __name__ == "__main__":
    if not os.getenv("FRIDAY_PASSWORD"):
        raise RuntimeError("Configure o segredo FRIDAY_PASSWORD no servidor.")

    port = int(os.environ.get("PORT", "5000"))

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True

    server = ReusableHTTPServer(("0.0.0.0", port), FridayHandler)
    print(f"FRIDAY online em http://0.0.0.0:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nFRIDAY encerrada.")
        server.server_close()
