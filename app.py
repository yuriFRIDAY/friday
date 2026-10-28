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
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request as URLRequest, urlopen
from urllib.error import HTTPError, URLError

from fastapi import FastAPI, Request, Response, HTTPException, Cookie
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from core.assistant import FridayAssistant
from core.auth import FridayAuth
from core.sessions import SessionStore # Removido 'Session' daqui para evitar crash se não existir
from core.friday_tools import (
    TOOL_REGISTRY, execute_tool, public_tool_specs,
    set_calendar_context, set_memory_context,
)

# ========================================
# CONFIGURAÇÕES INICIAIS
# ========================================
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

def _push_status(text):
    global STATUS_QUEUE
    with QUEUE_LOCK:
        STATUS_QUEUE.append(text)
        if len(STATUS_QUEUE) > 20:
            STATUS_QUEUE.pop(0)

# ========================================
# BANCO DE DADOS (POSTGRESQL COM FALLBACK SQLITE)
# ========================================
def get_db():
    if not DATABASE_URL:
        import sqlite3
        conn = sqlite3.connect("friday.db")
        conn.row_factory = sqlite3.Row
        return conn
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db()
        cursor = conn.cursor()
        if DATABASE_URL:
            cursor.execute("CREATE TABLE IF NOT EXISTS ai_memory (key TEXT PRIMARY KEY, value TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS tasks (id SERIAL PRIMARY KEY, description TEXT, status TEXT)")
        else:
            cursor.execute("CREATE TABLE IF NOT EXISTS ai_memory (key TEXT PRIMARY KEY, value TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, description TEXT, status TEXT)")
        conn.commit()
        cursor.close()
        conn.close()
        print("Banco de dados inicializado com sucesso.")
    except Exception as e:
        print(f"AVISO: Erro ao inicializar DB (o servidor continuará rodando): {e}")

# Roda a inicialização em background para não travar o Uvicorn (Evita erro 502)
threading.Thread(target=init_db, daemon=True).start()

def _db_save_memory(key, value):
    conn = get_db(); cursor = conn.cursor()
    try:
        if DATABASE_URL:
            cursor.execute("INSERT INTO ai_memory (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, value))
        else:
            cursor.execute("INSERT OR REPLACE INTO ai_memory (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally: 
        cursor.close()
        conn.close()

def _db_recall_memory(key):
    conn = get_db(); cursor = conn.cursor()
    try:
        if DATABASE_URL:
            cursor.execute("SELECT key, value FROM ai_memory WHERE key ILIKE %s OR value ILIKE %s LIMIT 1", (f"%{key}%", f"%{key}%"))
        else:
            cursor.execute("SELECT key, value FROM ai_memory WHERE key LIKE ? OR value LIKE ? LIMIT 1", (f"%{key}%", f"%{key}%"))
        row = cursor.fetchone()
        return f"{row[0]}: {row[1]}" if row else None
    finally: 
        cursor.close()
        conn.close()

def _db_delete_memory(key):
    conn = get_db(); cursor = conn.cursor()
    try:
        if DATABASE_URL:
            cursor.execute("DELETE FROM ai_memory WHERE key ILIKE %s", (f"%{key}%",))
        else:
            cursor.execute("DELETE FROM ai_memory WHERE key LIKE ?", (f"%{key}%",))
        conn.commit()
        return cursor.rowcount > 0
    finally: 
        cursor.close()
        conn.close()

set_memory_context(save_func=_db_save_memory, recall_func=_db_recall_memory, delete_func=_db_delete_memory)

# ========================================
# GOOGLE CALENDAR / GMAIL — OAUTH
# ========================================
GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/gmail.readonly"
GOOGLE_OWNER_KEY = "owner"
DATA_DIR = os.getenv("DATA_DIR", ".")
if os.path.exists("/data") and os.access("/data", os.W_OK):
    DATA_DIR = "/data"
GOOGLE_TOKEN_FILE = os.getenv("FRIDAY_GOOGLE_TOKEN_FILE", "").strip() or os.path.join(DATA_DIR, ".friday_google_tokens.json")

GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
GOOGLE_OAUTH_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()

GOOGLE_TOKEN_STORE = {}

def _load_google_token_store():
    global GOOGLE_TOKEN_STORE
    try:
        with open(GOOGLE_TOKEN_FILE, "r", encoding="utf-8") as h:
            GOOGLE_TOKEN_STORE = json.load(h)
    except Exception:
        GOOGLE_TOKEN_STORE = {}

_load_google_token_store()

def _save_owner_calendar_tokens(access_token, refresh_token, expires_in, scope):
    global GOOGLE_TOKEN_STORE
    GOOGLE_TOKEN_STORE[GOOGLE_OWNER_KEY] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": time.time() + max(60, int(expires_in)),
        "scope": scope or "",
    }
    try:
        os.makedirs(os.path.dirname(GOOGLE_TOKEN_FILE) or ".", exist_ok=True)
        with open(GOOGLE_TOKEN_FILE, "w", encoding="utf-8") as h:
            json.dump(GOOGLE_TOKEN_STORE, h)
    except Exception: 
        pass

def _usable_calendar_access_token():
    record = GOOGLE_TOKEN_STORE.get(GOOGLE_OWNER_KEY)
    if not record: 
        return None
    if time.time() < float(record.get("expires_at", 0)) - 60:
        return record["access_token"]
    refresh_token = record.get("refresh_token")
    if not refresh_token: 
        return None
    
    try:
        req = URLRequest(GOOGLE_OAUTH_TOKEN_URL, data=urlencode({
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        
        with urlopen(req, timeout=15) as resp:
            res = json.loads(resp.read().decode())
            _save_owner_calendar_tokens(res["access_token"], refresh_token, res.get("expires_in", 3600), res.get("scope", ""))
            return res["access_token"]
    except Exception:
        return None

def _calendar_events_query(access_token, time_min=None, time_max=None, max_results=10):
    params = {"maxResults": str(max_results), "singleEvents": "true", "orderBy": "startTime"}
    params["timeMin"] = time_min or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if time_max: 
        params["timeMax"] = time_max
    
    req = URLRequest("https://www.googleapis.com/calendar/v3/calendars/primary/events?" + urlencode(params), headers={"Authorization": "Bearer " + access_token})
    try:
        with urlopen(req, timeout=15) as resp: 
            result = json.loads(resp.read().decode())
    except Exception: 
        return None
    
    return [{"id": item.get("id", ""), "summary": item.get("summary", "(sem título)"), "start": (item.get("start") or {}).get("dateTime", ""), "hangoutLink": item.get("hangoutLink", "")} for item in (result.get("items") or [])[:max_results]]

def _calendar_today_events(access_token):
    now = time.localtime()
    return _calendar_events_query(access_token, time.strftime("%Y-%m-%dT00:00:00", now) + "Z", time.strftime("%Y-%m-%dT23:59:59", now) + "Z", 20)

def _calendar_create_event(access_token, payload):
    req = URLRequest("https://www.googleapis.com/calendar/v3/calendars/primary/events", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + access_token}, method="POST")
    try:
        with urlopen(req, timeout=15) as resp: 
            return json.loads(resp.read().decode())
    except Exception: 
        return None

def _calendar_delete_event(access_token, event_id):
    req = URLRequest(f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}", headers={"Authorization": "Bearer " + access_token}, method="DELETE")
    try:
        with urlopen(req, timeout=15): 
            return {"ok": True}
    except Exception: 
        return None

def _calendar_update_event(access_token, event_id, payload):
    req = URLRequest(f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + access_token}, method="PATCH")
    try:
        with urlopen(req, timeout=15) as resp: 
            return json.loads(resp.read().decode())
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

# ========================================
# ROTAS DA API (FASTAPI)
# ========================================
class LoginData(BaseModel):
    password: str

class ChatData(BaseModel):
    command: str
    pdf: Optional[str] = None
    pdf_name: Optional[str] = "documento.pdf"

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Erro: index.html não encontrado.</h1>"

@app.head("/")
async def head_index():
    return Response(status_code=200)

@app.get("/manifest.json")
async def serve_manifest():
    return JSONResponse(content=json.load(open("manifest.json", "r")))

@app.get("/sw.js")
async def serve_sw():
    return Response(content=open("sw.js", "r").read(), media_type="application/javascript")

@app.get("/health")
async def health_check():
    return {"status": "ok"}

# ========================================
# ROTA DE TTS (CORRIGIDA)
# ========================================
@app.get("/tts")
async def text_to_speech(text: str = "", friday_session: Optional[str] = Cookie(None)):
    if not friday_session:
        raise HTTPException(status_code=401, detail="Não autorizado")
    if not text:
        raise HTTPException(status_code=400, detail="Texto vazio.")

    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="Chave ElevenLabs ausente.")

    voice_id = "21m00Tcm4TlvDq8ikWAM"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
    
    payload = json.dumps({
        "text": text,
        "model_id": "eleven_flash_v2_5",
        "voice_settings": {"stability": 0.3, "similarity_boost": 0.85, "style": 0.0, "use_speaker_boost": True}
    }).encode("utf-8")
    
    req = URLRequest(url, data=payload, headers={
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }, method="POST")

    try:
        with urlopen(req, timeout=30) as response:
            audio_data = response.read()
            return Response(content=audio_data, media_type="audio/mpeg")
    except Exception as e:
        print(f"TTS erro: {e}")
        raise HTTPException(status_code=500, detail="Falha ao gerar áudio.")

@app.get("/status")
async def get_status(friday_session: Optional[str] = Cookie(None)):
    if not friday_session: 
        raise HTTPException(status_code=401, detail="Não autorizado")
    global STATUS_QUEUE
    with QUEUE_LOCK:
        msgs = list(STATUS_QUEUE)
        STATUS_QUEUE.clear()
    return {"status": msgs}

@app.get("/notifications")
async def get_notifications(friday_session: Optional[str] = Cookie(None)):
    if not friday_session: 
        raise HTTPException(status_code=401, detail="Não autorizado")
    global NOTIFICATION_QUEUE
    with QUEUE_LOCK:
        if NOTIFICATION_QUEUE:
            msgs = list(NOTIFICATION_QUEUE)
            NOTIFICATION_QUEUE.clear()
            return {"notifications": msgs}
    return {"notifications": []}

@app.post("/login")
async def login(data: LoginData, response: Response, request: Request):
    try:
        client_ip = request.client.host if request.client else "0.0.0.0"
        if not auth.authenticate(data.password, client_key=client_ip):
            raise HTTPException(status_code=401, detail="Senha incorreta.")
        
        token = secrets.token_urlsafe(32)
        
        # CORREÇÃO: Tentativa segura de criar a sessão para não derrubar o servidor (502)
        try:
            from core.sessions import Session
            SESSION_STORE._sessions[token] = Session(
                token=token,
                expires_at=SESSION_STORE._clock() + SESSION_STORE.ttl_seconds
            )
        except Exception:
            SESSION_STORE._sessions[token] = {"token": token, "expires_at": SESSION_STORE._clock() + SESSION_STORE.ttl_seconds}
        
        response.set_cookie(key="friday_session", value=token, httponly=True, samesite="lax", max_age=1800)
        return {"ok": True}
    except HTTPException as he:
        raise he
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro interno no login: {str(e)}")

@app.get("/session")
async def check_session(friday_session: Optional[str] = Cookie(None)):
    if friday_session and SESSION_STORE.get(friday_session):
        return {"authenticated": True}
    return {"authenticated": False}

@app.get("/system_metrics")
async def get_metrics(friday_session: Optional[str] = Cookie(None)):
    if not friday_session: 
        raise HTTPException(status_code=401)
    try:
        load1, _, _ = os.getloadavg()
        with open('/proc/meminfo', 'r') as f: 
            lines = f.readlines()
        mem_total = int(lines[0].split()[1])
        mem_free = int(lines[1].split()[1])
        ram_percent = round((mem_total - mem_free) / mem_total * 100, 1)
        return {"cpu": min(100, round(load1 * 10, 1)), "ram": ram_percent}
    except Exception:
        return {"cpu": 0, "ram": 0}

@app.get("/calendar/connect")
async def calendar_connect(friday_session: Optional[str] = Cookie(None)):
    if not friday_session: 
        return RedirectResponse(url="/?calendar=nosession")
    if GOOGLE_TOKEN_STORE.get(GOOGLE_OWNER_KEY): 
        return RedirectResponse(url="/?calendar=connected")
    
    params = {
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    }
    return RedirectResponse(url=f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{urlencode(params)}")

@app.get("/oauth/google/callback")
async def google_callback(request: Request):
    query = request.query_params
    code = query.get("code")
    if not code: 
        return RedirectResponse(url="/?calendar=denied")
    
    try:
        req = URLRequest(GOOGLE_OAUTH_TOKEN_URL, data=urlencode({
            "code": code,
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        }).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        with urlopen(req, timeout=15) as resp: 
            res = json.loads(resp.read().decode())
        _save_owner_calendar_tokens(res["access_token"], res.get("refresh_token"), res.get("expires_in", 3600), res.get("scope", ""))
        return RedirectResponse(url="/?calendar=connected")
    except Exception:
        return RedirectResponse(url="/?calendar=token")

@app.get("/calendar/status")
async def calendar_status(friday_session: Optional[str] = Cookie(None)):
    if not friday_session: 
        return JSONResponse(status_code=401, content={"connected": False})
    return {"connected": bool(GOOGLE_TOKEN_STORE.get(GOOGLE_OWNER_KEY))}

@app.get("/calendar/events")
async def calendar_events(range: str = "upcoming", friday_session: Optional[str] = Cookie(None)):
    if not friday_session: 
        raise HTTPException(status_code=401)
    at = _usable_calendar_access_token()
    if not at: 
        raise HTTPException(status_code=409, detail="Calendar desconectado.")
    
    events = _calendar_today_events(at) if range == "today" else _calendar_events_query(at)
    return {"events": events or []}

@app.post("/chat")
async def chat_endpoint(data: ChatData, friday_session: Optional[str] = Cookie(None)):
    if not friday_session: 
        raise HTTPException(status_code=401)
    
    history = SESSION_STORE.get_history(friday_session)
    if history is None:
        history = []
        
    session = SESSION_STORE.get(friday_session)
    if session is None:
        session = {}

    def event_generator():
        from core.ai_provider import FridayAIProvider
        provider = FridayAIProvider()
        
        if not provider.available():
            yield f"data: {json.dumps({'error': 'IA não configurada (Groq API Key).'})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
            return

        if data.pdf:
            yield f"data: {json.dumps({'text': 'Lendo documento PDF... '})}\n\n"

        current_messages = list(history)
        current_messages.append({"role": "user", "content": data.command})

        for _ in range(5):
            stream_gen = provider.chat_with_full_history_stream(current_messages, public_tool_specs())
            tool_call_acc = {}
            has_tool = False
            full_text = ""
            error_occurred = False

            for chunk in stream_gen:
                if "error" in chunk:
                    print(f"ERRO NA IA: {chunk['error']}") 
                    yield f"data: {json.dumps({'error': chunk['error']})}\n\n"
                    error_occurred = True
                    break
                if "text" in chunk:
                    full_text += chunk["text"]
                    yield f"data: {json.dumps({'text': chunk['text']})}\n\n"
                if "tool_call_chunk" in chunk:
                    has_tool = True
                    tc = chunk["tool_call_chunk"]
                    idx = tc.get("index", 0)
                    if idx not in tool_call_acc:
                        tool_call_acc[idx] = {"id": tc.get("id", ""), "name": "", "arguments": ""}
                    if "function" in tc:
                        if "name" in tc["function"]: 
                            tool_call_acc[idx]["name"] += tc["function"]["name"]
                        if "arguments" in tc["function"]: 
                            tool_call_acc[idx]["arguments"] += tc["function"]["arguments"]

            if error_occurred:
                break

            if has_tool:
                tool_msg = json.dumps({'text': '\n\n*[Executando ferramenta...]*\n\n'})
                yield f"data: {tool_msg}\n\n"
                
                tc_data = tool_call_acc[0]
                try: 
                    args = json.loads(tc_data["arguments"])
                except: 
                    args = {}
                
                exec_res = execute_tool(tc_data["name"], args, session=session)
                result = exec_res.get("result") if exec_res.get("ok") else {"error": exec_res.get("error")}
                
                current_messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": tc_data["id"], "type": "function", "function": {"name": tc_data["name"], "arguments": tc_data["arguments"]}}]})
                current_messages.append({"role": "tool", "tool_call_id": tc_data["id"], "content": json.dumps(result)})
                continue
            else:
                history.append({"role": "user", "content": data.command})
                history.append({"role": "assistant", "content": full_text})
                break

        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
