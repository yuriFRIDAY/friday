from http.server import BaseHTTPRequestHandler, HTTPServer
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit, parse_qs, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import json
import os
import secrets
import time

from core.assistant import FridayAssistant
from core.auth import FridayAuth
from core.sessions import SessionStore

SESSION_COOKIE = "friday_session"
MAX_BODY = 8192
MAX_HISTORY_MESSAGES = 10
# ========================================
# GOOGLE CALENDAR — OAUTH
# ========================================

GOOGLE_OAUTH_AUTHORIZE_URL = (
    "https://accounts.google.com/o/oauth2/v2/auth"
)
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

GOOGLE_CALENDAR_SCOPE = (
    "https://www.googleapis.com/auth/calendar.events.readonly"
)

OAUTH_STATE_TTL_SECONDS = 600
OAUTH_PENDING_STATES = {}
GOOGLE_TOKEN_STORE = {}

# Estados temporários de autorização mantidos no servidor.
OAUTH_PENDING_STATES = {}
GOOGLE_TOKEN_STORE = {}

GOOGLE_OAUTH_CLIENT_ID = os.getenv(
    "GOOGLE_OAUTH_CLIENT_ID", ""
).strip()

GOOGLE_OAUTH_CLIENT_SECRET = os.getenv(
    "GOOGLE_OAUTH_CLIENT_SECRET", ""
).strip()

GOOGLE_OAUTH_REDIRECT_URI = os.getenv(
    "GOOGLE_OAUTH_REDIRECT_URI", ""
).strip()

SESSION_TTL_SECONDS = 1800
COOKIE_SECURE_MODE = os.getenv(
    "FRIDAY_COOKIE_SECURE",
    "auto",
).strip().lower()
if COOKIE_SECURE_MODE not in {"auto", "always", "never"}:
    raise RuntimeError(
        "FRIDAY_COOKIE_SECURE deve ser auto, always ou never."
    )

auth = FridayAuth()
SESSION_STORE = SessionStore(ttl_seconds=SESSION_TTL_SECONDS)
friday = FridayAssistant()

HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#050914">
<title>FRIDAY | Stark Systems</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #050a13;
    --panel: rgba(10, 19, 32, 0.94);
    --panel-soft: rgba(13, 28, 45, 0.76);
    --cyan: #73edff;
    --cyan-bright: #c2f9ff;
    --blue: #258aff;
    --text: #e8f5ff;
    --muted: #829bb1;
    --line: rgba(104, 211, 255, 0.17);
    --glow: 0 0 32px rgba(25, 125, 220, 0.08);
    --radius: 16px;
  }

  *,
  *::before,
  *::after {
    box-sizing: border-box;
  }

  html {
    min-height: 100%;
    background: var(--bg);
    scroll-behavior: smooth;
  }

  body {
    margin: 0;
    min-height: 100vh;
    color: var(--text);
    background:
      radial-gradient(ellipse at 50% 0%,
        rgba(22, 98, 160, 0.16), transparent 48%),
      linear-gradient(145deg, #050a13, #081321 55%, #040811);
    font-family: Inter, "Segoe UI", Arial, sans-serif;
    font-size: 14px;
    -webkit-font-smoothing: antialiased;
  }

  body::before {
    content: "";
    position: fixed;
    inset: 0;
    z-index: 0;
    pointer-events: none;
    opacity: 0.13;
    background-image:
      linear-gradient(rgba(93, 190, 238, 0.08) 1px, transparent 1px),
      linear-gradient(90deg, rgba(93, 190, 238, 0.08) 1px, transparent 1px);
    background-size: 42px 42px;
    mask-image: linear-gradient(to bottom, #000, transparent 85%);
  }

  button, input, textarea, select {
    font: inherit;
  }

  button {
    min-height: 40px;
    padding: 10px 14px;
    color: var(--cyan-bright);
    background: rgba(13, 43, 67, 0.78);
    border: 1px solid rgba(104, 211, 255, 0.25);
    border-radius: 9px;
    cursor: pointer;
    transition: background 180ms ease, border-color 180ms ease,
      transform 180ms ease;
    -webkit-tap-highlight-color: transparent;
  }

  button:hover:not(:disabled) {
    background: rgba(19, 67, 99, 0.9);
    border-color: rgba(115, 237, 255, 0.65);
  }

  button:active:not(:disabled) {
    transform: translateY(1px);
  }

  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  button:focus-visible,
  input:focus-visible,
  textarea:focus-visible,
  select:focus-visible {
    outline: 2px solid var(--cyan);
    outline-offset: 3px;
  }

  input, textarea, select {
    width: 100%;
    min-width: 0;
    padding: 13px 14px;
    color: var(--text);
    background: rgba(3, 10, 19, 0.86);
    border: 1px solid var(--line);
    border-radius: 9px;
  }

  input::placeholder, textarea::placeholder {
    color: #728ba1;
  }

  input:focus, textarea:focus, select:focus {
    outline: none;
    border-color: rgba(115, 237, 255, 0.65);
    box-shadow: 0 0 0 3px rgba(115, 237, 255, 0.06);
  }

  .hidden {
    display: none !important;
  }

  /* Estrutura geral */

  .shell {
    position: relative;
    z-index: 1;
    width: min(1440px, 100%);
    min-height: 100vh;
    margin: 0 auto;
    padding: 24px 32px 16px;
  }

  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    min-height: 66px;
    margin-bottom: 24px;
    padding: 0 4px 18px;
    border-bottom: 1px solid var(--line);
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 13px;
  }

  .brand-mark {
    display: grid;
    place-items: center;
    width: 42px;
    height: 42px;
    color: var(--cyan);
    font-size: 27px;
    border: 1px solid rgba(115, 237, 255, 0.35);
    border-radius: 50%;
    box-shadow: inset 0 0 15px rgba(37, 138, 255, 0.12);
  }

  .brand-name {
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.2em;
  }

  .brand-sub {
    margin-top: 5px;
    color: var(--muted);
    font-size: 9px;
    letter-spacing: 0.17em;
  }

  .system-chip, .live-label {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: #a8c4d8;
    font-size: 10px;
    letter-spacing: 0.13em;
  }

  .status-dot {
    display: inline-block;
    flex: 0 0 auto;
    width: 7px;
    height: 7px;
    background: #62e8c0;
    border-radius: 50%;
    box-shadow: 0 0 10px rgba(98, 232, 192, 0.55);
  }

  .layout {
    display: grid;
    grid-template-columns: minmax(245px, 0.76fr) minmax(0, 1.8fr);
    align-items: stretch;
    gap: 20px;
  }

  .panel {
    position: relative;
    min-width: 0;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    box-shadow: var(--glow);
  }

  .panel::before {
    content: "";
    position: absolute;
    top: 0;
    left: 22px;
    right: 22px;
    height: 1px;
    background: linear-gradient(90deg, transparent, #65dfff, transparent);
    opacity: 0.55;
    pointer-events: none;
  }

  /* Painel lateral */

  .sidebar {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 28px 22px 22px;
  }

  .reactor-wrap {
    position: relative;
    display: grid;
    place-items: center;
    width: 166px;
    height: 166px;
    margin: 8px 0 23px;
  }

  .reactor {
    position: relative;
    display: grid;
    place-items: center;
    width: 116px;
    height: 116px;
    border: 1px solid rgba(115, 237, 255, 0.64);
    border-radius: 50%;
    box-shadow: 0 0 24px rgba(37, 138, 255, 0.12),
      inset 0 0 22px rgba(37, 138, 255, 0.1);
  }

  .reactor::before, .reactor::after {
    content: "";
    position: absolute;
    inset: 9px;
    border: 1px solid rgba(115, 237, 255, 0.25);
    border-radius: 50%;
  }

  .reactor::after {
    inset: -10px;
    border-color: rgba(37, 138, 255, 0.3);
    border-style: dashed;
  }

  .reactor-inner {
    display: grid;
    place-items: center;
    width: 66px;
    height: 66px;
    color: #d8fbff;
    font-size: 30px;
    background: radial-gradient(circle, #286a94, #0c2741 65%, #071320);
    border: 1px solid var(--cyan);
    border-radius: 50%;
    box-shadow: 0 0 20px rgba(115, 237, 255, 0.2),
      inset 0 0 18px rgba(115, 237, 255, 0.16);
  }

  .orbit {
    position: absolute;
    inset: 0;
    border: 1px solid rgba(115, 237, 255, 0.14);
    border-radius: 50%;
  }

  .orbit::before, .orbit::after {
    content: "";
    position: absolute;
    background: var(--cyan);
    border-radius: 50%;
    box-shadow: 0 0 10px rgba(115, 237, 255, 0.6);
  }

  .orbit::before {
    width: 5px;
    height: 5px;
    top: 18px;
    left: 27px;
  }

  .orbit::after {
    width: 4px;
    height: 4px;
    right: 18px;
    bottom: 37px;
  }

  .system-title {
    margin: 0;
    color: var(--cyan-bright);
    font-size: 26px;
    font-weight: 500;
    letter-spacing: 0.25em;
  }

  .system-sub {
    margin-top: 9px;
    color: var(--muted);
    font-size: 9px;
    letter-spacing: 0.12em;
    text-align: center;
  }

  .system-state {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 19px 0 28px;
    padding: 9px 12px;
    color: #9fead8;
    background: rgba(52, 181, 145, 0.07);
    border: 1px solid rgba(98, 232, 192, 0.16);
    border-radius: 30px;
    font-size: 9px;
    letter-spacing: 0.1em;
  }

  .mini-heading {
    width: 100%;
    margin-bottom: 9px;
    color: #7593aa;
    font-size: 9px;
    letter-spacing: 0.2em;
  }

  .metric {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    width: 100%;
    padding: 13px 0;
    border-bottom: 1px solid rgba(104, 211, 255, 0.09);
    font-size: 11px;
  }

  .metric-label {
    color: var(--muted);
  }

  .metric-value {
    color: #b9e8fa;
    font-size: 9px;
    letter-spacing: 0.08em;
    text-align: right;
  }

  .sidebar-note {
    width: 100%;
    margin-top: auto;
    padding-top: 24px;
    color: var(--muted);
    font-size: 10px;
    line-height: 1.9;
  }

  .sidebar-note span {
    color: #a9dced;
    font-size: 9px;
    letter-spacing: 0.13em;
  }

  /* Área principal */

  .main-panel {
    display: flex;
    flex-direction: column;
    padding: 26px;
  }

  .main-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    padding-bottom: 20px;
    border-bottom: 1px solid var(--line);
  }

  .heading {
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.15em;
  }

  .heading-sub {
    margin-top: 7px;
    color: var(--muted);
    font-size: 11px;
  }

  .live-label {
    padding: 8px 10px;
    border: 1px solid var(--line);
    border-radius: 6px;
    white-space: nowrap;
  }

  #status {
    margin: 16px 0;
    padding: 10px 12px;
    color: #9bb8cc;
    background: rgba(3, 12, 23, 0.55);
    border-left: 2px solid rgba(115, 237, 255, 0.65);
    font-size: 11px;
    line-height: 1.6;
  }

  /* Autenticação */

  #login {
    width: min(100%, 440px);
    margin: auto;
    padding: 34px 0 38px;
  }

  .login-symbol {
    color: var(--cyan);
    font-size: 9px;
    letter-spacing: 0.14em;
    line-height: 1.8;
  }

  .login-title {
    margin: 18px 0 10px;
    font-size: clamp(25px, 3vw, 34px);
    font-weight: 500;
    letter-spacing: 0.04em;
  }

  .login-copy {
    margin: 0 0 25px;
    color: var(--muted);
    font-size: 13px;
    line-height: 1.8;
  }

  #loginForm {
    display: grid;
    gap: 12px;
  }

  .primary {
    width: 100%;
    min-height: 47px;
    color: #04111c;
    background: linear-gradient(100deg, #66dfff, #9cefff);
    border: 1px solid #a5f3ff;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.13em;
  }

  .primary:hover:not(:disabled) {
    background: #c0f6ff;
    box-shadow: 0 0 20px rgba(115, 237, 255, 0.12);
  }

  .lockout-notice {
    color: #ffb9b9;
    font-size: 12px;
    line-height: 1.7;
  }

  /* Ferramentas de voz */

  .toolbar {
    display: flex;
    flex-wrap: wrap;
    gap: 9px;
    margin-bottom: 17px;
  }

  .toolbar button, .setting-buttons button {
    font-size: 10px;
  }

  .settings {
    margin-bottom: 22px;
    padding: 19px;
    background: rgba(3, 11, 21, 0.75);
    border: 1px solid var(--line);
    border-radius: 12px;
  }

  .settings h2 {
    margin: 0 0 19px;
    color: var(--cyan);
    font-size: 10px;
    letter-spacing: 0.14em;
  }

  .setting {
    margin-bottom: 19px;
  }

  .setting label {
    display: block;
    margin-bottom: 9px;
    color: #d5e7f2;
    font-size: 12px;
  }

  .setting small {
    display: block;
    margin-top: 8px;
    color: var(--muted);
    font-size: 10px;
    line-height: 1.7;
  }

  .toggle-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .toggle-row label {
    margin: 0;
  }

  input[type="checkbox"] {
    width: 18px;
    height: 18px;
    accent-color: var(--cyan);
  }

  input[type="range"] {
    padding: 0;
    accent-color: var(--cyan);
  }

  .setting-buttons {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 18px 0;
  }

  #settingsNotice {
    margin-top: 10px;
    color: var(--cyan);
    font-size: 11px;
  }

  /* Histórico da conversa */

  .chat-label {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    margin: 6px 0 12px;
    color: #9ab6c9;
    font-size: 9px;
    letter-spacing: 0.12em;
  }

  #messages {
    display: flex;
    flex-direction: column;
    gap: 13px;
    min-height: 280px;
    max-height: 46vh;
    overflow-y: auto;
    padding: 17px;
    background: rgba(3, 10, 19, 0.62);
    border: 1px solid var(--line);
    border-radius: 12px;
    scrollbar-color: #235e82 transparent;
    scrollbar-width: thin;
  }

  #messages:empty::before {
    content: "CANAL DISPONÍVEL — AGUARDANDO COMUNICAÇÃO";
    display: block;
    margin: auto;
    color: #526f84;
    font-size: 10px;
    letter-spacing: 0.1em;
    line-height: 1.9;
    text-align: center;
  }

  #messages::-webkit-scrollbar {
    width: 5px;
  }

  #messages::-webkit-scrollbar-thumb {
    background: #235e82;
    border-radius: 8px;
  }

  .msg {
    display: flex;
    flex-direction: column;
    gap: 7px;
    max-width: 90%;
    padding: 13px 15px;
    border: 1px solid var(--line);
    border-radius: 10px;
    font-size: 13px;
    line-height: 1.8;
    overflow-wrap: anywhere;
    white-space: pre-wrap;
  }

  .msg strong {
    color: var(--cyan);
    font-size: 9px;
    letter-spacing: 0.14em;
  }

  .user {
    align-self: flex-end;
    background: rgba(18, 67, 105, 0.30);
    border-color: rgba(57, 151, 226, 0.27);
  }

  .user strong {
    color: #8ecbff;
  }

  .bot {
    align-self: flex-start;
    background: rgba(12, 35, 52, 0.48);
    border-color: rgba(104, 211, 255, 0.18);
  }

  /* Compositor de comandos */

  .composer {
    margin-top: 18px;
    padding: 17px;
    background: rgba(8, 20, 33, 0.68);
    border: 1px solid var(--line);
    border-radius: 12px;
  }

  .composer-title {
    margin-bottom: 12px;
    color: #94b4c9;
    font-size: 9px;
    letter-spacing: 0.16em;
  }

  #chatForm {
    display: grid;
    gap: 11px;
  }

  .controls {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 9px;
  }

  #command {
    min-height: 46px;
  }

  #voiceBtn.listening {
    color: #fff;
    background: rgba(150, 45, 60, 0.8);
    border-color: rgba(255, 130, 150, 0.55);
  }

  #logout {
    align-self: flex-start;
    margin-top: 15px;
    color: #b2c2d0;
    background: transparent;
    border-color: rgba(140, 170, 190, 0.18);
    box-shadow: none;
    font-size: 10px;
  }

  footer {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    margin-top: 22px;
    padding: 16px 4px 0;
    color: #637d91;
    font-size: 9px;
    letter-spacing: 0.1em;
  }

  footer span {
    color: #a0c9dc;
  }

  /* Responsividade */

  @media (min-width: 1100px) {
    .sidebar {
      min-height: 610px;
    }

    .main-panel {
      min-height: 610px;
    }
  }

  @media (max-width: 850px) {
    .shell {
      padding: 18px;
    }

    .layout {
      grid-template-columns: minmax(205px, 0.7fr) minmax(0, 1.5fr);
      gap: 13px;
    }

    .sidebar {
      padding: 22px 15px;
    }

    .main-panel {
      padding: 19px;
    }

    .reactor-wrap {
      transform: scale(0.85);
      margin: 0 0 12px;
    }
  }

  @media (max-width: 640px) {
    .shell {
      padding: 14px 12px 12px;
    }

    .topbar {
      min-height: 55px;
      margin-bottom: 14px;
      padding-bottom: 14px;
    }

    .brand-mark {
      width: 35px;
      height: 35px;
      font-size: 22px;
    }

    .brand-name {
      font-size: 11px;
    }

    .brand-sub {
      font-size: 8px;
    }

    .system-chip {
      font-size: 8px;
      letter-spacing: 0.06em;
    }

    .layout {
      grid-template-columns: minmax(0, 1fr);
      gap: 13px;
    }

    .sidebar {
      display: grid;
      grid-template-columns: 86px minmax(0, 1fr);
      align-items: center;
      justify-items: start;
      column-gap: 14px;
      padding: 16px;
    }

    .reactor-wrap {
      grid-column: 1;
      grid-row: 1 / span 4;
      width: 86px;
      height: 86px;
      margin: 0;
      transform: none;
    }

    .reactor {
      width: 65px;
      height: 65px;
    }

    .reactor::before {
      inset: 6px;
    }

    .reactor::after {
      inset: -6px;
    }

    .reactor-inner {
      width: 39px;
      height: 39px;
      font-size: 19px;
    }

    .system-title {
      align-self: end;
      font-size: 22px;
      letter-spacing: 0.18em;
    }

    .system-sub {
      align-self: start;
      margin-top: 5px;
      font-size: 8px;
      text-align: left;
      line-height: 1.6;
    }

    .system-state {
      margin: 7px 0 0;
      padding: 6px 8px;
      font-size: 8px;
    }

    .mini-heading,
    .metric,
    .sidebar-note {
      display: none;
    }

    .main-panel {
      min-height: 0;
      padding: 16px 13px;
    }

    .main-header {
      padding-bottom: 14px;
    }

    .heading {
      font-size: 12px;
    }

    .heading-sub {
      font-size: 10px;
    }

    .live-label {
      padding: 7px;
      font-size: 8px;
    }

    #status {
      margin: 12px 0;
      font-size: 10px;
    }

    #login {
      padding: 22px 0;
    }

    .login-title {
      font-size: 27px;
    }

    #messages {
      min-height: 240px;
      max-height: 42vh;
      padding: 12px;
    }

    .msg {
      max-width: 96%;
      padding: 11px;
      font-size: 12px;
    }

    .composer {
      padding: 12px;
    }

    .controls {
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 7px;
    }

    #command {
      padding: 12px 10px;
    }

    .controls button {
      padding: 9px;
      font-size: 11px;
    }

    .toolbar button {
      flex: 1;
    }

    .settings {
      padding: 13px;
    }

    .setting-buttons button {
      flex: 1 1 40%;
    }

    footer {
      flex-wrap: wrap;
      font-size: 8px;
    }
  }

  @media (prefers-reduced-motion: no-preference) {
    .reactor-inner {
      animation: reactor-breathe 4s ease-in-out infinite;
    }

    .orbit {
      animation: orbit-turn 24s linear infinite;
    }

    @keyframes reactor-breathe {
      0%, 100% {
        box-shadow: 0 0 12px rgba(115, 237, 255, 0.15),
          inset 0 0 12px rgba(115, 237, 255, 0.08);
      }
      50% {
        box-shadow: 0 0 25px rgba(115, 237, 255, 0.3),
          inset 0 0 18px rgba(115, 237, 255, 0.16);
      }
    }

    @keyframes orbit-turn {
      to { transform: rotate(360deg); }
    }
  }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation: none !important;
      scroll-behavior: auto !important;
      transition: none !important;
    }
  }
  .awareness-panel {
    margin: 0 0 20px;
    padding: 18px;
    background: rgba(3, 12, 23, 0.62);
    border: 1px solid var(--line);
    border-radius: 12px;
  }

  .awareness-panel h2 {
    margin: 0 0 16px;
    color: var(--cyan);
    font-size: 10px;
    letter-spacing: 0.14em;
  }

  .awareness-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 11px 0;
    border-bottom: 1px solid rgba(104, 211, 255, 0.09);
  }

  .awareness-item span {
    color: var(--muted);
    font-size: 10px;
    letter-spacing: 0.08em;
  }

  .awareness-item strong,
  .location-details strong {
    color: var(--cyan-bright);
    overflow-wrap: anywhere;
  }

  .awareness-item strong {
    text-align: right;
    font-size: 12px;
  }

  .location-details {
    margin-top: 14px;
    padding: 12px;
    background: rgba(13, 28, 45, 0.76);
    border-radius: 8px;
    font-size: 12px;
    line-height: 1.8;
  }

  .location-details p,
  .awareness-note {
    color: var(--muted);
    font-size: 10px;
    line-height: 1.7;
  }

  .awareness-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 14px;
  }

  .awareness-actions button {
    flex: 1 1 140px;
    font-size: 11px;
  }

  .awareness-note {
    margin: 12px 0 0;
  }

  @media (max-width: 640px) {
    .awareness-panel {
      padding: 13px;
    }

    .awareness-item {
      align-items: flex-start;
      flex-direction: column;
      gap: 6px;
    }

    .awareness-item strong {
      text-align: left;
    }
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
      <section class="awareness-panel" aria-labelledby="awarenessTitle">
        <h2 id="awarenessTitle">/// CONSCIÊNCIA SITUACIONAL</h2>

        <div class="awareness-item">
          <span>DATA</span>
          <strong id="currentDate">Aguardando relógio...</strong>
        </div>

        <div class="awareness-item">
          <span>HORÁRIO DE BRASÍLIA</span>
          <strong id="currentTime">--:--:--</strong>
        </div>

        <div class="awareness-item">
          <span>LOCALIZAÇÃO</span>
          <strong id="locationStatus">Não solicitada</strong>
        </div>

        <div id="locationDetails" class="location-details hidden">
          <div>Latitude: <strong id="latitude">—</strong></div>
          <div>Longitude: <strong id="longitude">—</strong></div>
          <p id="locationAccuracy"></p>
        </div>

        <div class="awareness-actions">
          <button id="requestLocation" type="button">
            Autorizar localização
          </button>
          <button id="clearLocation" type="button">
            Ocultar localização
          </button>
        </div>

        <p class="awareness-note">
          O horário usa o fuso de Brasília. A localização só é solicitada
          quando você pressiona o botão. As coordenadas ficam nesta interface
          e não são enviadas à FRIDAY.
        </p>
      </section>

      <section id="login">
        <div class="login-symbol">/// ACESSO RESTRITO · STARK SYSTEMS</div>
        <h2 class="login-title">Autenticação</h2>
        <p class="login-copy">Identifique-se para iniciar uma sessão segura com a FRIDAY.</p>
        <form id="loginForm">
          <input id="password" type="password" placeholder="Senha de acesso"
            autocomplete="current-password" required>
          <button class="primary" type="submit">INICIAR SESSÃO ↗</button>
        </form>
        <p id="lockoutNotice" class="lockout-notice hidden" role="alert"
          aria-live="assertive"></p>
      </section>

      <section id="assistant" class="hidden assistant-content">
        <div class="toolbar">
          <button id="settingsToggle" type="button">⚙ Configurações de voz</button>
          <button id="stopSpeakTop" type="button">⏹ Parar voz</button>
          <button id="calendarConnect" type="button">📅 Google Calendar</button>
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
let speechQueue = [];
let speechRunId = 0;
let lockoutInterval = null;
let lockoutEndsAt = 0;

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
// ========================================
// CONSCIÊNCIA TEMPORAL E LOCALIZAÇÃO
// ========================================

let locationRequestInProgress = false;
let currentCoordinates = null;
let clockInterval = null;

function updateFridayClock() {
  const now = new Date();

  $('currentDate').textContent = new Intl.DateTimeFormat('pt-BR', {
    timeZone: 'America/Sao_Paulo',
    weekday: 'long',
    day: '2-digit',
    month: 'long',
    year: 'numeric'
  }).format(now);

  $('currentTime').textContent = new Intl.DateTimeFormat('pt-BR', {
    timeZone: 'America/Sao_Paulo',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).format(now);
}

function clearFridayLocation(message = 'Localização ocultada') {
  currentCoordinates = null;

  $('latitude').textContent = '—';
  $('longitude').textContent = '—';
  $('locationAccuracy').textContent = '';
  $('locationDetails').classList.add('hidden');
  $('locationStatus').textContent = message;

  locationRequestInProgress = false;
  $('requestLocation').disabled = false;
}

function requestFridayLocation() {
  if (locationRequestInProgress) return;

  if (!('geolocation' in navigator)) {
    $('locationStatus').textContent =
      'Geolocalização não suportada neste navegador.';
    return;
  }

  if (!window.isSecureContext) {
    $('locationStatus').textContent =
      'É necessária uma conexão segura HTTPS.';
    return;
  }

  locationRequestInProgress = true;
  $('requestLocation').disabled = true;
  $('locationStatus').textContent = 'Aguardando autorização do dispositivo...';

  navigator.geolocation.getCurrentPosition(
    position => {
      const latitude = position.coords.latitude;
      const longitude = position.coords.longitude;
      const accuracy = position.coords.accuracy;

      currentCoordinates = {
        latitude,
        longitude,
        accuracy
      };

      $('latitude').textContent = latitude.toFixed(6);
      $('longitude').textContent = longitude.toFixed(6);
      $('locationAccuracy').textContent =
        `Precisão estimada: ${Math.round(accuracy)} metros. ` +
        'Endereço e cidade não foram consultados.';

      $('locationDetails').classList.remove('hidden');
      $('locationStatus').textContent = 'Localização obtida neste dispositivo.';

      locationRequestInProgress = false;
      $('requestLocation').disabled = false;
    },
    error => {
      const messages = {
        1: 'Permissão de localização negada.',
        2: 'A localização não está disponível.',
        3: 'A solicitação de localização expirou.'
      };

      clearFridayLocation(
        messages[error.code] || 'Não foi possível obter a localização.'
      );
    },
    {
      enableHighAccuracy: false,
      timeout: 15000,
      maximumAge: 60000
    }
  );
}

$('requestLocation').addEventListener('click', requestFridayLocation);

$('clearLocation').addEventListener('click', () => {
  clearFridayLocation();
});

updateFridayClock();

if (clockInterval !== null) {
  window.clearInterval(clockInterval);
}

clockInterval = window.setInterval(updateFridayClock, 1000);

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

speechRunId++;
speechQueue = [];
currentUtterance = null;


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

  const synth = window.speechSynthesis;

  // Invalida qualquer reprodução anterior.
  speechRunId++;
  const runId = speechRunId;
  speechQueue = [];
  synth.cancel();

  // Divide o texto em blocos para melhorar a reprodução longa.
  const sentences = cleanText.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [cleanText];

  let chunk = '';

  for (const sentence of sentences) {
    const part = sentence.trim();
    if (!part) continue;

    if (chunk && (chunk.length + part.length + 1 > 220)) {
      speechQueue.push(chunk);
      chunk = '';
    }

    // Divide frases excepcionalmente longas sem perder palavras.
    if (part.length > 220) {
      const words = part.split(/\s+/);

      for (const word of words) {
        if (chunk && chunk.length + word.length + 1 > 220) {
          speechQueue.push(chunk);
          chunk = '';
        }

        chunk += (chunk ? ' ' : '') + word;
      }
    } else {
      chunk += (chunk ? ' ' : '') + part;
    }
  }

  if (chunk) speechQueue.push(chunk);

  const selectedVoice = getSelectedVoice();

  function speakNext() {
    if (runId !== speechRunId) return;

    if (!speechQueue.length) {
      currentUtterance = null;
      speaking = false;
      status('Sistema online.');
      return;
    }

    const nextText = speechQueue.shift();
    const utterance = new SpeechSynthesisUtterance(nextText);

    currentUtterance = utterance;
    utterance.lang = selectedVoice?.lang || 'pt-BR';
    utterance.rate = Number(voiceSettings.rate) || 0.95;
    utterance.pitch =
      voiceSettings.style === 'cinematic' ? 0.82 :
      voiceSettings.style === 'formal' ? 0.95 : 1.0;

    if (selectedVoice) utterance.voice = selectedVoice;

    utterance.onstart = () => {
      if (runId !== speechRunId) return;
      speaking = true;
      status('FRIDAY está falando...');
    };

    utterance.onend = () => {
      if (runId !== speechRunId) return;
      speakNext();
    };

    utterance.onerror = event => {
      if (runId !== speechRunId) return;

      if (event.error === 'canceled' ||
          event.error === 'interrupted') return;

      speechQueue = [];
      currentUtterance = null;
      speaking = false;
      status('Não foi possível reproduzir a voz.');
    };

    synth.speak(utterance);
  }

  speakNext();
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
    const requestError = new Error('Resposta inválida do servidor.');
    requestError.status = response.status;
    requestError.remaining = 0;
    throw requestError;
  }

  if (!response.ok) {
    const requestError = new Error(
      (result && result.error) || 'Falha na solicitação.'
    );
    requestError.status = response.status;
    requestError.remaining = result && result.remaining;
    throw requestError;
  }

  return result;
}

function startLockout(remaining) {
  const parsedSeconds = Number(remaining);
  const seconds = Number.isFinite(parsedSeconds)
    ? Math.max(0, Math.ceil(parsedSeconds))
    : 0;
  const notice = $('lockoutNotice');

  window.clearInterval(lockoutInterval);
  lockoutInterval = null;

  if (seconds === 0) {
    lockoutEndsAt = 0;
    notice.classList.add('hidden');
    return;
  }

  lockoutEndsAt = Date.now() + seconds * 1000;
  notice.classList.remove('hidden');

  const updateNotice = () => {
    const secondsLeft = Math.max(
      0,
      Math.ceil((lockoutEndsAt - Date.now()) / 1000)
    );

    if (secondsLeft === 0) {
      window.clearInterval(lockoutInterval);
      lockoutInterval = null;
      lockoutEndsAt = 0;
      notice.textContent = 'Bloqueio encerrado. Você pode tentar novamente.';
      return;
    }

    notice.textContent =
      `Acesso temporariamente bloqueado. Tente novamente em ${secondsLeft} s.`;
  };

  updateNotice();
  lockoutInterval = window.setInterval(updateNotice, 1000);
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
    if (error.status === 429) {
      startLockout(error.remaining);
    } else {
      $('password').focus();
    }
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
$('calendarConnect').addEventListener('click', () => {
  window.location.href = '/calendar/connect';
});


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

    def get_session(self):
        cookie = SimpleCookie()

        try:
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie.get(SESSION_COOKIE)

            if token and SESSION_STORE.get(token.value):
                return token.value

        except (CookieError, TypeError, ValueError):
            pass

        return None

    def request_scheme(self):
        forwarded = self.headers.get(
            "X-Forwarded-Proto", ""
        ).split(",", 1)[0].strip().lower()

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

        configured_origins = os.getenv(
            "FRIDAY_ALLOWED_ORIGINS", ""
        ).strip()

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
        expected_origin = self.normalize_origin(
            f"{self.request_scheme()}://{host}"
        )

        return bool(
            expected_origin and normalized_origin == expected_origin
        )

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None

        if length <= 0 or length > MAX_BODY:
            return None

        try:
            data = json.loads(
                self.rfile.read(length).decode("utf-8")
            )
            return data if isinstance(data, dict) else None

        except (ValueError, UnicodeDecodeError):
            return None

    def do_GET(self):
        path = urlsplit(self.path).path

        if path == "/":
            body = HTML.encode("utf-8")

            self.send_response(200)
            self.send_header(
                "Content-Type", "text/html; charset=utf-8"
            )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/session":
            token = self.get_session()
            self.send_json(
                200,
                {"authenticated": token is not None},
                clear_cookie=token is None,
            )
            return

        if path == "/calendar/connect":
            token = self.get_session()

            if not token:
                self.send_json(
                    401,
                    {
                        "error": (
                            "Entre na FRIDAY antes de conectar "
                            "o calendário."
                        )
                    },
                    clear_cookie=True,
                )
                return

            if not (
                GOOGLE_OAUTH_CLIENT_ID
                and GOOGLE_OAUTH_REDIRECT_URI
            ):
                self.send_json(
                    503,
                    {
                        "error": (
                            "A integração com o Google ainda "
                            "não está configurada."
                        )
                    },
                )
                return

            state = secrets.token_urlsafe(32)
            now = time.time()

            # Remove estados expirados.
            for old_state, details in list(
                OAUTH_PENDING_STATES.items()
            ):
                if (
                    now - details["created_at"]
                    > OAUTH_STATE_TTL_SECONDS
                ):
                    OAUTH_PENDING_STATES.pop(old_state, None)

            OAUTH_PENDING_STATES[state] = {
                "session_token": token,
                "created_at": now,
            }

            params = {
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
                "response_type": "code",
                "scope": GOOGLE_CALENDAR_SCOPE,
                "state": state,
                "access_type": "offline",
                "prompt": "consent",
            }

            authorization_url = (
                f"{GOOGLE_OAUTH_AUTHORIZE_URL}?"
                f"{urlencode(params)}"
            )

            self.send_response(302)
            self.send_header("Location", authorization_url)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            return

    def handle_google_callback(self):
        query = parse_qs(urlsplit(self.path).query)
        state = query.get("state", [""])[0]
        code = query.get("code", [""])[0]
        google_error = query.get("error", [""])[0]

        if google_error:
            self.send_json(
                400,
                {"error": "A autorização do Google foi cancelada ou negada."},
            )
            return

        if not state or not code:
            self.send_json(
                400,
                {"error": "Resposta de autorização incompleta."},
            )
            return

        pending = OAUTH_PENDING_STATES.pop(state, None)

        if not pending:
            self.send_json(
                400,
                {"error": "Estado de autorização inválido ou já utilizado."},
            )
            return

        if time.time() - pending["created_at"] > OAUTH_STATE_TTL_SECONDS:
            self.send_json(
                400,
                {"error": "A autorização expirou. Tente novamente."},
            )
            return

        session_token = self.get_session()

        if (
            not session_token
            or session_token != pending["session_token"]
            or not SESSION_STORE.get(session_token)
        ):
            self.send_json(
                401,
                {"error": "Sua sessão expirou. Entre novamente na FRIDAY."},
                clear_cookie=True,
            )
            return

        if not (
            GOOGLE_OAUTH_CLIENT_ID
            and GOOGLE_OAUTH_CLIENT_SECRET
            and GOOGLE_OAUTH_REDIRECT_URI
        ):
            self.send_json(
                503,
                {"error": "A configuração OAuth do Google está incompleta."},
            )
            return

        token_data = urlencode({
            "code": code,
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        }).encode("utf-8")

        request = Request(
            GOOGLE_OAUTH_TOKEN_URL,
            data=token_data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=15) as response:
                result = json.loads(response.read(65536).decode("utf-8"))

            access_token = result.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise ValueError("Resposta sem access token válido.")

            # Guarda os tokens apenas na memória do servidor por enquanto.
            # Não envie tokens ou segredos ao navegador.
            GOOGLE_TOKEN_STORE[session_token] = {
                "access_token": access_token,
                "refresh_token": result.get("refresh_token"),
                "expires_at": time.time() + int(result.get("expires_in", 3600)),
                "scope": result.get("scope", ""),
            }

        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
            print("FRIDAY WEB: falha na troca do código OAuth do Google.")
            self.send_json(
                502,
                {"error": "Não foi possível concluir a autorização do Google."},
            )
            return

        self.send_json(
            200,
            {"ok": True, "message": "Google Calendar autorizado pela FRIDAY."},
        )
    def do_POST(self):
        path = urlsplit(self.path).path

        if not self.valid_origin():
            self.send_json(
                403, {"error": "Origem não autorizada."}
            )
            return

        data = self.read_json()

        if data is None:
            self.send_json(
                400, {"error": "Solicitação inválida."}
            )
            return

        if path == "/login":
            password = data.get("password")
            client_key = self.client_address[0]

            authenticated = (
                isinstance(password, str)
                and auth.authenticate(
                    password, client_key=client_key
                )
            )

            if not authenticated:
                remaining = auth.remaining_lockout(client_key)
                locked = remaining > 0

                self.send_json(
                    429 if locked else 401,
                    {
                        "error": (
                            "Acesso temporariamente bloqueado."
                            if locked
                            else "Autenticação negada."
                        ),
                        "locked": locked,
                        "remaining": remaining,
                    },
                )
                return

            token = SESSION_STORE.create()
            self.send_json(
                200, {"ok": True}, cookie=token
            )
            return

        token = self.get_session()

        if not token:
            self.send_json(
                401,
                {"error": "Sessão inválida. Entre novamente."},
                clear_cookie=True,
            )
            return

        if path == "/logout":
            SESSION_STORE.invalidate(token)
            self.send_json(
                200, {"ok": True}, clear_cookie=True
            )
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
                    {"error": "Comando vazio ou muito longo."},
                )
                return

            try:
                clean_command = command.strip()
                history = SESSION_STORE.get_history(token)
                session = SESSION_STORE.get(token)

                if history is None or session is None:
                    self.send_json(
                        401,
                        {
                            "error": (
                                "Sessão inválida. Entre novamente."
                            )
                        },
                        clear_cookie=True,
                    )
                    return

                normalized_command = clean_command.casefold()

                if session.pending_action is not None:
                    if normalized_command == "atualizar lembrança":
                        intention = "confirm_memory_update"
                    elif normalized_command == "manter ambas":
                        intention = "confirm_memory_keep_both"
                    elif normalized_command == "cancelar operação":
                        intention = "cancel_pending"
                    else:
                        intention = friday.interpret(clean_command)
                else:
                    intention = friday.interpret(clean_command)

                response = friday.execute(
                    intention,
                    clean_command,
                    "owner",
                    history=history,
                    session=session,
                )

                if response is None:
                    SESSION_STORE.invalidate(token)
                    self.send_json(
                        200,
                        {
                            "response": "FRIDAY encerrada.",
                            "closed": True,
                        },
                        clear_cookie=True,
                    )
                    return

                if intention == "unknown":
                    from core.ai_provider import FridayAIProvider

                    provider = FridayAIProvider()

                    if (
                        provider.available()
                        and response
                        != "Não consegui obter uma resposta da IA agora."
                    ):
                        history.extend(
                            [
                                {
                                    "role": "user",
                                    "content": clean_command,
                                },
                                {
                                    "role": "assistant",
                                    "content": str(response),
                                },
                            ]
                        )
                        history[:] = history[-MAX_HISTORY_MESSAGES:]

                self.send_json(
                    200,
                    {
                        "response": str(response),
                        "closed": False,
                    },
                )

            except Exception:
                print("FRIDAY WEB: erro ao processar comando.")
                self.send_json(
                    500,
                    {
                        "error": (
                            "Erro interno ao processar comando."
                        )
                    },
                )
            return

        if path == "/oauth/google/callback":
            self.handle_google_callback()
            return
        
        self.send_json(
            404, {"error": "Página não encontrada."}
        )

    def log_message(self, format, *args):
        print("FRIDAY WEB:", format % args)


if __name__ == "__main__":
    if not os.getenv("FRIDAY_PASSWORD"):
        raise RuntimeError(
            "Configure o segredo FRIDAY_PASSWORD no Replit."
        )

    port = int(os.environ.get("PORT", "5000"))

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True

    server = ReusableHTTPServer(
        ("0.0.0.0", port), FridayHandler
    )

    print(f"FRIDAY online em http://0.0.0.0:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nFRIDAY encerrada.")
        server.server_close()
