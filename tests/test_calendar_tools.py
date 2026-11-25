"""Regression tests for Google Calendar event handling and OAuth diagnostics."""
import asyncio
import io
import json
from datetime import datetime
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest

import app as friday_app
from core.friday_tools import calendar as calendar_tools


TZ = ZoneInfo("America/Sao_Paulo")
START = datetime(2026, 12, 6, 9, 0, tzinfo=TZ)


def configure_calendar(monkeypatch, **overrides):
    context = {
        "access_token_provider": lambda: "test-access-token",
        "create_event": lambda token, payload: {"id": "google-event-123"},
        "delete_event": lambda token, event_id: {"ok": True},
        "update_event": lambda token, event_id, payload: {"id": event_id},
        "events_query": lambda *args, **kwargs: [],
        "today_events": lambda token: [],
    }
    context.update(overrides)
    monkeypatch.setattr(calendar_tools, "_CALENDAR_CONTEXT", context)
    monkeypatch.setattr(calendar_tools, "_parse_natural_date", lambda value: START if value else None)
    return context


def test_create_calendar_event_sends_sao_paulo_timezone_and_confirms_id(monkeypatch):
    captured = {}
    def create(token, payload):
        captured.update(payload)
        return {"id": "google-event-123"}

    configure_calendar(monkeypatch, create_event=create)
    result = calendar_tools._handler_create_calendar_event("Dia do Kauai", "06/12/2026 às 09:00")

    assert result["ok"] is True
    assert result["event_id"] == "google-event-123"
    assert captured["summary"] == "Dia do Kauai"
    assert captured["start"]["dateTime"].startswith("2026-12-06T09:00:00")
    assert captured["start"]["timeZone"] == "America/Sao_Paulo"


def test_create_calendar_event_propagates_google_permission_error(monkeypatch):
    configure_calendar(monkeypatch, create_event=lambda token, payload: {
        "error": "O Google negou a operação. Verifique as permissões."
    })
    result = calendar_tools._handler_create_calendar_event("Dia do Kauai", "06/12/2026 às 09:00")
    assert result == {"error": "O Google negou a operação. Verifique as permissões."}


def test_create_calendar_event_rejects_unparseable_dates(monkeypatch):
    configure_calendar(monkeypatch)
    monkeypatch.setattr(calendar_tools, "_parse_natural_date", lambda value: None)
    result = calendar_tools._handler_create_calendar_event("Dia do Kauai", "data inválida")
    assert "interpretar" in result["error"].lower()


def test_create_calendar_event_rejects_end_before_start(monkeypatch):
    configure_calendar(monkeypatch)
    earlier = datetime(2026, 12, 6, 8, 0, tzinfo=TZ)
    monkeypatch.setattr(calendar_tools, "_parse_natural_date", lambda value: earlier if value == "08:00" else START)
    result = calendar_tools._handler_create_calendar_event("Dia do Kauai", "09:00", "08:00")
    assert "posterior" in result["error"].lower()


def test_calendar_list_does_not_turn_api_failure_into_empty_agenda(monkeypatch):
    configure_calendar(monkeypatch, events_query=lambda *args, **kwargs: None)
    result = calendar_tools._handler_get_calendar_events()
    assert "error" in result


def test_calendar_api_403_returns_permission_guidance():
    error = HTTPError(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        403,
        "Forbidden",
        {},
        io.BytesIO(json.dumps({"error": {"message": "insufficientPermissions"}}).encode()),
    )
    result = friday_app._calendar_api_error("create event", error)
    assert "negou" in result["error"].lower()
    assert "calendar.events" in result["error"]


def test_calendar_api_401_requests_reauthorization():
    error = HTTPError("https://googleapis.com", 401, "Unauthorized", {}, io.BytesIO(b"{}"))
    result = friday_app._calendar_api_error("create event", error)
    assert "reconecte" in result["error"].lower()


def test_calendar_token_refresh_preserves_existing_scope_and_refresh_token(tmp_path, monkeypatch):
    token_path = tmp_path / "google_tokens.json"
    monkeypatch.setattr(friday_app, "GOOGLE_TOKEN_FILE", str(token_path))
    monkeypatch.setattr(friday_app, "GOOGLE_TOKEN_STORE", {
        "owner": {"refresh_token": "refresh-old", "scope": "https://www.googleapis.com/auth/calendar.events"}
    })
    friday_app._save_owner_calendar_tokens("access-new", None, 3600, "")
    saved = friday_app.GOOGLE_TOKEN_STORE["owner"]
    assert saved["refresh_token"] == "refresh-old"
    assert saved["scope"] == "https://www.googleapis.com/auth/calendar.events"
    assert json.loads(token_path.read_text(encoding="utf-8"))["owner"]["scope"] == saved["scope"]
