import os
import json
import time
import base64
import urllib.parse
import threading
import re
from pathlib import Path
from urllib.request import Request, urlopen

from core.database import DATA_DIR

WORK_TOKEN_FILE = os.getenv("FRIDAY_WORK_TOKEN_FILE", "").strip() or os.path.join(DATA_DIR, ".friday_work_tokens.json")
WORK_TOKEN_STORE = {}
WORK_LOCK = threading.Lock()

# Variável que estava faltando
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

def _load_work_tokens():
    global WORK_TOKEN_STORE
    try:
        with open(WORK_TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        WORK_TOKEN_STORE = data if isinstance(data, dict) else {}
    except FileNotFoundError:
        WORK_TOKEN_STORE = {}
    except Exception as e:
        print(f"Erro ao carregar tokens do trabalho: {e}")
        WORK_TOKEN_STORE = {}

_load_work_tokens()

def _save_work_tokens_unlocked(access_token, refresh_token, expires_in):
    global WORK_TOKEN_STORE
    WORK_TOKEN_STORE["access_token"] = access_token
    WORK_TOKEN_STORE["refresh_token"] = refresh_token or WORK_TOKEN_STORE.get("refresh_token")
    WORK_TOKEN_STORE["expires_at"] = time.time() + max(60, int(expires_in))
    directory = os.path.dirname(WORK_TOKEN_FILE) or "."
    os.makedirs(directory, exist_ok=True)
    temporary = WORK_TOKEN_FILE + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(WORK_TOKEN_STORE, f)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, WORK_TOKEN_FILE)
        try:
            os.chmod(WORK_TOKEN_FILE, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass

def save_work_tokens(access_token, refresh_token, expires_in):
    with WORK_LOCK:
        _save_work_tokens_unlocked(access_token, refresh_token, expires_in)

def get_valid_work_token(client_id, client_secret):
    with WORK_LOCK:
        if not WORK_TOKEN_STORE: return None
        if time.time() < WORK_TOKEN_STORE.get("expires_at", 0) - 60:
            return WORK_TOKEN_STORE["access_token"]
        
        refresh_token = WORK_TOKEN_STORE.get("refresh_token")
        if not refresh_token: return None
        
        try:
            data = urllib.parse.urlencode({
                "client_id": client_id, "client_secret": client_secret,
                "refresh_token": refresh_token, "grant_type": "refresh_token"
            }).encode()
            req = Request("https://oauth2.googleapis.com/token", data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urlopen(req, timeout=10) as resp:
                res = json.loads(resp.read().decode())
                _save_work_tokens_unlocked(res["access_token"], refresh_token, res.get("expires_in", 3600))
                return res["access_token"]
        except Exception:
            return None

def _b64_decode(data):
    try:
        data = data.replace('-', '+').replace('_', '/')
        padding = '=' * (-len(data) % 4)
        return base64.b64decode(data + padding).decode("utf-8", errors="ignore")
    except Exception:
        return ""

def extract_email_body(payload):
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data: return _b64_decode(data)
    elif payload.get("mimeType") == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            html = _b64_decode(data)
            text = re.sub(r'<[^>]+>', ' ', html)
            return re.sub(r'\s+', ' ', text).strip()
    
    for part in payload.get("parts", []):
        text = extract_email_body(part)
        if text: return text
    return ""

def fetch_work_emails(access_token, max_results=10):
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults={max_results}"
    req = Request(url, headers={"Authorization": f"Bearer {access_token}"})
    
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            messages = data.get("messages", [])
            
        emails = []
        for msg in messages:
            msg_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}?format=full"
            msg_req = Request(msg_url, headers={"Authorization": f"Bearer {access_token}"})
            with urlopen(msg_req, timeout=15) as resp_msg:
                msg_data = json.loads(resp_msg.read().decode())
                
                headers = msg_data.get("payload", {}).get("headers", [])
                subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "(Sem assunto)")
                sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "Desconhecido")
                date = next((h["value"] for h in headers if h["name"].lower() == "date"), "")
                
                label_ids = msg_data.get("labelIds", [])
                is_unread = "UNREAD" in label_ids
                
                snippet = msg_data.get("snippet", "")
                body = extract_email_body(msg_data.get("payload", {}))[:2000]
                
                emails.append({
                    "id": msg['id'],
                    "subject": subject,
                    "sender": sender,
                    "date": date,
                    "snippet": snippet,
                    "body": body,
                    "is_unread": is_unread,
                    "link": f"https://mail.google.com/mail/u/0/#all/{msg['id']}"
                })
        return emails
    except Exception as e:
        print(f"Erro ao buscar e-mails no Gmail: {e}")
        return {"error": str(e)}
