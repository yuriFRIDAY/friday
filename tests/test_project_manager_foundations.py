"""Regression tests for shared DB, tool authorization and Work OAuth storage."""
import json
import os

import pytest

from core import database
from core.permissions import PermissionManager
from core.friday_tools.registry import execute_tool
from core import work_mode


def test_shared_database_connection_persists_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "shared.sqlite3"
    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    first = database.get_db()
    first.execute("CREATE TABLE IF NOT EXISTS probe (value TEXT)")
    first.execute("INSERT INTO probe(value) VALUES (?)", ("shared",))
    first.commit()
    first.close()

    second = database.get_db()
    row = second.execute("SELECT value FROM probe").fetchone()
    second.close()
    assert row["value"] == "shared"


def test_permissions_deny_guest_tool_access():
    manager = PermissionManager()
    assert not manager.has_permission("guest", "use_tools")
    assert not manager.has_permission("guest", "critical_operations")


def test_owner_has_explicit_critical_permission():
    manager = PermissionManager()
    assert manager.has_permission("owner", "use_tools")
    assert manager.has_permission("owner", "critical_operations")


@pytest.mark.parametrize("tool_name,args", [
    ("send_email", {"to_email": "x@example.com", "subject": "s", "body": "b"}),
    ("reply_to_email", {"to_email": "x@example.com", "subject": "s", "body": "b"}),
    ("delete_calendar_event", {"event_id": "event-1"}),
])
def test_sensitive_tools_require_explicit_confirmation(tool_name, args):
    result = execute_tool(tool_name, args, user_role="owner", confirmed=False)
    assert result["ok"] is False
    assert "confirma" in result["error"].lower()


def test_sensitive_tools_reject_guest_even_if_confirmed():
    result = execute_tool(
        "send_email",
        {"to_email": "x@example.com", "subject": "s", "body": "b"},
        user_role="guest",
        confirmed=True,
    )
    assert result["ok"] is False
    assert "permiss" in result["error"].lower()


def test_work_tokens_are_persisted_atomically(tmp_path, monkeypatch):
    token_path = tmp_path / ".friday_work_tokens.json"
    monkeypatch.setattr(work_mode, "WORK_TOKEN_FILE", str(token_path))
    monkeypatch.setattr(work_mode, "WORK_TOKEN_STORE", {})
    work_mode.save_work_tokens("access", "refresh", 3600)

    persisted = json.loads(token_path.read_text(encoding="utf-8"))
    assert persisted["access_token"] == "access"
    assert persisted["refresh_token"] == "refresh"
    assert not (tmp_path / ".friday_work_tokens.json.tmp").exists()
