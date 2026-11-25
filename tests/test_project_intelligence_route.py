"""Authentication and response tests for the project intelligence endpoint."""
import asyncio

import pytest
from fastapi import HTTPException

import app as friday_app


def test_project_intelligence_endpoint_rejects_unauthenticated_request(monkeypatch):
    monkeypatch.setattr(friday_app.SESSION_STORE, "get", lambda token: None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(friday_app.pm_project_intelligence(None))

    assert exc.value.status_code == 401


def test_project_intelligence_endpoint_returns_read_only_report(monkeypatch):
    expected = {
        "summary": {"active_projects": 1},
        "priority_recommendations": [],
        "mutations_performed": False,
    }

    class FakeIntelligence:
        def __init__(self, manager):
            self.manager = manager

        def analyze(self):
            return expected

    monkeypatch.setattr(friday_app.SESSION_STORE, "get", lambda token: {"user": "owner"})
    monkeypatch.setattr(friday_app, "ProjectIntelligence", FakeIntelligence)

    result = asyncio.run(friday_app.pm_project_intelligence("valid-session"))

    assert result == expected
    assert result["mutations_performed"] is False
