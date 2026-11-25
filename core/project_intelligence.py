"""Deterministic, read-only intelligence for FRIDAY Project Manager."""
from datetime import date, timedelta


class ProjectIntelligence:
    """Builds actionable project insights without mutating project data."""

    def __init__(self, manager, today=None, due_soon_days=3):
        self.manager = manager
        self.today = today or date.today()
        self.due_soon_days = due_soon_days

    def analyze(self):
        projects = self.manager.list_projects()
        active_projects = [p for p in projects if p.get("status") == "active"]
        all_tasks = []
        project_summaries = []
        for project in projects:
            if project.get("status") in ("archived", "completed"):
                continue
            tasks = self.manager.list_tasks(project["id"])
            all_tasks.extend((project, task) for task in tasks)
            project_summaries.append(self._summarize_project(project, tasks))

        task_status = {task["id"]: task.get("status") for _, task in all_tasks}
        insights = []
        for project, task in all_tasks:
            insights.extend(self._task_insights(project, task, task_status))

        insights.sort(key=lambda item: (-item["score"], item.get("due_date") or "9999-12-31"))
        overdue = sum(1 for _, t in all_tasks if self._days_until(t.get("due_date")) is not None
                      and self._days_until(t.get("due_date")) < 0 and t.get("status") not in ("done", "cancelled"))
        due_soon = sum(1 for _, t in all_tasks if self._days_until(t.get("due_date")) is not None
                       and 0 <= self._days_until(t.get("due_date")) <= self.due_soon_days
                       and t.get("status") not in ("done", "cancelled"))
        blocked = sum(1 for _, t in all_tasks if t.get("status") == "blocked")
        open_tasks = sum(1 for _, t in all_tasks if t.get("status") not in ("done", "cancelled"))
        completed_tasks = sum(1 for _, t in all_tasks if t.get("status") == "done")
        return {
            "generated_for": self.today.isoformat(),
            "summary": {
                "active_projects": len(active_projects),
                "open_tasks": open_tasks,
                "completed_tasks": completed_tasks,
                "overdue_tasks": overdue,
                "due_soon_tasks": due_soon,
                "blocked_tasks": blocked,
            },
            "projects": project_summaries,
            "priority_recommendations": insights[:10],
            "risks": self._risks(project_summaries, overdue, blocked),
            "mutations_performed": False,
        }

    def _days_until(self, due_date):
        if not due_date:
            return None
        try:
            return (date.fromisoformat(str(due_date)[:10]) - self.today).days
        except (TypeError, ValueError):
            return None

    def _summarize_project(self, project, tasks):
        total = len(tasks)
        done = sum(1 for t in tasks if t.get("status") == "done")
        open_count = sum(1 for t in tasks if t.get("status") not in ("done", "cancelled"))
        overdue = [t for t in tasks if self._days_until(t.get("due_date")) is not None
                   and self._days_until(t.get("due_date")) < 0
                   and t.get("status") not in ("done", "cancelled")]
        blocked = [t for t in tasks if t.get("status") == "blocked"]
        return {
            "id": project["id"], "name": project.get("name", ""),
            "status": project.get("status", ""),
            "total_tasks": total, "open_tasks": open_count,
            "completed_tasks": done,
            "completion_percent": round(done / total * 100) if total else 0,
            "overdue_tasks": len(overdue), "blocked_tasks": len(blocked),
        }

    def _task_insights(self, project, task, task_status):
        status = task.get("status")
        if status in ("done", "cancelled"):
            return []
        days = self._days_until(task.get("due_date"))
        priority = task.get("priority", "medium")
        score = {"urgent": 40, "high": 30, "medium": 20, "low": 10}.get(priority, 10)
        reasons = []
        if days is not None:
            if days < 0:
                score += min(60, 35 + abs(days))
                reasons.append(f"Atrasada há {abs(days)} dia(s)")
            elif days <= self.due_soon_days:
                score += 25 - days * 5
                reasons.append("Prazo próximo")
        if status == "blocked":
            score += 15
            reasons.append("Está marcada como bloqueada")
        incomplete = [dep for dep in task.get("dependencies", []) if task_status.get(dep) != "done"]
        if incomplete:
            score += 20
            reasons.append(f"{len(incomplete)} dependência(s) ainda não concluída(s)")
        if not reasons:
            reasons.append(f"Prioridade cadastrada: {priority}")
        action = "Resolver bloqueios e dependências" if incomplete or status == "blocked" else (
            "Replanejar prazo e iniciar a execução" if days is not None and days < 0 else
            "Priorizar esta tarefa na próxima sessão de trabalho")
        return [{
            "project_id": project["id"], "project_name": project.get("name", ""),
            "task_id": task["id"], "title": task.get("title", ""),
            "status": status, "priority": priority, "due_date": task.get("due_date"),
            "days_until_due": days, "score": score,
            "reasons": reasons, "suggested_action": action,
        }]

    def _risks(self, projects, overdue, blocked):
        risks = []
        for project in projects:
            if project["overdue_tasks"]:
                risks.append({"project_id": project["id"], "project_name": project["name"],
                              "type": "overdue", "count": project["overdue_tasks"],
                              "message": f'{project["overdue_tasks"]} tarefa(s) atrasada(s)'})
            if project["blocked_tasks"]:
                risks.append({"project_id": project["id"], "project_name": project["name"],
                              "type": "blocked", "count": project["blocked_tasks"],
                              "message": f'{project["blocked_tasks"]} tarefa(s) bloqueada(s)'})
        if not risks and not overdue and not blocked:
            risks.append({"type": "none", "count": 0, "message": "Nenhum risco crítico identificado."})
        return risks
