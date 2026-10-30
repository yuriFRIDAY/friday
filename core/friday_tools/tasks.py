from .context import _get_db, DATABASE_URL, HAS_PSYCOPG2

def _handler_create_task(description):
    conn = _get_db()
    cursor = conn.cursor()
    try:
        if DATABASE_URL and HAS_PSYCOPG2:
            cursor.execute("INSERT INTO tasks (description, status) VALUES (%s, 'pending')", (description,))
        else:
            cursor.execute("INSERT INTO tasks (description, status) VALUES (?, 'pending')", (description,))
        conn.commit()
        return f"Tarefa criada: {description}"
    except Exception as e: return {"error": str(e)}
    finally:
        cursor.close(); conn.close()

def _handler_list_tasks():
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, description FROM tasks WHERE status = 'pending'")
        rows = cursor.fetchall()
        if not rows: return "Nenhuma tarefa pendente."
        return "Tarefas pendentes:\n" + "\n".join(f"[{r[0]}] {r[1]}" for r in rows)
    except Exception as e: return {"error": str(e)}
    finally:
        cursor.close(); conn.close()

def _handler_complete_task(task_id):
    conn = _get_db()
    cursor = conn.cursor()
    try:
        if DATABASE_URL and HAS_PSYCOPG2:
            cursor.execute("UPDATE tasks SET status = 'done' WHERE id = %s", (task_id,))
        else:
            cursor.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))
        conn.commit()
        return f"Tarefa {task_id} concluída."
    except Exception as e: return {"error": str(e)}
    finally:
        cursor.close(); conn.close()
