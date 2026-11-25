"""Project Manager domain service backed by FRIDAY's shared database."""
from datetime import date, datetime, timezone

from core.database import DATABASE_URL, get_db

VALID_PROJECT_STATUSES = {"active", "paused", "completed", "archived"}
VALID_TASK_STATUSES = {"todo", "in_progress", "blocked", "done", "cancelled"}
VALID_PRIORITIES = {"low", "medium", "high", "urgent"}


class ProjectManagerError(ValueError):
    pass


def _placeholder():
    return "%s" if DATABASE_URL else "?"


def _row_dict(cursor, row):
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    names = [column[0] for column in cursor.description]
    return dict(zip(names, row))


def _validate_text(value, field, max_length=500, required=True):
    if not isinstance(value, str):
        raise ProjectManagerError(f"{field} deve ser texto.")
    value = value.strip()
    if required and not value:
        raise ProjectManagerError(f"{field} é obrigatório.")
    if len(value) > max_length:
        raise ProjectManagerError(f"{field} excede {max_length} caracteres.")
    return value


def _validate_date(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ProjectManagerError("due_date deve estar no formato ISO (AAAA-MM-DD).")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ProjectManagerError("due_date inválida. Use AAAA-MM-DD.")


class ProjectManager:
    """CRUD de projetos/tarefas e dependências sem ciclos."""

    def __init__(self):
        self.ensure_schema()

    def ensure_schema(self):
        conn = get_db()
        cur = conn.cursor()
        try:
            if DATABASE_URL:
                statements = [
                    """CREATE TABLE IF NOT EXISTS pm_projects (
                        id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS pm_tasks (
                        id BIGSERIAL PRIMARY KEY, project_id BIGINT NOT NULL REFERENCES pm_projects(id) ON DELETE CASCADE,
                        title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'todo', priority TEXT NOT NULL DEFAULT 'medium',
                        due_date TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS pm_task_dependencies (
                        task_id BIGINT NOT NULL REFERENCES pm_tasks(id) ON DELETE CASCADE,
                        depends_on_task_id BIGINT NOT NULL REFERENCES pm_tasks(id) ON DELETE CASCADE,
                        PRIMARY KEY(task_id, depends_on_task_id),
                        CHECK(task_id <> depends_on_task_id))""",
                    "CREATE INDEX IF NOT EXISTS idx_pm_tasks_project ON pm_tasks(project_id)",
                    "CREATE INDEX IF NOT EXISTS idx_pm_tasks_due_date ON pm_tasks(due_date)",
                ]
            else:
                cur.execute("PRAGMA foreign_keys = ON")
                statements = [
                    """CREATE TABLE IF NOT EXISTS pm_projects (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS pm_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES pm_projects(id) ON DELETE CASCADE,
                        title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'todo', priority TEXT NOT NULL DEFAULT 'medium',
                        due_date TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS pm_task_dependencies (
                        task_id INTEGER NOT NULL REFERENCES pm_tasks(id) ON DELETE CASCADE,
                        depends_on_task_id INTEGER NOT NULL REFERENCES pm_tasks(id) ON DELETE CASCADE,
                        PRIMARY KEY(task_id, depends_on_task_id),
                        CHECK(task_id <> depends_on_task_id))""",
                    "CREATE INDEX IF NOT EXISTS idx_pm_tasks_project ON pm_tasks(project_id)",
                    "CREATE INDEX IF NOT EXISTS idx_pm_tasks_due_date ON pm_tasks(due_date)",
                ]
            for statement in statements:
                cur.execute(statement)
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def _fetch_one(self, cursor):
        return _row_dict(cursor, cursor.fetchone())

    def _fetch_all(self, cursor):
        return [_row_dict(cursor, row) for row in cursor.fetchall()]

    def create_project(self, name, description=""):
        name = _validate_text(name, "name", 160)
        description = _validate_text(description, "description", 4000, False)
        now = datetime.now(timezone.utc).isoformat()
        conn, cur = get_db(), None
        try:
            cur = conn.cursor()
            if DATABASE_URL:
                cur.execute(
                    "INSERT INTO pm_projects(name,description,status,created_at,updated_at) VALUES (%s,%s,'active',%s,%s) RETURNING id",
                    (name, description, now, now))
                project_id = cur.fetchone()[0]
            else:
                cur.execute(
                    "INSERT INTO pm_projects(name,description,status,created_at,updated_at) VALUES (?,?, 'active',?,?)",
                    (name, description, now, now))
                project_id = cur.lastrowid
            conn.commit()
            return self.get_project(project_id)
        finally:
            if cur: cur.close()
            conn.close()

    def list_projects(self, include_archived=False):
        conn, cur = get_db(), None
        try:
            cur = conn.cursor()
            if include_archived:
                cur.execute("SELECT * FROM pm_projects ORDER BY updated_at DESC")
            else:
                cur.execute("SELECT * FROM pm_projects WHERE status <> 'archived' ORDER BY updated_at DESC")
            return self._fetch_all(cur)
        finally:
            if cur: cur.close()
            conn.close()

    def get_project(self, project_id):
        conn, cur = get_db(), None
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM pm_projects WHERE id = {_placeholder()}", (project_id,))
            return self._fetch_one(cur)
        finally:
            if cur: cur.close()
            conn.close()

    def update_project(self, project_id, **changes):
        allowed = {"name", "description", "status"}
        if not changes or set(changes) - allowed:
            raise ProjectManagerError("Campos de projeto inválidos ou ausentes.")
        if "name" in changes:
            changes["name"] = _validate_text(changes["name"], "name", 160)
        if "description" in changes:
            changes["description"] = _validate_text(changes["description"], "description", 4000, False)
        if "status" in changes and changes["status"] not in VALID_PROJECT_STATUSES:
            raise ProjectManagerError("Status de projeto inválido.")
        changes["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._update("pm_projects", "id", project_id, changes)
        result = self.get_project(project_id)
        if result is None:
            raise ProjectManagerError("Projeto não encontrado.")
        return result

    def delete_project(self, project_id):
        return self._delete("pm_projects", "id", project_id)

    def create_task(self, project_id, title, description="", priority="medium", due_date=None):
        title = _validate_text(title, "title", 200)
        description = _validate_text(description, "description", 4000, False)
        if priority not in VALID_PRIORITIES:
            raise ProjectManagerError("Prioridade inválida.")
        due_date = _validate_date(due_date)
        if not self.get_project(project_id):
            raise ProjectManagerError("Projeto não encontrado.")
        now = datetime.now(timezone.utc).isoformat()
        conn, cur = get_db(), None
        try:
            cur = conn.cursor()
            if DATABASE_URL:
                cur.execute(
                    """INSERT INTO pm_tasks(project_id,title,description,status,priority,due_date,created_at,updated_at)
                    VALUES (%s,%s,%s,'todo',%s,%s,%s,%s) RETURNING id""",
                    (project_id,title,description,priority,due_date,now,now))
                task_id = cur.fetchone()[0]
            else:
                cur.execute(
                    """INSERT INTO pm_tasks(project_id,title,description,status,priority,due_date,created_at,updated_at)
                    VALUES (?,?,?,'todo',?,?,?,?)""",
                    (project_id,title,description,priority,due_date,now,now))
                task_id = cur.lastrowid
            conn.commit()
            return self.get_task(task_id)
        finally:
            if cur: cur.close()
            conn.close()

    def get_task(self, task_id):
        conn, cur = get_db(), None
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM pm_tasks WHERE id = {_placeholder()}", (task_id,))
            task = self._fetch_one(cur)
            if task:
                cur.execute(f"""SELECT depends_on_task_id FROM pm_task_dependencies
                    WHERE task_id = {_placeholder()} ORDER BY depends_on_task_id""", (task_id,))
                task["dependencies"] = [r[0] for r in cur.fetchall()]
            return task
        finally:
            if cur: cur.close()
            conn.close()

    def list_tasks(self, project_id, status=None):
        if not self.get_project(project_id):
            raise ProjectManagerError("Projeto não encontrado.")
        conn, cur = get_db(), None
        try:
            cur = conn.cursor()
            if status:
                if status not in VALID_TASK_STATUSES:
                    raise ProjectManagerError("Status de tarefa inválido.")
                cur.execute(f"SELECT * FROM pm_tasks WHERE project_id = {_placeholder()} AND status = {_placeholder()} ORDER BY due_date, id", (project_id,status))
            else:
                cur.execute(f"SELECT * FROM pm_tasks WHERE project_id = {_placeholder()} ORDER BY due_date, id", (project_id,))
            tasks = self._fetch_all(cur)
            for task in tasks:
                cur.execute(f"SELECT depends_on_task_id FROM pm_task_dependencies WHERE task_id = {_placeholder()} ORDER BY depends_on_task_id", (task["id"],))
                task["dependencies"] = [r[0] for r in cur.fetchall()]
            return tasks
        finally:
            if cur: cur.close()
            conn.close()

    def update_task(self, task_id, **changes):
        allowed = {"title", "description", "status", "priority", "due_date"}
        if not changes or set(changes) - allowed:
            raise ProjectManagerError("Campos de tarefa inválidos ou ausentes.")
        if "title" in changes:
            changes["title"] = _validate_text(changes["title"], "title", 200)
        if "description" in changes:
            changes["description"] = _validate_text(changes["description"], "description", 4000, False)
        if "status" in changes and changes["status"] not in VALID_TASK_STATUSES:
            raise ProjectManagerError("Status de tarefa inválido.")
        if "priority" in changes and changes["priority"] not in VALID_PRIORITIES:
            raise ProjectManagerError("Prioridade inválida.")
        if "due_date" in changes:
            changes["due_date"] = _validate_date(changes["due_date"])
        if changes.get("status") == "done" and not self._dependencies_complete(task_id):
            raise ProjectManagerError("Conclua as dependências antes de concluir esta tarefa.")
        changes["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._update("pm_tasks", "id", task_id, changes)
        result = self.get_task(task_id)
        if result is None:
            raise ProjectManagerError("Tarefa não encontrada.")
        return result

    def add_dependency(self, task_id, depends_on_task_id):
        task = self.get_task(task_id)
        dependency = self.get_task(depends_on_task_id)
        if not task or not dependency:
            raise ProjectManagerError("Tarefa não encontrada.")
        if task["project_id"] != dependency["project_id"]:
            raise ProjectManagerError("Dependências devem pertencer ao mesmo projeto.")
        if task_id == depends_on_task_id or self._would_cycle(task_id, depends_on_task_id):
            raise ProjectManagerError("Dependência criaria um ciclo.")
        conn, cur = get_db(), None
        try:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO pm_task_dependencies(task_id,depends_on_task_id) VALUES ({_placeholder()},{_placeholder()})",
                (task_id, depends_on_task_id))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise ProjectManagerError("Dependência já existe.")
            raise
        finally:
            if cur: cur.close()
            conn.close()
        return self.get_task(task_id)

    def remove_dependency(self, task_id, depends_on_task_id):
        conn, cur = get_db(), None
        try:
            cur = conn.cursor()
            cur.execute(
                f"DELETE FROM pm_task_dependencies WHERE task_id = {_placeholder()} AND depends_on_task_id = {_placeholder()}",
                (task_id, depends_on_task_id))
            conn.commit()
            return cur.rowcount > 0
        finally:
            if cur: cur.close()
            conn.close()

    def _would_cycle(self, task_id, dependency_id):
        # If dependency already transitively depends on task, adding the edge closes a cycle.
        visited, stack = set(), [dependency_id]
        conn, cur = get_db(), None
        try:
            cur = conn.cursor()
            while stack:
                current = stack.pop()
                if current == task_id:
                    return True
                if current in visited:
                    continue
                visited.add(current)
                cur.execute(
                    f"SELECT depends_on_task_id FROM pm_task_dependencies WHERE task_id = {_placeholder()}",
                    (current,))
                stack.extend(row[0] for row in cur.fetchall())
            return False
        finally:
            if cur: cur.close()
            conn.close()

    def _dependencies_complete(self, task_id):
        conn, cur = get_db(), None
        try:
            cur = conn.cursor()
            cur.execute(f"""SELECT t.status FROM pm_task_dependencies d
                JOIN pm_tasks t ON t.id = d.depends_on_task_id
                WHERE d.task_id = {_placeholder()}""", (task_id,))
            return all(row[0] == "done" for row in cur.fetchall())
        finally:
            if cur: cur.close()
            conn.close()

    def _update(self, table, key, value, changes):
        conn, cur = get_db(), None
        try:
            cur = conn.cursor()
            assignments = ", ".join(f"{column} = {_placeholder()}" for column in changes)
            cur.execute(f"UPDATE {table} SET {assignments} WHERE {key} = {_placeholder()}",
                        tuple(changes.values()) + (value,))
            conn.commit()
            if cur.rowcount == 0:
                raise ProjectManagerError("Registro não encontrado.")
        finally:
            if cur: cur.close()
            conn.close()

    def _delete(self, table, key, value):
        conn, cur = get_db(), None
        try:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM {table} WHERE {key} = {_placeholder()}", (value,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            if cur: cur.close()
            conn.close()
