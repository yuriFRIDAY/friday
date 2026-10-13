
from http.server import BaseHTTPRequestHandler, HTTPServer
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
import json
import os
import secrets

from core.assistant import FridayAssistant
from core.auth import FridayAuth


SESSIONS = set()
SESSION_COOKIE = "friday_session"
MAX_BODY = 8192
CHAT_HISTORY = {}
MAX_HISTORY_MESSAGES = 10

auth = FridayAuth()
friday = FridayAssistant()


HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FRIDAY | Stark System</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:#050a14;color:#e8f3ff;
font-family:Arial,sans-serif;display:flex;justify-content:center;align-items:center;
padding:18px}
.panel{width:100%;max-width:720px;background:#0b1424;border:1px solid #173e60;
border-radius:20px;padding:24px;box-shadow:0 0 35px #0077ff18}
header{text-align:center;margin-bottom:25px}
.reactor{width:86px;height:86px;border:3px solid #21c8ff;border-radius:50%;
margin:0 auto 14px;display:grid;place-items:center;box-shadow:0 0 24px #00aaff55;
animation:pulse 3s infinite}
.reactor span{font-size:35px;color:#6ce7ff}
@keyframes pulse{50%{box-shadow:0 0 38px #00aaff99}}
h1{letter-spacing:6px;color:#72dcff;margin:8px 0}
.sub{color:#829bb8;font-size:13px}
input,button{font-size:16px;border-radius:10px;padding:13px}
input{width:100%;background:#050c18;border:1px solid #23415e;color:white;outline:none}
input:focus{border-color:#39cfff}
button{border:0;background:#087fb4;color:white;font-weight:bold;cursor:pointer}
button:disabled{opacity:.5}
button:hover{filter:brightness(1.15)}
.primary{width:100%;margin-top:12px}
.hidden{display:none!important}
.status{text-align:center;color:#72dcff;font-size:13px;margin:14px 0}
#messages{height:300px;overflow-y:auto;border:1px solid #18304a;
border-radius:12px;padding:12px;margin:15px 0;background:#060d19}
.msg{white-space:pre-wrap;overflow-wrap:anywhere;padding:11px 13px;
border-radius:10px;margin:8px 0;line-height:1.45}
.user{background:#10324a;color:#bfeeff}
.bot{background:#111f31;color:#e8f3ff}
.error{color:#ff9b9b}
.controls{display:flex;gap:8px}
.controls input{flex:1;min-width:0}
#voiceBtn{background:#126b8f;white-space:nowrap}
#voiceBtn.listening{background:#b33b55}
#logout{background:#26364a;width:100%;margin-top:12px}
footer{text-align:center;color:#58718e;font-size:12px;margin-top:18px}
</style>
</head>
<body>
<main class="panel">
<header>
<div class="reactor"><span>✦</span></div>
<h1>FRIDAY</h1>
<div class="sub">STARK INTELLIGENCE SYSTEM</div>
<div id="status" class="status">Sistema aguardando autenticação</div>
</header>

<section id="login">
<form id="loginForm">
<input id="password" type="password" placeholder="Senha de acesso"
autocomplete="current-password" required>
<button class="primary" type="submit">AUTENTICAR</button>
</form>
</section>

<section id="assistant" class="hidden">
<div id="messages" aria-live="polite"></div>
<form id="chatForm">
<div class="controls">
<input id="command" maxlength="2000" placeholder="Comandante, digite seu comando..."
autocomplete="off" required>
<button id="voiceBtn" type="button">🎙️ Falar</button>
</div>
<button class="primary" id="sendBtn" type="submit">ENVIAR COMANDO</button>
</form>
<button id="logout" type="button">Encerrar sessão</button>
</section>
<footer>PROJETO TONY STARK · FRIDAY</footer>
</main>

<script>
const $ = id => document.getElementById(id);
let recognition = null;
let listening = false;

function status(text) { $('status').textContent = text; }

function addMessage(who, text) {
  const item = document.createElement('div');
  item.className = 'msg ' + (who === 'Você' ? 'user' : 'bot');
  const label = document.createElement('strong');
  label.textContent = who + ': ';
  const content = document.createElement('span');
  content.textContent = text;
  item.append(label, content);
  $('messages').appendChild(item);
  $('messages').scrollTop = $('messages').scrollHeight;
}

async function request(url, data) {
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Falha na solicitação.');
  return result;
}

$('loginForm').addEventListener('submit', async event => {
  event.preventDefault();
  const password = $('password').value;
  status('Verificando credenciais...');
  try {
    await request('/login', {password});
    $('password').value = '';
    $('login').classList.add('hidden');
    $('assistant').classList.remove('hidden');
    status('Autenticada. FRIDAY online.');
    addMessage('FRIDAY', 'Aguardando suas ordens, comandante.');
    $('command').focus();
  } catch (error) {
    status(error.message);
    $('password').value = '';
    $('password').focus();
  }
});

$('chatForm').addEventListener('submit', async event => {
  event.preventDefault();
  const command = $('command').value.trim();
  if (!command) return;

  $('command').value = '';
  addMessage('Você', command);
  $('sendBtn').disabled = true;
  $('voiceBtn').disabled = true;
  status('FRIDAY está processando...');

  try {
    const result = await request('/chat', {command});
    addMessage('FRIDAY', result.response || 'Comando concluído.');
    status('Sistema online.');
    if (result.closed) showLogin();
  } catch (error) {
    addMessage('FRIDAY', error.message);
    status('Verifique a conexão ou autenticação.');
    if (error.message.toLowerCase().includes('sessão')) showLogin();
  } finally {
    $('sendBtn').disabled = false;
    $('voiceBtn').disabled = false;
    $('command').focus();
  }
});

function startVoice() {
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!SpeechRecognition) {
    status('Reconhecimento de voz indisponível. Tente Chrome ou Edge.');
    return;
  }

  if (listening && recognition) {
    recognition.stop();
    return;
  }

  recognition = new SpeechRecognition();
  recognition.lang = 'pt-BR';
  recognition.continuous = false;
  recognition.interimResults = false;

  recognition.onstart = () => {
    listening = true;
    $('voiceBtn').classList.add('listening');
    $('voiceBtn').textContent = '⏹️ Parar';
    status('Estou ouvindo, comandante...');
  };

  recognition.onresult = event => {
    const transcript = event.results[0][0].transcript;
    $('command').value = transcript;
    status('Voz reconhecida.');
    $('chatForm').requestSubmit();
  };

  recognition.onerror = event => {
    status('Falha no microfone: ' + event.error);
  };

  recognition.onend = () => {
    listening = false;
    $('voiceBtn').classList.remove('listening');
    $('voiceBtn').textContent = '🎙️ Falar';
  };

  try {
    recognition.start();
  } catch (error) {
    status('Não foi possível iniciar o microfone. Tente novamente.');
  }
}

$('voiceBtn').addEventListener('click', startVoice);

function showLogin() {
  if (recognition && listening) recognition.stop();
  $('assistant').classList.add('hidden');
  $('login').classList.remove('hidden');
  $('messages').replaceChildren();
  status('Sistema aguardando autenticação');
}

$('logout').addEventListener('click', async () => {
  try {
    await request('/logout', {});
  } catch (error) {
    // A interface será encerrada mesmo se a conexão falhar.
  }
  showLogin();
});

async function checkSession() {
  try {
    const response = await fetch('/session', {credentials: 'same-origin'});
    const data = await response.json();
    if (data.authenticated) {
      $('login').classList.add('hidden');
      $('assistant').classList.remove('hidden');
      status('Sessão ativa. FRIDAY online.');
      addMessage('FRIDAY', 'Bem-vindo de volta, comandante.');
    }
  } catch (error) {
    status('Não foi possível verificar a sessão.');
  }
}
checkSession();
</script>
</body>
</html>
"""


class FridayHandler(BaseHTTPRequestHandler):
    def send_json(self, status_code, data, cookie=None, clear_cookie=False):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")

        secure = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        cookie_text = (
            f"{SESSION_COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Strict"
        )
        if secure:
            cookie_text += "; Secure"
        if cookie:
            self.send_header("Set-Cookie", cookie_text)
        elif clear_cookie:
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
                + ("; Secure" if secure else "")
            )

        self.end_headers()
        self.wfile.write(body)

    def get_session(self):
        cookie_header = self.headers.get("Cookie", "")
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
            token = cookie.get(SESSION_COOKIE)
            if token and token.value in SESSIONS:
                return token.value
        except Exception:
            pass
        return None

    def valid_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlsplit(origin).netloc == self.headers.get("Host", "")

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

        if path == "/":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/session":
            self.send_json(200, {"authenticated": self.get_session() is not None})
            return

        self.send_json(404, {"error": "Página não encontrada."})

    def do_POST(self):
        path = urlsplit(self.path).path

        if not self.valid_origin():
            self.send_json(403, {"error": "Origem não autorizada."})
            return

        data = self.read_json()
        if data is None:
            self.send_json(400, {"error": "Solicitação inválida."})
            return

        if path == "/login":
            password = data.get("password")
            if not isinstance(password, str) or not auth.authenticate(password):
                self.send_json(401, {"error": "Autenticação negada. Aguarde se houver bloqueio."})
                return

            token = secrets.token_urlsafe(32)
            SESSIONS.add(token)
            self.send_json(200, {"ok": True}, cookie=token)
            return

        token = self.get_session()
        if not token:
            self.send_json(401, {"error": "Sessão inválida. Entre novamente."},
                           clear_cookie=True)
            return


        if path == "/logout":
            SESSIONS.discard(token)
            CHAT_HISTORY.pop(token, None)
            self.send_json(200, {"ok": True}, clear_cookie=True)
            return

        if path == "/chat":
            command = data.get("command")
            if not isinstance(command, str) or not command.strip() or len(command) > 2000:
                self.send_json(400, {"error": "Comando vazio ou muito longo."})
                return


            try:
                intention = friday.interpret(command.strip())

                history = CHAT_HISTORY.setdefault(token, [])

                response = friday.execute(
                    intention,
                    command.strip(),
                    "owner",
                    history=history,
                )

                if response is None:
                    SESSIONS.discard(token)
                    CHAT_HISTORY.pop(token, None)

                    self.send_json(200, {
                        "response": "FRIDAY encerrada.",
                        "closed": True
                    }, clear_cookie=True)
                    return

                if intention == "unknown":
                    from core.ai_provider import FridayAIProvider

                    provider = FridayAIProvider()

                    if (
                        provider.available()
                        and response
                        != "Não consegui obter uma resposta da IA agora."
                    ):
                        history.extend([
                            {
                                "role": "user",
                                "content": command.strip()
                            },
                            {
                                "role": "assistant",
                                "content": str(response)
                            },
                        ])

                        CHAT_HISTORY[token] = history[-MAX_HISTORY_MESSAGES:]

                self.send_json(200, {
                    "response": str(response),
                    "closed": False
                })

            except Exception:
                print("FRIDAY WEB: erro ao processar comando.")
                self.send_json(500, {"error": "Erro interno ao processar comando."})
            return

        self.send_json(404, {"error": "Rota não encontrada."})

    def log_message(self, format, *args):
        print("FRIDAY WEB:", format % args)


if __name__ == "__main__":
    if not os.getenv("FRIDAY_PASSWORD"):
        raise RuntimeError("Configure o segredo FRIDAY_PASSWORD no Replit.")

    port = int(os.environ.get("PORT", "5000"))
    server = HTTPServer(("0.0.0.0", port), FridayHandler)
    print(f"FRIDAY online na porta {port}.")
    server.serve_forever()
