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

# ✅ PDF support (opcional — se PyPDF2 não estiver instalado, loga aviso)
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

def _push_notification(text):
    """Enfileira toast notification. Frontend polla /notifications a cada 30s."""
    if not text:
        return
    global NOTIFICATION_QUEUE
    with QUEUE_LOCK:
        NOTIFICATION_QUEUE.append(text)
        if len(NOTIFICATION_QUEUE) > 20:
            NOTIFICATION_QUEUE.pop(0)

def _notify_tool(tool_name, args, result):
    """Gera notificação toast para tools relevantes."""
    if not result:
        return
    try:
        if tool_name == "create_calendar_event" and isinstance(result, dict) and result.get("ok"):
            summary = result.get("summary") or args.get("summary", "evento")
            start = result.get("start", "")
            _push_notification(f"📅 Evento criado: {summary}" + (f" ({start})" if start else ""))

        elif tool_name == "delete_calendar_event" and isinstance(result, dict) and result.get("ok"):
            _push_notification("🗑 Evento removido da agenda")

        elif tool_name == "update_calendar_event" and isinstance(result, dict) and result.get("ok"):
            _push_notification("✏ Evento atualizado")

        elif tool_name == "send_email" and isinstance(result, str) and "enviado" in result.lower():
            _push_notification(f"✉ E-mail enviado para {args.get('to_email', '?')}")

        elif tool_name == "reply_to_email" and isinstance(result, str) and "enviada" in result.lower():
            _push_notification(f"✉ Resposta enviada para {args.get('to_email', '?')}")

        elif tool_name == "create_task" and isinstance(result, str) and "criada" in result.lower():
            desc = str(args.get("description", ""))[:60]
            _push_notification(f"📋 Tarefa criada: {desc}")

        elif tool_name == "complete_task" and isinstance(result, str) and "concluída" in result.lower():
            _push_notification(f"✓ Tarefa {args.get('task_id', '?')} concluída")

        elif tool_name == "play_music" and isinstance(result, dict):
            action = result.get("action")
            if action == "play_new":
                _push_notification(f"🎵 Tocando: {args.get('query', '?')}")
            elif action == "pause":
                _push_notification("⏸ Música pausada")
            elif action == "play":
                _push_notification("▶ Música retomada")

    except Exception as e:
        print(f"[NOTIFY] Falha ao gerar notificação: {e}")

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
        _push_notification(f"⚠ Banco de dados indisponível: {str(e)[:80]}")

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
    except Exception as e:
        print(f"ERRO NO GOOGLE CALENDAR CREATE: {e}")
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

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Erro: index.html não encontrado.</h1>"

@app.get("/manifest.json")
async def serve_manifest():
    return JSONResponse(content=json.load(open("manifest.json", "r")))

@app.get("/sw.js")
async def serve_sw():
    return Response(content=open("sw.js", "r").read(), media_type="application/javascript")

@app.get("/health")
async def health_check():
    return {"status": "ok"}

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

        token = SESSION_STORE.create()
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

@app.get("/history")
async def get_history(friday_session: Optional[str] = Cookie(None)):
    if not friday_session:
        raise HTTPException(status_code=401, detail="Não autorizado")
    history = SESSION_STORE.get_history(friday_session)
    if history is None:
        return {"history": []}
    return {"history": history}

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
        _push_notification("🔗 Google Calendar conectado com sucesso")
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

# ========================================
# CHAT ENDPOINT (COM PDF + NOTIFICAÇÕES)
# ========================================
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

        # ═══════════════════════════════════════════
        # ✅ PDF: extrai texto e enriquece a mensagem
        # ═══════════════════════════════════════════
        user_content = (data.command or "").strip()

        if data.pdf:
            if not HAS_PYPDF2:
                yield f"data: {json.dumps({'text': '⚠ PyPDF2 não instalado — não consigo ler o PDF. '})}\n\n"
                _push_notification("⚠ Tentativa de ler PDF, mas PyPDF2 não instalado")
            else:
                try:
                    yield f"data: {json.dumps({'text': '📄 Lendo documento PDF... '})}\n\n"
                    pdf_b64 = data.pdf.split(",", 1)[1] if "," in data.pdf else data.pdf
                    pdf_bytes = base64.b64decode(pdf_b64)
                    reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
                    n_pages = len(reader.pages)
                    extracted = "\n".join((page.extract_text() or "") for page in reader.pages[:20])

                    if extracted.strip():
                        yield f"data: {json.dumps({'text': f'PDF lido ({n_pages} págs, {len(extracted)} chars). '})}\n\n"
                        pdf_block = (
                            f"\n\n--- CONTEÚDO DO PDF '{data.pdf_name or 'documento.pdf'}' ---\n"
                            f"{extracted[:8000]}\n"
                            f"--- FIM DO PDF ---"
                        )
                        if not user_content:
                            user_content = "Analise o conteúdo do documento em anexo."
                        user_content += pdf_block
                    else:
                        yield f"data: {json.dumps({'text': '⚠ PDF sem texto extraível (pode ser escaneado). '})}\n\n"
                        _push_notification(f"⚠ PDF '{data.pdf_name}' sem texto extraível (OCR necessário)")
                except Exception as e:
                    err_msg = f"⚠ Falha ao ler PDF: {str(e)[:100]}"
                    yield f"data: {json.dumps({'text': err_msg + '. '})}\n\n"
                    _push_notification(err_msg)

        if not user_content:
            user_content = "Olá"

        current_messages = list(history)
        current_messages.append({"role": "user", "content": user_content})
        response_generated = False

        for _ in range(5):
            stream_gen = provider.chat_with_full_history_stream(current_messages, public_tool_specs())
            tool_call_acc = {}
            has_tool = False
            full_text = ""
            error_occurred = False

            for chunk in stream_gen:
                if "error" in chunk:
                    err_str = str(chunk["error"])
                    print(f"ERRO NA IA: {err_str}")
                    yield f"data: {json.dumps({'error': err_str})}\n\n"
                    _push_notification(f"⚠ Erro IA: {err_str[:120]}")
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
                tc_data = tool_call_acc[0]
                try:
                    args = json.loads(tc_data["arguments"])
                except:
                    args = {}

                yield f"data: {json.dumps({'tool': tc_data['name']})}\n\n"

                exec_res = execute_tool(tc_data["name"], args, session=session, confirmed=True)

                if not exec_res.get("ok"):
                    err_msg = exec_res.get("error", "Erro na ferramenta.")
                    yield f"data: {json.dumps({'text': f'Erro ao executar a ferramenta: {err_msg}'})}\n\n"
                    _push_notification(f"⚠ Erro em {tc_data['name']}: {err_msg[:80]}")
                    break

                result = exec_res.get("result")

                # ✅ Notificação para tools relevantes
                if exec_res.get("ok"):
                    _notify_tool(tc_data["name"], args, result)

                current_messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": tc_data["id"], "type": "function", "function": {"name": tc_data["name"], "arguments": tc_data["arguments"]}}]})
                current_messages.append({"role": "tool", "tool_call_id": tc_data["id"], "content": json.dumps(result)})
                continue
            else:
                if full_text.strip():
                    history.append({"role": "user", "content": user_content})
                    history.append({"role": "assistant", "content": full_text})
                    response_generated = True
                break

        if not response_generated and not error_occurred:
            yield f"data: {json.dumps({'text': 'Desculpe, chefe. Tentei executar a ação mas não consegui obter uma resposta.'})}\n\n"

        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
