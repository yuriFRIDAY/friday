"""Project Manager domain regression tests."""
import pytest

from core import database
from core.project_manager import ProjectManager, ProjectManagerError


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "pm.sqlite3"))
    instance = ProjectManager()
    return instance


def test_create_update_and_list_project(manager):
    project = manager.create_project("FRIDAY", "Projeto pessoal")
    assert project["name"] == "FRIDAY"
    updated = manager.update_project(project["id"], status="paused")
    assert updated["status"] == "paused"
    assert manager.list_projects()[0]["id"] == project["id"]


def test_task_priority_due_date_and_status(manager):
    project = manager.create_project("Projeto")
    task = manager.create_task(project["id"], "Auditar", priority="high", due_date="2026-12-01")
    assert task["priority"] == "high"
    assert task["due_date"] == "2026-12-01"
    updated = manager.update_task(task["id"], status="in_progress")
    assert updated["status"] == "in_progress"


def test_dependency_blocks_completion_and_allows_after_dependency_done(manager):
    project = manager.create_project("Projeto")
    first = manager.create_task(project["id"], "Primeira")
    second = manager.create_task(project["id"], "Segunda")
    manager.add_dependency(second["id"], first["id"])
    with pytest.raises(ProjectManagerError):
        manager.update_task(second["id"], status="done")
    manager.update_task(first["id"], status="done")
    assert manager.update_task(second["id"], status="done")["status"] == "done"


def test_dependency_rejects_cycles_and_cross_project_links(manager):
    one = manager.create_project("Um")
    two = manager.create_project("Dois")
    a = manager.create_task(one["id"], "A")
    b = manager.create_task(one["id"], "B")
    foreign = manager.create_task(two["id"], "Externa")
    manager.add_dependency(a["id"], b["id"])
    with pytest.raises(ProjectManagerError):
        manager.add_dependency(b["id"], a["id"])
    with pytest.raises(ProjectManagerError):
        manager.add_dependency(a["id"], foreign["id"])


def test_project_delete_cascades_tasks(manager):
    project = manager.create_project("Projeto")
    task = manager.create_task(project["id"], "Tarefa")
    assert manager.delete_project(project["id"])
    assert manager.get_task(task["id"]) is None
