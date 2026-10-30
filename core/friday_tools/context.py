import os
import time
import threading
import sqlite3

try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

DATA_DIR = os.getenv("DATA_DIR", ".")
if os.path.exists("/data") and os.access("/data", os.W_OK):
    DATA_DIR = "/data"
DB_PATH = os.path.join(DATA_DIR, "friday.db")

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_CALENDAR_CONTEXT = {}
_MEMORY_CONTEXT = {}

def set_calendar_context(access_token_provider, events_query, today_events, create_event, delete_event, update_event):
    _CALENDAR_CONTEXT.update({
        "access_token_provider": access_token_provider,
        "events_query": events_query,
        "today_events": today_events,
        "create_event": create_event,
        "delete_event": delete_event,
        "update_event": update_event,
    })

def set_memory_context(save_func, recall_func, delete_func=None):
    _MEMORY_CONTEXT.update({"save": save_func, "recall": recall_func, "delete": delete_func})

def _get_db():
    if DATABASE_URL and HAS_PSYCOPG2:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect(DB_PATH)

_CACHE = {}
_CACHE_LOCK = threading.Lock()

def _get_cache(key):
    with _CACHE_LOCK:
        if key in _CACHE:
            data, expires = _CACHE[key]
            if time.time() < expires:
                return data
            del _CACHE[key]
    return None

def _set_cache(key, data, ttl_seconds=300):
    with _CACHE_LOCK:
        _CACHE[key] = (data, time.time() + ttl_seconds)
