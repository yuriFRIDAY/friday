import os
import sys
import json
import time
import hmac
import hashlib
import base64
import io
import re
import secrets
import traceback
import threading
import logging
import shutil
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request as URLRequest, urlopen
from urllib.error import HTTPError, URLError

import psycopg2
import psycopg2.extras

from fastapi import FastAPI, Request, Response, HTTPException, Cookie
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse, FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

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

logger = logging.getLogger("friday.main")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Pega o caminho absoluto da pasta onde o app.py está
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ... (código do Sentry, FastAPI, Auth, DB, Google OAuth - Mantenha o seu original aqui) ...
# Vou encurtar os comentários para focar na correção das rotas:

SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.1, environment=os.getenv("ENV", "production"))
    except Exception:
        pass

app = FastAPI(title="FRIDAY API")
auth = FridayAuth()
SESSION_STORE = SessionStore(ttl_seconds=1800)
friday = FridayAssistant()

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

STATUS_QUEUE = []
NOTIFICATION_QUEUE = []
QUEUE_LOCK = threading.Lock()
_CHAT_RATE = defaultdict(list)
_RATE_LOCK = threading.Lock()
_TOKEN_LOCK = threading.Lock()

MAX_IMAGE_BYTES = 8_000_000
MAX_PDF_BYTES = 12_000_000

TOOL_STATUS = {
    "get_weather": "🌤 Consultando clima...",
    "get_calendar_events": "📅 Consultando agenda...",
    "create_calendar_event": "📅 Criando evento...",
    "update_calendar_event": "✏ Atualizando evento...",
    "delete_calendar_event": "🗑 Removendo evento...",
    "get_news": "📰 Buscando notícias...",
    "search_web": "🔍 Pesquisando na web...",
    "deep_search": "🔎 Análise profunda...",
    "read_webpage": "🌐 Lendo página...",
    "play_music": "🎵 Buscando áudio...",
    "get_crypto_price": "💰 Consultando cotação...",
    "send_email": "✉ Enviando e-mail...",
    "reply_to_email": "✉ Respondendo e-mail...",
    "read_emails": "📧 Lendo e-mails...",
    "read_email_full": "📧 Abrindo e-mail...",
    "get_system_status": "⚙ Verificando sistema...",
    "get_morning_briefing": "☀ Preparando briefing...",
    "find_free_slots": "📅 Buscando horários...",
    "create_task": "📋 Criando tarefa...",
    "list_tasks": "📋 Listando tarefas...",
    "complete_task": "✓ Concluindo tarefa...",
    "save_memory": "🧠 Salvando memória...",
    "recall_memory": "🧠 Recuperando memória...",
}

# --- Middlewares e DB (Mantendo o seu original) ---
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(self)"
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
app.add_middleware(SecurityHeadersMiddleware)

def _push_status(text):
    global STATUS_QUEUE
    if not text: return
    with QUEUE_LOCK:
        STATUS_QUEUE.append(text)
        if len(STATUS_QUEUE) > 20: STATUS_QUEUE.pop(0)

def _push_notification(text):
    if not text: return
    global NOTIFICATION_QUEUE
    with QUEUE_LOCK:
        NOTIFICATION_QUEUE.append(text)
        if len(NOTIFICATION_QUEUE) > 20: NOTIFICATION_QUEUE.pop(0)

def _check_chat_rate(session_token):
    now = time.time()
    with _RATE_LOCK:
        calls = [t for t in _CHAT_RATE[session_token] if now - t < 60]
        if len(calls) >= 30:
            _CHAT_RATE[session_token] = calls
            return False
        calls.append(now)
        _CHAT_RATE[session_token] = calls
        return True

def get_db():
    if not DATABASE_URL:
        import sqlite3
        conn = sqlite3.connect("friday.db")
        conn.row_factory = sqlite3.Row
        return conn
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db(); cursor = conn.cursor()
        if DATABASE_URL:
            cursor.execute("CREATE TABLE IF NOT EXISTS ai_memory (key TEXT PRIMARY KEY, value TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS tasks (id SERIAL PRIMARY KEY, description TEXT, status TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS chat_history (id SERIAL PRIMARY KEY, session_token TEXT, role TEXT, content TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS tool_audit (id SERIAL PRIMARY KEY, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP, tool TEXT, args_summary TEXT, ok BOOLEAN, error TEXT)")
        else:
            cursor.execute("CREATE TABLE IF NOT EXISTS ai_memory (key TEXT PRIMARY KEY, value TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, description TEXT, status TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, session_token TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS tool_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP, tool TEXT, args_summary TEXT, ok BOOLEAN, error TEXT)")
        conn.commit(); cursor.close(); conn.close()
    except Exception as e:
        logger.warning(f"AVISO: Erro ao inicializar DB: {e}")

def _prune_old_data():
    try:
        conn = get_db(); cursor = conn.cursor()
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        if DATABASE_URL:
            cursor.execute("DELETE FROM chat_history WHERE timestamp < %s", (cutoff,))
            cursor.execute("DELETE FROM ai_memory WHERE key NOT IN (SELECT key FROM ai_memory ORDER BY ctid DESC LIMIT 500)")
            cursor.execute("DELETE FROM tool_audit WHERE ts < %s", (cutoff,))
        else:
            cursor.execute("DELETE FROM chat_history WHERE timestamp < ?", (cutoff.isoformat(),))
            cursor.execute("DELETE FROM ai_memory WHERE key NOT IN (SELECT key FROM ai_memory ORDER BY rowid DESC LIMIT 500)")
            cursor.execute("DELETE FROM tool_audit WHERE ts < ?", (cutoff.isoformat(),))
        conn.commit(); cursor.close(); conn.close()
    except Exception as e:
        logger.warning(f"Pruning falhou: {e}")

def _audit_tool(tool_name, args, ok, error=None):
    try:
        conn = get_db(); cur = conn.cursor()
        summary = json.dumps({k: str(v)[:100] for k, v in (args or {}).items()}, ensure_ascii=False)[:500]
        if DATABASE_URL:
            cur.execute("INSERT INTO tool_audit (tool, args_summary, ok, error) VALUES (%s, %s, %s, %s)", (tool_name, summary, bool(ok), (error or "")[:500] or None))
        else:
            cur.execute("INSERT INTO tool_audit (tool, args_summary, ok, error) VALUES (?, ?, ?, ?)", (tool_name, summary, 1 if ok else 0, (error or "")[:500] or None))
        conn.commit(); cur.close(); conn.close()
    except Exception: pass

def _schedule_prune():
    while True:
        time.sleep(6 * 60 * 60); _prune_old_data()

def _bootstrap_db():
    init_db(); _prune_old_data()

threading.Thread(target=_bootstrap_db, daemon=True).start()
threading.Thread(target=_schedule_prune, daemon=True).start()

def _db_save_memory(key, value):
    conn = get_db(); cursor = conn.cursor()
    try:
        if DATABASE_URL: cursor.execute("INSERT INTO ai_memory (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, value))
        else: cursor.execute("INSERT OR REPLACE INTO ai_memory (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally: cursor.close(); conn.close()

def _db_recall_memory(key):
    conn = get_db(); cursor = conn.cursor()
    try:
        if DATABASE_URL: cursor.execute("SELECT key, value FROM ai_memory WHERE key ILIKE %s OR value ILIKE %s LIMIT 1", (f"%{key}%", f"%{key}%"))
        else: cursor.execute("SELECT key, value FROM ai_memory WHERE key LIKE ? OR value LIKE ? LIMIT 1", (f"%{key}%", f"%{key}%"))
        row = cursor.fetchone()
        return f"{row[0]}: {row[1]}" if row else None
    finally: cursor.close(); conn.close()

def _db_delete_memory(key):
    conn = get_db(); cursor = conn.cursor()
    try:
        if DATABASE_URL: cursor.execute("DELETE FROM ai_memory WHERE key ILIKE %s", (f"%{key}%",))
        else: cursor.execute("DELETE FROM ai_memory WHERE key LIKE ?", (f"%{key}%",))
        conn.commit()
        return cursor.rowcount > 0
    finally: cursor.close(); conn.close()

def _db_get_recent_memories():
    conn = get_db(); cursor = conn.cursor()
    try:
        if DATABASE_URL: cursor.execute("SELECT key, value FROM ai_memory ORDER BY ctid DESC LIMIT 5")
        else: cursor.execute("SELECT key, value FROM ai_memory ORDER BY rowid DESC LIMIT 5")
        return [f"{r[0]}: {r[1]}" for r in cursor.fetchall()]
    except Exception: return []
    finally: cursor.close(); conn.close()

def _db_save_message(session_token, role, content):
    conn = get_db(); cursor = conn.cursor()
    try:
        if DATABASE_URL: cursor.execute("INSERT INTO chat_history (session_token, role, content) VALUES (%s, %s, %s)", (session_token, role, content))
        else: cursor.execute("INSERT INTO chat_history (session_token, role, content) VALUES (?, ?, ?)", (session_token, role, content))
        conn.commit()
    except Exception: pass
    finally: cursor.close(); conn.close()

def _db_get_history(session_token):
    conn = get_db(); cursor = conn.cursor()
    try:
        if DATABASE_URL: cursor.execute("SELECT role, content FROM chat_history WHERE session_token = %s ORDER BY id DESC LIMIT 100", (session_token,))
        else: cursor.execute("SELECT role, content FROM chat_history WHERE session_token = ? ORDER BY id DESC LIMIT 100", (session_token,))
        rows = list(cursor.fetchall()); rows.reverse()
        history = []
        for r in rows:
            if DATABASE_URL: history.append({"role": r[0], "content": r[1]})
            else: history.append({"role": r["role"], "content": r["content"]})
        return history
    except Exception: return []
    finally: cursor.close(); conn.close()

set_memory_context(save_func=_db_save_memory, recall_func=_db_recall_memory, delete_func=_db_delete_memory)

# --- Rotas de Arquivos Estáticos ---
# ESSAS SÃO AS ROTAS CRUCIAIS PARA O DESIGN FUNCIONAR!
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    try:
        with open(os.path.join(BASE_DIR, "index.html"), "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "<h1>Erro: index.html não encontrado.</h1>"

@app.get("/styles.css")
async def serve_css():
    return FileResponse(os.path.join(BASE_DIR, "styles.css"), media_type="text/css")

@app.get("/app.js")
async def serve_app_js():
    return FileResponse(os.path.join(BASE_DIR, "app.js"), media_type="application/javascript")

@app.get("/reactor.js")
async def serve_reactor_js():
    return FileResponse(os.path.join(BASE_DIR, "reactor.js"), media_type="application/javascript")

@app.get("/manifest.json")
async def serve_manifest():
    return JSONResponse(content=json.load(open(os.path.join(BASE_DIR, "manifest.json"), "r")))

@app.get("/sw.js")
async def serve_sw():
    return Response(content=open(os.path.join(BASE_DIR, "sw.js"), "r").read(), media_type="application/javascript")

# --- Rotas da API ---
class LoginData(BaseModel): password: str
class ChatData(BaseModel):
    command: str
    pdf: Optional[str] = None
    pdf_name: Optional[str] = "documento.pdf"
    image: Optional[str] = None
    image_name: Optional[str] = "image.jpg"
class TTSData(BaseModel): text: str = ""

@app.get("/health")
async def health_check():
    checks = {"db": False, "groq": False, "disk": False}
    try:
        conn = get_db(); cur = conn.cursor(); cur.execute("SELECT 1"); cur.close(); conn.close(); checks["db"] = True
    except Exception: pass
    checks["groq"] = bool(os.getenv("GROQ_API_KEY", "").strip())
    try:
        st = shutil.disk_usage("/"); checks["disk"] = (st.free / st.total) > 0.05
    except Exception: checks["disk"] = True
    ok = all(checks.values())
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "degraded", "checks": checks})

@app.post("/tts")
async def text_to_speech_post(data: TTSData, friday_session: Optional[str] = Cookie(None)): return await _tts_impl(data.text, friday_session)
@app.get("/tts")
async def text_to_speech_get(text: str = "", friday_session: Optional[str] = Cookie(None)): return await _tts_impl(text, friday_session)

async def _tts_impl(text: str, friday_session):
    try:
        if not friday_session: raise HTTPException(status_code=401)
        if not text: raise HTTPException(status_code=400)
        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not api_key: return Response(status_code=204)
        voice_id = "21m00Tcm4TlvDq8ikWAM"
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
        payload = json.dumps({"text": text, "model_id": "eleven_flash_v2_5", "voice_settings": {"stability": 0.3, "similarity_boost": 0.85, "style": 0.0, "use_speaker_boost": True}}).encode("utf-8")
        req = URLRequest(url, data=payload, headers={"Accept": "audio/mpeg", "Content-Type": "application/json", "xi-api-key": api_key}, method="POST")
        try:
            response = urlopen(req, timeout=30)
            def audio_stream():
                while True:
                    chunk = response.read(4096)
                    if not chunk: break
                    yield chunk
            return StreamingResponse(audio_stream(), media_type="audio/mpeg")
        except Exception: return Response(status_code=204)
    except HTTPException: raise
    except Exception: return Response(status_code=204)

@app.get("/status")
async def get_status(friday_session: Optional[str] = Cookie(None)):
    if not friday_session: raise HTTPException(401)
    global STATUS_QUEUE
    with QUEUE_LOCK:
        msgs = list(STATUS_QUEUE); STATUS_QUEUE.clear()
    return {"status": msgs}

@app.get("/notifications")
async def get_notifications(friday_session: Optional[str] = Cookie(None)):
    if not friday_session: raise HTTPException(401)
    global NOTIFICATION_QUEUE
    with QUEUE_LOCK:
        if NOTIFICATION_QUEUE:
            msgs = list(NOTIFICATION_QUEUE); NOTIFICATION_QUEUE.clear()
            return {"notifications": msgs}
    return {"notifications": []}

@app.post("/login")
async def login(data: LoginData, response: Response, request: Request):
    try:
        client_ip = request.client.host if request.client else "0.0.0.0"
        if not auth.authenticate(data.password, client_key=client_ip): raise HTTPException(401, "Senha incorreta.")
        token = SESSION_STORE.create()
        secure_cookie = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
        response.set_cookie(key="friday_session", value=token, httponly=True, secure=secure_cookie, samesite="lax", max_age=1800, path="/")
        return {"ok": True}
    except HTTPException as he: raise he
    except Exception as e: raise HTTPException(500, str(e))

@app.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key="friday_session", path="/", httponly=True, samesite="lax")
    return {"ok": True}

@app.get("/session")
async def check_session(friday_session: Optional[str] = Cookie(None)):
    if friday_session and SESSION_STORE.get(friday_session): return {"authenticated": True}
    return {"authenticated": False}

@app.get("/history")
async def get_history(friday_session: Optional[str] = Cookie(None)):
    if not friday_session: raise HTTPException(401)
    return {"history": _db_get_history(friday_session)}

@app.get("/system_metrics")
async def get_metrics(friday_session: Optional[str] = Cookie(None)):
    if not friday_session: raise HTTPException(401)
    try:
        load1, _, _ = os.getloadavg(); cpu_percent = min(100, round(load1 * 10, 1))
    except Exception: cpu_percent = 0
    ram_percent = 0
    try:
        with open('/proc/meminfo', 'r') as f: lines = f.readlines()
        mem_total = int(lines[0].split()[1]); mem_avail = int(lines[2].split()[1]) if len(lines) > 2 else int(lines[1].split()[1])
        ram_percent = round((mem_total - mem_avail) / mem_total * 100, 1)
    except Exception:
        try:
            import psutil; ram_percent = psutil.virtual_memory().percent
        except ImportError: ram_percent = 0
    return {"cpu": cpu_percent, "ram": ram_percent}

@app.get("/calendar/connect")
async def calendar_connect(friday_session: Optional[str] = Cookie(None)):
    if not friday_session: return RedirectResponse(url="/?calendar=nosession")
    return RedirectResponse(url=f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{urlencode({'client_id': GOOGLE_OAUTH_CLIENT_ID, 'redirect_uri': GOOGLE_OAUTH_REDIRECT_URI, 'response_type': 'code', 'scope': GOOGLE_SCOPES, 'access_type': 'offline', 'prompt': 'consent'})}")

@app.get("/oauth/google/callback")
async def google_callback(request: Request):
    code = request.query_params.get("code")
    if not code: return RedirectResponse(url="/?calendar=denied")
    try:
        req = URLRequest(GOOGLE_OAUTH_TOKEN_URL, data=urlencode({"code": code, "client_id": GOOGLE_OAUTH_CLIENT_ID, "client_secret": GOOGLE_OAUTH_CLIENT_SECRET, "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI, "grant_type": "authorization_code"}).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        with urlopen(req, timeout=15) as resp: res = json.loads(resp.read().decode())
        _save_owner_calendar_tokens(res["access_token"], res.get("refresh_token"), res.get("expires_in", 3600), res.get("scope", ""))
        _push_notification("🔗 Google Calendar conectado com sucesso")
        return RedirectResponse(url="/?calendar=connected")
    except Exception: return RedirectResponse(url="/?calendar=token")

@app.get("/calendar/status")
async def calendar_status(friday_session: Optional[str] = Cookie(None)):
    if not friday_session: return JSONResponse(status_code=401, content={"connected": False})
    return {"connected": bool(GOOGLE_TOKEN_STORE.get(GOOGLE_OWNER_KEY))}

@app.get("/calendar/events")
async def calendar_events(range: str = "upcoming", friday_session: Optional[str] = Cookie(None)):
    if not friday_session: raise HTTPException(401)
    at = _usable_calendar_access_token()
    if not at: return {"events": []}
    events = _calendar_today_events(at) if range == "today" else _calendar_events_query(at)
    return {"events": events or []}

@app.post("/chat")
async def chat_endpoint(data: ChatData, friday_session: Optional[str] = Cookie(None)):
    # (Seu código original do chat_endpoint aqui...)
    pass # Substitua pelo seu código real que estava aqui

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
