import os
import base64
import json
import urllib.parse
import io
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from urllib.request import Request, urlopen

from .context import _CALENDAR_CONTEXT
from .utils import _extract_email_body, _truncate_text

try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False

def _handler_send_email(to_email, subject, body, attachment_base64=None, attachment_name=None):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")

    if not all([smtp_server, smtp_user, smtp_pass]): return {"error": "Credenciais ausentes."}

    if attachment_base64 and attachment_name:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        try:
            data = attachment_base64.split(",", 1)[1] if "," in attachment_base64 else attachment_base64
            raw = base64.b64decode(data)
            part = MIMEApplication(raw)
            part.add_header('Content-Disposition', 'attachment', filename=attachment_name)
            msg.attach(part)
        except Exception as e: return {"error": f"Falha no anexo: {e}"}
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

    if not all([smtp_server, smtp_user, smtp_pass]): return {"error": "Credenciais ausentes."}

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
    if not callable(ctx): return {"error": "Google não conectado."}
    access_token = ctx()
    if not access_token: return {"error": "Google não conectado."}

    params = {"maxResults": str(max_results), "labelIds": "INBOX"}
    if query: params["q"] = query

    try:
        req = Request("https://gmail.googleapis.com/gmail/v1/users/me/messages?" + urllib.parse.urlencode(params), headers={"Authorization": "Bearer " + access_token})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": f"Falha Gmail: {e}"}

    messages = data.get("messages", [])
    if not messages: return {"ok": True, "emails": [], "message": "Nenhum e-mail encontrado."}

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
        except Exception: pass

    return {"ok": True, "emails": emails}

def _handler_read_email_full(message_id):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    if not callable(ctx): return {"error": "Google não conectado."}
    access_token = ctx()
    if not access_token: return {"error": "Google não conectado."}

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
    if not callable(ctx): return {"error": "Google não conectado."}
    access_token = ctx()
    if not access_token: return {"error": "Google não conectado."}

    try:
        req = Request(f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}?format=metadata", headers={"Authorization": "Bearer " + access_token})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        msgs = data.get("messages", [])
        return {"thread_id": thread_id, "history": "\n".join([f"Data: {m.get('snippet')}" for m in msgs])}
    except Exception as e:
        return {"error": str(e)}
