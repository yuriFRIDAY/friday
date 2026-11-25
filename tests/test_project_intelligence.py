from datetime import date

from core.project_intelligence import ProjectIntelligence


class FakeManager:
    def list_projects(self):
        return [
            {"id": 1, "name": "FRIDAY", "status": "active"},
            {"id": 2, "name": "Concluído", "status": "completed"},
        ]

    def list_tasks(self, project_id):
        if project_id == 1:
            return [
                {"id": 10, "title": "Tarefa atrasada", "status": "todo",
                 "priority": "medium", "due_date": "2026-11-20", "dependencies": []},
                {"id": 11, "title": "Tarefa bloqueada", "status": "blocked",
                 "priority": "high", "due_date": "2026-11-28", "dependencies": [10]},
                {"id": 12, "title": "Concluída", "status": "done",
                 "priority": "urgent", "due_date": "2026-11-21", "dependencies": []},
            ]
        return []


def test_intelligence_summarizes_risks_and_prioritizes_overdue():
    report = ProjectIntelligence(FakeManager(), today=date(2026, 11, 25)).analyze()

    assert report["summary"]["active_projects"] == 1
    assert report["summary"]["overdue_tasks"] == 1
    assert report["summary"]["blocked_tasks"] == 1
    assert report["projects"][0]["completion_percent"] == 33
    assert report["priority_recommendations"][0]["task_id"] == 10
    assert any(r["type"] == "overdue" for r in report["risks"])
    assert report["mutations_performed"] is False


def test_completed_tasks_are_not_recommended():
    report = ProjectIntelligence(FakeManager(), today=date(2026, 11, 25)).analyze()
    recommended_ids = {item["task_id"] for item in report["priority_recommendations"]}
    assert 12 not in recommended_ids


def test_due_soon_task_is_flagged():
    class DueSoonManager:
        def list_projects(self):
            return [{"id": 3, "name": "Prazo", "status": "active"}]

        def list_tasks(self, project_id):
            return [{"id": 30, "title": "Entrega", "status": "todo",
                     "priority": "low", "due_date": "2026-11-27", "dependencies": []}]

    report = ProjectIntelligence(DueSoonManager(), today=date(2026, 11, 25)).analyze()
    assert report["summary"]["due_soon_tasks"] == 1
    assert report["priority_recommendations"][0]["task_id"] == 30
    assert "Prazo próximo" in report["priority_recommendations"][0]["reasons"]
