"""Shared database configuration and connection factory for FRIDAY.

SQLite path selection is deliberately conservative: if both the legacy working-
directory database and the persistent DATA_DIR database exist, startup fails
with an actionable message instead of silently opening an empty database.
"""
import os
import sqlite3
from pathlib import Path

try:
    import psycopg2
except ImportError:  # SQLite-only development/test environments
    psycopg2 = None

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

def _resolve_data_dir():
    configured = os.getenv("DATA_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return str(path)
    persistent = Path("/data")
    if persistent.is_dir() and os.access(str(persistent), os.W_OK):
        return str(persistent)
    return str(Path(__file__).resolve().parents[1])

DATA_DIR = _resolve_data_dir()

def _resolve_sqlite_path():
    explicit = os.getenv("FRIDAY_DB_PATH", "").strip()
    if explicit:
        path = Path(explicit).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)

    candidates = []
    # The old app.py opened ./friday.db while task tools opened
    # DATA_DIR/friday.db. Detect both even when DATA_DIR is configured.
    for candidate in (Path.cwd() / "friday.db", Path(DATA_DIR) / "friday.db"):
        resolved = candidate.resolve()
        if resolved.exists() and resolved not in candidates:
            candidates.append(resolved)
    if len(candidates) > 1:
        raise RuntimeError(
            "Foram encontrados dois bancos SQLite (friday.db). "
            "Defina FRIDAY_DB_PATH explicitamente após identificar qual contém "
            "os dados atuais; nenhuma migração automática foi executada."
        )
    if candidates:
        return str(candidates[0])

    path = Path(DATA_DIR) / "friday.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)

DB_PATH = None if DATABASE_URL else _resolve_sqlite_path()

def get_db():
    """Return a new SQLite or PostgreSQL connection for all application modules."""
    if DATABASE_URL:
        if psycopg2 is None:
            raise RuntimeError("DATABASE_URL configurada, mas psycopg2 não está instalado.")
        return psycopg2.connect(DATABASE_URL)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
