"""Testes mínimos para proteger os fixes recentes.

Rodar:
    pip install pytest httpx
    pytest tests/ -v

Cobre:
- SSRF blocked/allowed
- execute_tool propaga erro / requer confirmação
- /health retorna estrutura válida
"""
import os

# Env mínimo ANTES de importar main
os.environ.setdefault("GROQ_API_KEY", "test-key-for-ci")
os.environ.setdefault("DATA_DIR", "/tmp/friday_test")
os.environ.setdefault("ENV", "test")

import pytest
from fastapi.testclient import TestClient

from core import friday_tools
import main as friday_main

client = TestClient(friday_main.app)


# ---------- SSRF ----------
def test_ssrf_localhost_blocked():
    assert not friday_tools._is_safe_url("http://localhost:6379")
    assert not friday_tools._is_safe_url("http://127.0.0.1/")
    assert not friday_tools._is_safe_url("http://[::1]/")

def test_ssrf_metadata_blocked():
    assert not friday_tools._is_safe_url("http://169.254.169.254/latest/meta-data")
    assert not friday_tools._is_safe_url("http://10.0.0.1/")
    assert not friday_tools._is_safe_url("http://192.168.0.1/")

def test_ssrf_public_allowed():
    assert friday_tools._is_safe_url("https://google.com")
    assert friday_tools._is_safe_url("https://api.github.com")

def test_ssrf_blocks_non_http():
    assert not friday_tools._is_safe_url("ftp://example.com")
    assert not friday_tools._is_safe_url("file:///etc/passwd")
    assert not friday_tools._is_safe_url("javascript:alert(1)")


# ---------- execute_tool ----------
def test_execute_tool_unknown():
    r = friday_tools.execute_tool("nonexistent_tool", {}, confirmed=True)
    assert r["ok"] is False
    assert "não encontrada" in r["error"].lower() or "encontrada" in r["error"].lower()

def test_execute_tool_requires_confirmation():
    r = friday_tools.execute_tool(
        "send_email",
        {"to_email": "x@y.com", "subject": "s", "body": "b"},
        confirmed=False,
    )
    assert r["ok"] is False
    assert "onfirma" in r["error"]

def test_execute_tool_propagates_handler_error():
    # URL interna → handler retorna {"error": ...} → execute_tool deve propagar
    r = friday_tools.execute_tool("read_webpage", {"url": "http://localhost/"}, confirmed=True)
    assert r["ok"] is False
    assert r["error"]


# ---------- health ----------
def test_health_endpoint_structure():
    r = client.get("/health")
    assert r.status_code in (200, 503)
    data = r.json()
    assert "status" in data
    assert "checks" in data
    assert "db" in data["checks"]
    assert "groq" in data["checks"]


# ---------- auth ----------
def test_session_requires_auth():
    r = client.get("/session")
    assert r.status_code == 200
    assert r.json()["authenticated"] is False

def test_chat_requires_auth():
    r = client.post("/chat", json={"command": "oi"})
    assert r.status_code == 401
