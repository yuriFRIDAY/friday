import time
import threading

from core.database import DATABASE_URL, DB_PATH, DATA_DIR, get_db

HAS_PSYCOPG2 = bool(DATABASE_URL)

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
    return get_db()

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
