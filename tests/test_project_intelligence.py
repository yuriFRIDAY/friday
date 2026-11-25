from datetime import date

from core.project_intelligence import ProjectIntelligence


class FakeManager:
    def list_projects(self):
        return [
            {"id": 1, "name": "FRIDAY", "status": "active"},
            {"id": 3, "name": "Em espera", "status": "paused"},
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
                {"id": 13, "title": "Cancelada", "status": "cancelled",
                 "priority": "urgent", "due_date": "2026-11-20", "dependencies": []},
                {"id": 14, "title": "Data inválida", "status": "todo",
                 "priority": "low", "due_date": "não-é-data", "dependencies": []},
            ]
        if project_id == 3:
            return [
                {"id": 30, "title": "Tarefa pausada", "status": "todo",
                 "priority": "low", "due_date": None, "dependencies": []},
            ]
        return []


def test_intelligence_summarizes_risks_and_uses_score_order():
    report = ProjectIntelligence(FakeManager(), today=date(2026, 11, 25)).analyze()

    assert report["summary"]["active_projects"] == 1
    assert report["summary"]["overdue_tasks"] == 1
    assert report["summary"]["blocked_tasks"] == 1
    assert report["summary"]["open_tasks"] == 4
    assert report["summary"]["completed_tasks"] == 1
    assert {p["id"] for p in report["projects"]} == {1, 3}
    assert report["projects"][0]["completion_percent"] == 20
    recommendations = report["priority_recommendations"]
    assert recommendations[0]["task_id"] == 11
    assert recommendations[0]["score"] > next(
        item["score"] for item in recommendations if item["task_id"] == 10
    )
    assert any(item["task_id"] == 10 for item in recommendations)
    assert any(r["type"] == "overdue" for r in report["risks"])
    assert report["mutations_performed"] is False


def test_completed_and_cancelled_tasks_are_not_recommended():
    report = ProjectIntelligence(FakeManager(), today=date(2026, 11, 25)).analyze()
    recommended_ids = {item["task_id"] for item in report["priority_recommendations"]}
    assert 12 not in recommended_ids
    assert 13 not in recommended_ids


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


def test_invalid_due_date_is_ignored_without_crashing():
    class InvalidDateManager:
        def list_projects(self):
            return [{"id": 4, "name": "Datas", "status": "active"}]

        def list_tasks(self, project_id):
            return [{"id": 40, "title": "Sem data válida", "status": "todo",
                     "priority": "medium", "due_date": "2026-99-99", "dependencies": []}]

    report = ProjectIntelligence(InvalidDateManager(), today=date(2026, 11, 25)).analyze()
    assert report["summary"]["overdue_tasks"] == 0
    assert report["summary"]["due_soon_tasks"] == 0
    assert report["priority_recommendations"][0]["task_id"] == 40


def test_incomplete_dependencies_increase_priority_and_explain_action():
    class DependencyManager:
        def list_projects(self):
            return [{"id": 5, "name": "Dependências", "status": "active"}]

        def list_tasks(self, project_id):
            return [
                {"id": 50, "title": "Pré-requisito", "status": "todo",
                 "priority": "low", "due_date": None, "dependencies": []},
                {"id": 51, "title": "Entrega", "status": "todo",
                 "priority": "low", "due_date": None, "dependencies": [50]},
            ]

    report = ProjectIntelligence(DependencyManager(), today=date(2026, 11, 25)).analyze()
    recommendation = next(
        item for item in report["priority_recommendations"] if item["task_id"] == 51
    )
    assert recommendation["score"] == 30
    assert "1 dependência(s) ainda não concluída(s)" in recommendation["reasons"]
    assert recommendation["suggested_action"] == "Resolver bloqueios e dependências"
