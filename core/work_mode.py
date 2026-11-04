def fetch_work_emails(access_token, max_results=10):
    # Removemos 'labelIds=INBOX' para pegar e-mails gerais e aumentamos o limite
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
                
                # Verifica se está na label UNREAD
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
        return {"error": str(e)} # Retorna o erro para a tela
