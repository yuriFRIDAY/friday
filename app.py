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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#050914">
<title>FRIDAY | Stark Systems</title>
<style>
:root{
  color-scheme:dark;
  --bg:#040711;
  --panel:#091322;
  --panel2:#0c1a2b;
  --cyan:#74e5ff;
  --blue:#278bff;
  --muted:#829bb7;
  --border:rgba(93,177,229,.19);
  --glow:rgba(30,177,255,.15);
  --green:#6affc4;
  --danger:#ff6b87;
}
*{box-sizing:border-box}
html{min-height:100%;background:var(--bg)}
body{
  margin:0;min-height:100vh;
  color:#eaf6ff;
  font-family:Inter,"Segoe UI",Arial,sans-serif;
  background:
    radial-gradient(ellipse at 50% -20%,#123c6470,transparent 52%),
    radial-gradient(ellipse at 100% 100%,#06385a25,transparent 40%),
    var(--bg);
  display:flex;justify-content:center;align-items:center;
  padding:24px;position:relative;isolation:isolate;
}
body:before{
  content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
  background-image:
    linear-gradient(#4c9dc90b 1px,transparent 1px),
    linear-gradient(90deg,#4c9dc90b 1px,transparent 1px);
  background-size:38px 38px;
  mask-image:linear-gradient(to bottom,#000 0%,transparent 85%);
}
body:after{
  content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:repeating-linear-gradient(
    to bottom,transparent 0px,transparent 4px,#8bdfff04 5px
  );
}
button,input,select{font:inherit}
button{
  border:1px solid #47c9ff35;
  background:linear-gradient(135deg,#126b98,#12456f);
  color:#effaff;font-size:13px;font-weight:700;letter-spacing:.4px;
  padding:12px 15px;border-radius:10px;cursor:pointer;
  transition:transform .18s,border-color .18s,filter .18s,background .18s;
}
button:hover{filter:brightness(1.18);border-color:#72e5ff80}
button:active{transform:scale(.985)}
button:disabled{opacity:.45;cursor:not-allowed;transform:none}
button:focus-visible,input:focus-visible,select:focus-visible{
  outline:2px solid var(--cyan);outline-offset:2px;
}
input,select{
  width:100%;min-width:0;color:#eaf6ff;
  background:#050b16;border:1px solid #24405b;
  padding:13px 14px;border-radius:10px;outline:none;
  transition:border-color .2s,box-shadow .2s;
}
input::placeholder{color:#627b96}
input:focus,select:focus{
  border-color:#53d9ff;box-shadow:0 0 0 3px #21bfff12;
}
input[type=checkbox]{width:19px;height:19px;accent-color:#47d8ff;cursor:pointer}
input[type=range]{padding:0;accent-color:#54dfff;cursor:pointer}
.hidden{display:none!important}

.shell{width:100%;max-width:1060px;min-width:0}
.topbar{
  display:flex;align-items:center;justify-content:space-between;
  gap:12px;margin-bottom:16px;padding:0 4px;
}
.brand{display:flex;align-items:center;gap:11px}
.brand-mark{
  width:37px;height:37px;border:1px solid #57dfff80;
  border-radius:11px;display:grid;place-items:center;
  color:var(--cyan);font-size:21px;
  background:linear-gradient(145deg,#153c5b,#071321);
  box-shadow:0 0 20px #20bfff1c;
}
.brand-name{font-size:14px;font-weight:800;letter-spacing:2.5px}
.brand-sub{font-size:9px;color:#7898b8;letter-spacing:2px;margin-top:4px}
.system-chip{
  display:flex;align-items:center;gap:8px;padding:8px 11px;
  border:1px solid var(--border);border-radius:30px;
  color:#9eb7d0;font-size:10px;letter-spacing:1px;
  background:#091322b8;white-space:nowrap;
}
.status-dot{
  width:7px;height:7px;border-radius:50%;background:var(--green);
  box-shadow:0 0 10px #5effbd80;flex-shrink:0;
}
.layout{
  display:grid;grid-template-columns:250px minmax(0,1fr);
  gap:16px;align-items:stretch;
}
.panel{
  min-width:0;overflow:hidden;
  background:linear-gradient(155deg,#0b1728f5,#070e1bf7);
  border:1px solid var(--border);border-radius:19px;
  box-shadow:0 20px 70px #0006,0 0 35px #0879bb0b;
}
.sidebar{padding:22px 17px;display:flex;flex-direction:column;align-items:center}
.reactor-wrap{
  width:174px;height:174px;position:relative;
  display:grid;place-items:center;margin:6px auto 20px;
}
.reactor{
  width:100px;height:100px;border-radius:50%;
  position:relative;display:grid;place-items:center;
  border:1px solid #74e5ffb5;
  background:radial-gradient(circle,#43d5ff30 0%,#0879ba15 46%,#06101c 73%);
  box-shadow:0 0 25px #25c9ff28,inset 0 0 25px #2bceff17;
  animation:coreGlow 4s ease-in-out infinite;
}
.reactor:before,.reactor:after{
  content:"";position:absolute;border-radius:50%;
  border:1px solid #50d9ff75;
}
.reactor:before{inset:-12px;border-right-color:transparent;animation:spin 10s linear infinite}
.reactor:after{inset:-23px;border-left-color:transparent;border-bottom-color:#4fbcff35;animation:spin 15s linear infinite reverse}
.reactor-inner{
  width:58px;height:58px;display:grid;place-items:center;
  border:2px solid #a0f1ff;border-radius:50%;
  color:#c8f8ff;font-size:32px;
  text-shadow:0 0 20px #40dfff;
  box-shadow:inset 0 0 18px #23bfff4c,0 0 20px #2ecbff40;
}
.orbit{
  position:absolute;inset:0;border:1px dashed #58dfff28;border-radius:50%;
  animation:spin 35s linear infinite;
}
.orbit:before,.orbit:after{
  content:"";position:absolute;width:5px;height:5px;border-radius:50%;
  background:var(--cyan);box-shadow:0 0 12px var(--cyan);
}
.orbit:before{top:17px;left:27px}
.orbit:after{right:20px;bottom:35px}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes coreGlow{
  0%,100%{box-shadow:0 0 25px #25c9ff28,inset 0 0 25px #2bceff17}
  50%{box-shadow:0 0 43px #25c9ff4c,inset 0 0 35px #2bceff30}
}
.system-title{
  text-align:center;font-size:25px;letter-spacing:8px;
  padding-left:8px;color:var(--cyan);font-weight:700;
  text-shadow:0 0 25px #4bcfff35;margin:0;
}
.system-sub{
  text-align:center;font-size:9px;color:#7897b7;
  letter-spacing:2.2px;margin-top:8px;
}
.system-state{
  width:100%;display:flex;align-items:center;justify-content:center;
  gap:8px;margin:22px 0 18px;padding:11px 8px;
  border-top:1px solid var(--border);border-bottom:1px solid var(--border);
  font-size:10px;letter-spacing:1.3px;color:#a5bfd8;
}
.mini-heading{
  width:100%;font-size:9px;letter-spacing:2px;color:#6485a6;
  margin:3px 0 10px;
}
.metric{
  width:100%;display:flex;align-items:center;justify-content:space-between;
  padding:10px 0;border-bottom:1px solid #8acfff10;gap:10px;
}
.metric-label{font-size:11px;color:#819bb6}
.metric-value{font-size:10px;color:#8de9ff;letter-spacing:.7px}
.sidebar-note{
  margin-top:auto;padding-top:22px;text-align:center;
  color:#4f6d8a;font-size:10px;line-height:1.7;
}
.sidebar-note span{color:#8aa8c4;letter-spacing:1.5px}

.main-panel{display:flex;flex-direction:column;min-height:620px}
.main-header{
  padding:20px 22px 17px;border-bottom:1px solid var(--border);
  display:flex;justify-content:space-between;align-items:center;gap:12px;
  background:linear-gradient(90deg,#0c1c2e,#091423);
}
.heading{font-size:14px;font-weight:700;letter-spacing:1.5px}
.heading-sub{font-size:10px;color:#7893b1;margin-top:6px}
.live-label{
  color:var(--green);font-size:9px;letter-spacing:1.5px;
  border:1px solid #58ffc32a;background:#5affc309;
  padding:7px 9px;border-radius:7px;white-space:nowrap;
}
.live-label:before{content:"";display:inline-block;width:5px;height:5px;
background:var(--green);border-radius:50%;margin:0 7px 1px 0}
#status{
  font-size:11px;color:#7fcce7;line-height:1.5;
  padding:12px 22px;background:#07101c;border-bottom:1px solid #83dfff10;
  min-height:39px;
}
#login{padding:25px;max-width:500px;width:100%;margin:auto}
.login-symbol{
  font-size:10px;color:#6e9abc;letter-spacing:2px;margin-bottom:18px;
}
.login-title{font-size:23px;letter-spacing:1px;margin:0 0 9px}
.login-copy{font-size:12px;color:#829bb7;line-height:1.7;margin:0 0 22px}
.primary{width:100%;margin-top:12px;min-height:45px}
#login .primary{letter-spacing:2px}
.assistant-content{display:flex;flex-direction:column;min-height:0;flex:1}
.toolbar{
  display:flex;gap:8px;flex-wrap:wrap;padding:13px 17px;
  border-bottom:1px solid #6dc9ff10;
}
.toolbar button{flex:1;min-width:125px}
.toolbar .secondary{background:#101f32;border-color:#29445f}
#settingsToggle{background:#102941}
#stopSpeakTop{background:#251827;border-color:#73374c;color:#ffc4d0}
.settings{
  margin:15px 17px;padding:19px;border:1px solid #265273;
  border-radius:13px;background:linear-gradient(145deg,#0b1a2b,#07111e);
  box-shadow:inset 0 1px #b3e9ff09;
}
.settings h2{font-size:13px;color:var(--cyan);letter-spacing:1.8px;margin:0 0 18px}
.setting{margin:18px 0}
.setting label{display:block;font-size:12px;color:#c5d9eb;margin-bottom:9px}
.setting small{display:block;color:#718ba7;font-size:10px;margin-top:7px;line-height:1.6}
.setting input[type=range]{background:transparent;border:0;box-shadow:none}
.toggle-row{display:flex;align-items:center;justify-content:space-between;gap:12px}
.toggle-row label{margin:0}
.setting-buttons{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px}
.setting-buttons button{flex:1;min-width:130px;font-size:11px}
#testVoiceBtn{background:#11557c}
#readLastBtn{background:#122b42}
#stopSpeakBtn{background:#402031;border-color:#824054}
#saveSettingsBtn{width:100%;margin-top:12px}
#settingsNotice{font-size:11px;color:var(--cyan);min-height:17px;margin-top:10px}

.chat-label{
  padding:16px 20px 0;display:flex;justify-content:space-between;
  gap:10px;color:#7895b3;font-size:9px;letter-spacing:2px;
}
.chat-label span:last-child{color:#4d718f;letter-spacing:1px}
#messages{
  margin:12px 17px 15px;padding:15px;flex:1;min-height:250px;
  max-height:440px;overflow-y:auto;scroll-behavior:smooth;
  border:1px solid #203b54;border-radius:13px;
  background:
    radial-gradient(ellipse at 50% 0%,#0b27411a,transparent 60%),
    #050b15;
}
#messages:empty:before{
  content:"AGUARDANDO INTERAÇÃO DO COMANDANTE";
  display:block;text-align:center;margin-top:85px;
  color:#405b77;font-size:10px;letter-spacing:1.7px;line-height:2;
}
.msg{
  white-space:pre-wrap;overflow-wrap:anywhere;
  padding:12px 14px;border-radius:4px 12px 12px 12px;
  margin:9px 0 14px;line-height:1.65;font-size:13px;
  animation:messageIn .22s ease-out;
}
@keyframes messageIn{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:translateY(0)}}
.msg strong{display:block;font-size:9px;letter-spacing:1.5px;margin-bottom:5px}
.user{
  margin-left:12%;background:linear-gradient(120deg,#10314b,#0c2236);
  border:1px solid #3e8fbd3d;color:#d4efff;
  border-radius:12px 4px 12px 12px;
}
.user strong{color:#79d9ff}
.bot{
  margin-right:5%;background:linear-gradient(120deg,#101e30,#0b1727);
  border:1px solid #6ccfff1c;color:#e4f1ff;
}
.bot strong{color:var(--cyan)}
.composer{
  padding:0 17px 18px;border-top:1px solid #83dfff10;
}
.composer-title{
  font-size:9px;letter-spacing:1.7px;color:#6486a5;margin:15px 0 10px;
}
.controls{display:flex;gap:8px;align-items:stretch}
.controls input{flex:1;min-width:0}
#voiceBtn{white-space:nowrap;background:#123953}
#voiceBtn.listening{
  background:#712c42;border-color:#ff829d;animation:listenPulse 1.3s infinite;
}
@keyframes listenPulse{50%{box-shadow:0 0 18px #ff537533}}
#sendBtn{letter-spacing:2px}
#logout{
  width:calc(100% - 34px);margin:0 17px 18px;
  background:#101927;border-color:#263b50;color:#a9bed2;
}
footer{
  text-align:center;padding:16px 8px 0;color:#425e79;
  font-size:9px;letter-spacing:2px;line-height:1.8;
}
footer span{color:#6489a9}
::-webkit-scrollbar{width:6px}
::-webkit-scrollbar-track{background:#060c16;border-radius:10px}
::-webkit-scrollbar-thumb{background:#244765;border-radius:10px}
::-webkit-scrollbar-thumb:hover{background:#367aa1}

@media(min-width:900px){
  .main-panel{min-height:680px}
}
@media(max-width:760px){
  body{padding:13px}
  .layout{grid-template-columns:1fr}
  .sidebar{padding:15px 18px 17px}
  .reactor-wrap{width:112px;height:112px;margin:3px auto 12px}
  .reactor{width:66px;height:66px}
  .reactor-inner{width:42px;height:42px;font-size:23px}
  .reactor:before{inset:-8px}
  .reactor:after{inset:-15px}
  .system-title{font-size:22px}
  .system-state{margin:14px 0 8px;padding:9px}
  .sidebar .mini-heading,.sidebar .metric,.sidebar-note{display:none}
  .main-panel{min-height:560px}
  #messages{min-height:230px;max-height:42vh}
}
@media(max-width:480px){
  body{padding:8px;align-items:flex-start}
  .shell{margin:4px 0}
  .topbar{margin:0 2px 11px}
  .brand-mark{width:33px;height:33px}
  .brand-name{font-size:12px;letter-spacing:2px}
  .brand-sub{font-size:8px;letter-spacing:1.3px}
  .system-chip{font-size:9px;padding:7px 8px}
  .panel{border-radius:15px}
  .sidebar{padding:12px 14px}
  .reactor-wrap{width:90px;height:90px;margin:0 auto 9px}
  .reactor{width:55px;height:55px}
  .reactor-inner{width:35px;height:35px;font-size:19px}
  .reactor:before{inset:-7px}
  .reactor:after{inset:-12px}
  .system-title{font-size:19px;letter-spacing:7px}
  .system-sub{font-size:8px;letter-spacing:1.7px;margin-top:5px}
  .system-state{font-size:9px;margin:11px 0 0;padding:8px}
  .main-panel{min-height:510px}
  .main-header{padding:15px}
  .heading{font-size:12px}
  .heading-sub{font-size:9px}
  .live-label{font-size:8px;padding:6px}
  #status{padding:10px 15px;font-size:10px}
  .toolbar{padding:10px;gap:7px}
  .toolbar button{min-width:0;padding:10px 7px;font-size:10px}
  #messages{margin:10px;padding:10px;min-height:220px;max-height:39vh}
  .msg{font-size:12px;padding:10px 11px}
  .composer{padding:0 10px 13px}
  .controls{gap:6px}
  .controls input{font-size:13px;padding:12px 9px}
  #voiceBtn{font-size:11px;padding:11px 8px}
  #sendBtn{font-size:11px}
  #logout{width:calc(100% - 20px);margin:0 10px 12px}
  .settings{margin:10px;padding:13px}
  .setting-buttons{gap:7px}
  .setting-buttons button{min-width:100%;padding:11px}
  #login{padding:24px 18px}
}
@media(prefers-reduced-motion:reduce){
  *,*:before,*:after{animation-duration:.01ms!important;animation-iteration-count:1!important;scroll-behavior:auto!important;transition:none!important}
}
</style>
</head>
<body>
<div class="shell">
  <div class="topbar">
    <div class="brand">
      <div class="brand-mark">✦</div>
      <div>
        <div class="brand-name">STARK SYSTEMS</div>
        <div class="brand-sub">PERSONAL INTELLIGENCE</div>
      </div>
    </div>
    <div class="system-chip"><span class="status-dot"></span> INTERFACE V2.0</div>
  </div>

  <div class="layout">
    <aside class="panel sidebar">
      <div class="reactor-wrap" aria-label="Núcleo visual da FRIDAY">
        <div class="orbit"></div>
        <div class="reactor"><div class="reactor-inner">✦</div></div>
      </div>
      <h1 class="system-title">FRIDAY</h1>
      <div class="system-sub">STARK INTELLIGENCE SYSTEM</div>
      <div class="system-state"><span class="status-dot"></span> <span>INTERFACE PRONTA</span></div>

      <div class="mini-heading">SISTEMA</div>
      <div class="metric">
        <span class="metric-label">Interface</span>
        <span class="metric-value">ONLINE</span>
      </div>
      <div class="metric">
        <span class="metric-label">Canal de voz</span>
        <span class="metric-value">BROWSER</span>
      </div>
      <div class="metric">
        <span class="metric-label">Assistente</span>
        <span class="metric-value">FRIDAY AI</span>
      </div>
      <div class="sidebar-note">
        <span>PROJETO TONY STARK</span><br>
        Uma interface. Um sistema.<br>Novas capacidades em desenvolvimento.
      </div>
    </aside>

    <main class="panel main-panel">
      <div class="main-header">
        <div>
          <div class="heading">CENTRAL DE COMANDO</div>
          <div class="heading-sub">Canal seguro de interação</div>
        </div>
        <div class="live-label">STARK CORE</div>
      </div>

      <div id="status" role="status" aria-live="polite">Sistema aguardando autenticação.</div>

      <section id="login">
        <div class="login-symbol">/// ACESSO RESTRITO · STARK SYSTEMS</div>
        <h2 class="login-title">Autenticação</h2>
        <p class="login-copy">Identifique-se para iniciar uma sessão segura com a FRIDAY.</p>
        <form id="loginForm">
          <input id="password" type="password" placeholder="Senha de acesso"
            autocomplete="current-password" required>
          <button class="primary" type="submit">INICIAR SESSÃO ↗</button>
        </form>
      </section>

      <section id="assistant" class="hidden assistant-content">
        <div class="toolbar">
          <button id="settingsToggle" type="button">⚙ Configurações de voz</button>
          <button id="stopSpeakTop" type="button">⏹ Parar voz</button>
        </div>

        <section id="settingsPanel" class="settings hidden">
          <h2>/// CONFIGURAÇÕES DE VOZ</h2>
          <div class="setting">
            <div class="toggle-row">
              <label for="autoSpeak">Leitura automática das respostas</label>
              <input id="autoSpeak" type="checkbox" checked>
            </div>
            <small>Desative para receber respostas somente em texto. Você ainda pode pedir a leitura manual.</small>
          </div>
          <div class="setting">
            <label for="speechRate">Velocidade: <strong id="rateValue">0,95×</strong></label>
            <input id="speechRate" type="range" min="0.75" max="1.2" step="0.05" value="0.95">
            <small>Ajuste a velocidade da fala.</small>
          </div>
          <div class="setting">
            <label for="voiceStyle">Estilo de voz</label>
            <select id="voiceStyle">
              <option value="natural">Natural e clara</option>
              <option value="formal">Formal e profissional</option>
              <option value="cinematic">Mais grave e cinematográfica</option>
            </select>
            <small>O resultado depende das vozes instaladas no navegador.</small>
          </div>
          <div class="setting">
            <label for="voiceSelect">Voz disponível no navegador</label>
            <select id="voiceSelect"><option value="">Automática (português brasileiro)</option></select>
            <small id="voiceInfo">Carregando vozes disponíveis...</small>
          </div>
          <div class="setting-buttons">
            <button type="button" id="testVoiceBtn">🔊 Testar voz</button>
            <button type="button" id="readLastBtn">▶ Ler última resposta</button>
            <button type="button" id="stopSpeakBtn">⏹ Interromper fala</button>
          </div>
          <button type="button" id="saveSettingsBtn">Salvar configurações</button>
          <div id="settingsNotice" role="status"></div>
        </section>

        <div class="chat-label">
          <span>/// REGISTRO DE COMUNICAÇÃO</span>
          <span>ENCRYPTED CHANNEL</span>
        </div>
        <div id="messages" aria-live="polite"></div>

        <div class="composer">
          <div class="composer-title">/// NOVO COMANDO</div>
          <form id="chatForm">
            <div class="controls">
              <input id="command" maxlength="2000"
                placeholder="Comandante, qual é a próxima ordem?"
                autocomplete="off" required>
              <button id="voiceBtn" type="button">🎙 Falar</button>
            </div>
            <button class="primary" id="sendBtn" type="submit">TRANSMITIR COMANDO ↗</button>
          </form>
        </div>
        <button id="logout" type="button">Encerrar sessão segura</button>
      </section>
    </main>
  </div>

  <footer><span>STARK SYSTEMS</span> · PROJETO TONY STARK · FRIDAY</footer>
</div>

<script>
const $ = id => document.getElementById(id);
let recognition = null;
let listening = false;
let lastReply = '';
let availableVoices = [];
let currentUtterance = null;
let speaking = false;

const SETTINGS_KEY = 'friday_voice_settings_v1';

const defaultSettings = {
  autoSpeak: true,
  rate: 0.95,
  style: 'natural',
  voiceURI: ''
};

let voiceSettings = {...defaultSettings};

function status(text) {
  $('status').textContent = text;
}

function loadSettings() {
  try {
    const saved = localStorage.getItem(SETTINGS_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      voiceSettings = {...defaultSettings, ...parsed};
    }
  } catch (error) {
    voiceSettings = {...defaultSettings};
  }

  voiceSettings.rate = Math.min(
    1.2, Math.max(0.75, Number(voiceSettings.rate) || 0.95)
  );

  if (!['natural', 'formal', 'cinematic'].includes(voiceSettings.style)) {
    voiceSettings.style = 'natural';
  }

  $('autoSpeak').checked = Boolean(voiceSettings.autoSpeak);
  $('speechRate').value = voiceSettings.rate;
  $('rateValue').textContent =
    voiceSettings.rate.toFixed(2).replace('.', ',') + '×';
  $('voiceStyle').value = voiceSettings.style;
}

function saveSettings(showNotice = true) {
  voiceSettings.autoSpeak = $('autoSpeak').checked;
  voiceSettings.rate = Number($('speechRate').value);
  voiceSettings.style = $('voiceStyle').value;
  voiceSettings.voiceURI = $('voiceSelect').value;

  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(voiceSettings));
    if (showNotice) {
      $('settingsNotice').textContent = 'Preferências salvas neste navegador.';
    }
  } catch (error) {
    if (showNotice) {
      $('settingsNotice').textContent =
        'Não foi possível salvar. As opções valem nesta sessão.';
    }
  }

  $('rateValue').textContent =
    voiceSettings.rate.toFixed(2).replace('.', ',') + '×';
}

function loadVoices() {
  if (!('speechSynthesis' in window)) {
    $('voiceSelect').innerHTML = '<option value="">Áudio indisponível</option>';
    $('voiceInfo').textContent = 'Este navegador não oferece síntese de voz.';
    return;
  }

  availableVoices = window.speechSynthesis.getVoices();
  const select = $('voiceSelect');
  const previous = voiceSettings.voiceURI;
  select.replaceChildren();

  const defaultOption = document.createElement('option');
  defaultOption.value = '';
  defaultOption.textContent = 'Automática (português brasileiro)';
  select.appendChild(defaultOption);

  const portugueseVoices = availableVoices.filter(voice =>
    /^pt(-|_)/i.test(voice.lang)
  );
  const voicesToShow = portugueseVoices.length
    ? portugueseVoices : availableVoices;

  voicesToShow.forEach(voice => {
    const option = document.createElement('option');
    option.value = voice.voiceURI;
    option.textContent = voice.name + ' (' + voice.lang + ')';
    select.appendChild(option);
  });

  const found = availableVoices.some(voice => voice.voiceURI === previous);
  select.value = found ? previous : '';

  if (portugueseVoices.length) {
    $('voiceInfo').textContent =
      portugueseVoices.length + ' voz(es) em português disponível(is).';
  } else if (availableVoices.length) {
    $('voiceInfo').textContent =
      'Nenhuma voz em português foi identificada. Confira as opções.';
  } else {
    $('voiceInfo').textContent =
      'O navegador ainda não disponibilizou vozes.';
  }
}

function getSelectedVoice() {
  const selectedURI = $('voiceSelect').value;

  if (selectedURI) {
    const selected = availableVoices.find(
      voice => voice.voiceURI === selectedURI
    );
    if (selected) return selected;
  }

  return availableVoices.find(
    voice => voice.lang.toLowerCase() === 'pt-br'
  ) || availableVoices.find(
    voice => voice.lang.toLowerCase().startsWith('pt')
  ) || null;
}

function stopVoice() {
  if (recognition && listening) {
    try {
      recognition.stop();
    } catch (error) {}
  }

  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }

  currentUtterance = null;
  speaking = false;
}

function cleanSpeechText(text) {
  return String(text)
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/\*(.*?)\*/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/https?:\/\/\S+/g, 'link omitido')
    .replace(/^\s*[-*]\s+/gm, '')
    .trim();
}

function speakText(text, force = false) {
  if (!('speechSynthesis' in window) ||
      typeof SpeechSynthesisUtterance === 'undefined') {
    status('Áudio indisponível neste navegador.');
    return;
  }

  if (!force && !voiceSettings.autoSpeak) return;

  const cleanText = cleanSpeechText(text);
  if (!cleanText) return;

  window.speechSynthesis.cancel();

  const utterance = new SpeechSynthesisUtterance(cleanText);
  currentUtterance = utterance;
  utterance.lang = 'pt-BR';
  utterance.rate = Number(voiceSettings.rate) || 0.95;
  utterance.pitch = voiceSettings.style === 'cinematic' ? 0.82 :
                    voiceSettings.style === 'formal' ? 0.95 : 1.0;

  const selectedVoice = getSelectedVoice();
  if (selectedVoice) {
    utterance.voice = selectedVoice;
    utterance.lang = selectedVoice.lang || 'pt-BR';
  }

  utterance.onstart = () => {
    speaking = true;
    status('FRIDAY está falando...');
  };

  utterance.onend = () => {
    speaking = false;
    if (currentUtterance === utterance) currentUtterance = null;
    status('Sistema online.');
  };

  utterance.onerror = event => {
    speaking = false;
    if (currentUtterance === utterance) currentUtterance = null;
    if (event.error !== 'canceled' && event.error !== 'interrupted') {
      status('Não foi possível reproduzir a voz.');
    }
  };

  window.speechSynthesis.speak(utterance);
}

function speakResponse(text) {
  lastReply = String(text || '');
  speakText(lastReply, false);
}

function addMessage(who, text) {
  const item = document.createElement('div');
  item.className = 'msg ' + (who === 'Você' ? 'user' : 'bot');

  const label = document.createElement('strong');
  label.textContent = who === 'Você' ? 'COMANDANTE' : 'FRIDAY';

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

  let result;
  try {
    result = await response.json();
  } catch (error) {
    throw new Error('Resposta inválida do servidor.');
  }

  if (!response.ok) {
    throw new Error(result.error || 'Falha na solicitação.');
  }

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
    const reply = result.response || 'Comando concluído.';

    addMessage('FRIDAY', reply);
    lastReply = reply;

    if (result.closed) {
      showLogin();
      return;
    }

    speakResponse(reply);
  } catch (error) {
    addMessage('FRIDAY', error.message);
    status('Verifique a conexão ou autenticação.');

    if (error.message.toLowerCase().includes('sessão')) {
      showLogin();
    }
  } finally {
    $('sendBtn').disabled = false;
    $('voiceBtn').disabled = false;

    if (!$('assistant').classList.contains('hidden')) {
      $('command').focus();
    }
  }
});

function startVoice() {
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!SpeechRecognition) {
    status('Reconhecimento indisponível. Tente outro navegador compatível.');
    return;
  }

  if (listening && recognition) {
    recognition.stop();
    return;
  }

  stopVoice();
  recognition = new SpeechRecognition();
  recognition.lang = 'pt-BR';
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  recognition.onstart = () => {
    listening = true;
    $('voiceBtn').classList.add('listening');
    $('voiceBtn').textContent = '⏹ Parar';
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
    $('voiceBtn').textContent = '🎙 Falar';
  };

  try {
    recognition.start();
  } catch (error) {
    status('Não foi possível iniciar o microfone. Tente novamente.');
  }
}

$('voiceBtn').addEventListener('click', startVoice);

$('settingsToggle').addEventListener('click', () => {
  const panel = $('settingsPanel');
  panel.classList.toggle('hidden');

  $('settingsToggle').textContent =
    panel.classList.contains('hidden')
      ? '⚙ Configurações de voz'
      : '✖ Fechar configurações';

  if (!panel.classList.contains('hidden')) loadVoices();
});

$('speechRate').addEventListener('input', () => {
  $('rateValue').textContent =
    Number($('speechRate').value).toFixed(2).replace('.', ',') + '×';
});

$('autoSpeak').addEventListener('change', () => {
  saveSettings(false);
  if (!$('autoSpeak').checked) {
    stopVoice();
    status('Leitura automática desativada.');
  } else {
    status('Leitura automática ativada.');
  }
});

$('speechRate').addEventListener('change', () => saveSettings(false));
$('voiceStyle').addEventListener('change', () => saveSettings(false));
$('voiceSelect').addEventListener('change', () => saveSettings(false));

$('saveSettingsBtn').addEventListener('click', () => saveSettings(true));

$('testVoiceBtn').addEventListener('click', () => {
  saveSettings(false);
  speakText('Olá, comandante. Sou FRIDAY. Configurações de voz prontas.', true);
});

$('readLastBtn').addEventListener('click', () => {
  if (!lastReply) {
    $('settingsNotice').textContent = 'Ainda não existe uma resposta para ler.';
    return;
  }
  saveSettings(false);
  speakText(lastReply, true);
});

$('stopSpeakBtn').addEventListener('click', () => {
  stopVoice();
  status('Fala interrompida.');
});

$('stopSpeakTop').addEventListener('click', () => {
  stopVoice();
  status('Fala interrompida.');
});

function showLogin() {
  stopVoice();
  $('assistant').classList.add('hidden');
  $('login').classList.remove('hidden');
  $('messages').replaceChildren();
  $('settingsPanel').classList.add('hidden');
  $('settingsToggle').textContent = '⚙ Configurações de voz';
  status('Sistema aguardando autenticação');
}

$('logout').addEventListener('click', async () => {
  stopVoice();
  try {
    await request('/logout', {});
  } catch (error) {
    // Encerra a interface mesmo se a conexão falhar.
  }
  showLogin();
});

async function checkSession() {
  try {
    const response = await fetch('/session', {
      credentials: 'same-origin',
      cache: 'no-store'
    });
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

loadSettings();

if ('speechSynthesis' in window) {
  loadVoices();
  window.speechSynthesis.onvoiceschanged = loadVoices;
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

        secure = (
            self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        )

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
                f"{SESSION_COOKIE}=; Path=/; HttpOnly; "
                f"SameSite=Strict; Max-Age=0"
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
            self.send_json(
                200,
                {"authenticated": self.get_session() is not None}
            )
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
            if (
                not isinstance(password, str)
                or not auth.authenticate(password)
            ):
                self.send_json(
                    401,
                    {"error": "Autenticação negada. Aguarde se houver bloqueio."}
                )
                return

            token = secrets.token_urlsafe(32)
            SESSIONS.add(token)
            self.send_json(200, {"ok": True}, cookie=token)
            return

        token = self.get_session()
        if not token:
            self.send_json(
                401,
                {"error": "Sessão inválida. Entre novamente."},
                clear_cookie=True
            )
            return

        if path == "/logout":
            SESSIONS.discard(token)
            CHAT_HISTORY.pop(token, None)
            self.send_json(200, {"ok": True}, clear_cookie=True)
            return

        if path == "/chat":
            command = data.get("command")
            if (
                not isinstance(command, str)
                or not command.strip()
                or len(command) > 2000
            ):
                self.send_json(
                    400,
                    {"error": "Comando vazio ou muito longo."}
                )
                return

            try:
                clean_command = command.strip()
                intention = friday.interpret(clean_command)
                history = CHAT_HISTORY.setdefault(token, [])

                response = friday.execute(
                    intention,
                    clean_command,
                    "owner",
                    history=history,
                )

                if response is None:
                    SESSIONS.discard(token)
                    CHAT_HISTORY.pop(token, None)
                    self.send_json(
                        200,
                        {"response": "FRIDAY encerrada.", "closed": True},
                        clear_cookie=True
                    )
                    return

                if intention == "unknown":
                    from core.ai_provider import FridayAIProvider
                    provider = FridayAIProvider()

                    if (
                        provider.available()
                        and response != "Não consegui obter uma resposta da IA agora."
                    ):
                        history.extend([
                            {"role": "user", "content": clean_command},
                            {"role": "assistant", "content": str(response)},
                        ])
                        CHAT_HISTORY[token] = history[-MAX_HISTORY_MESSAGES:]

                self.send_json(
                    200,
                    {"response": str(response), "closed": False}
                )

            except Exception:
                print("FRIDAY WEB: erro ao processar comando.")
                self.send_json(
                    500,
                    {"error": "Erro interno ao processar comando."}
                )
            return

        self.send_json(404, {"error": "Rota não encontrada."})

    def log_message(self, format, *args):
        print("FRIDAY WEB:", format % args)


if __name__ == "__main__":
    if not os.getenv("FRIDAY_PASSWORD"):
        raise RuntimeError(
            "Configure o segredo FRIDAY_PASSWORD no Replit."
        )

    port = int(os.environ.get("PORT", "5000"))
    server = HTTPServer(("0.0.0.0", port), FridayHandler)
    print(f"FRIDAY online na porta {port}.")
    server.serve_forever()
