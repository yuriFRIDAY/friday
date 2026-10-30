from datetime import timedelta

from .context import _CALENDAR_CONTEXT
from .utils import _parse_natural_date, _parse_iso, _format_iso_to_br, _get_tz

def _handler_get_calendar_events(range_value="upcoming"):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    if not callable(ctx): return {"error": "Calendar indisponível."}

    access_token = ctx()
    if not access_token: return {"error": "Calendar indisponível."}

    query = _CALENDAR_CONTEXT.get("events_query")
    today = _CALENDAR_CONTEXT.get("today_events")

    if range_value == "today" and callable(today):
        events = today(access_token)
    elif callable(query):
        events = query(access_token, max_results=10)
    else:
        events = None

    if not events: return {"events": []}
    return {"range": range_value, "events": [{"id": e.get("id"), "summary": e.get("summary"), "start": _format_iso_to_br(e.get("start")), "hangoutLink": e.get("hangoutLink")} for e in events]}

def _handler_create_calendar_event(summary, start_natural, end_natural=None):
    start_dt = _parse_natural_date(start_natural)
    if not start_dt: return {"error": "Data de início inválida."}

    end_dt = _parse_natural_date(end_natural) if end_natural else start_dt + timedelta(hours=1)

    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    create = _CALENDAR_CONTEXT.get("create_event")

    if not callable(ctx) or not callable(create): return {"error": "Calendar indisponível."}

    access_token = ctx()
    if not access_token: return {"error": "Calendar indisponível (Token inválido ou expirado)."}

    payload = {"summary": summary, "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Sao_Paulo"}, "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Sao_Paulo"}}
    res = create(access_token, payload)

    if res: return {"ok": True, "summary": summary, "start": start_dt.isoformat()}
    return {"error": "O Google recusou a criação do evento."}

def _handler_delete_calendar_event(event_id):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    delete = _CALENDAR_CONTEXT.get("delete_event")

    if not callable(ctx) or not callable(delete): return {"error": "Calendar indisponível."}

    access_token = ctx()
    if not access_token: return {"error": "Calendar indisponível."}

    if delete(access_token, event_id): return {"ok": True}
    return {"error": "Falha ao deletar."}

def _handler_update_calendar_event(event_id, summary=None, start_natural=None, end_natural=None, description=None):
    start_dt = _parse_natural_date(start_natural) if start_natural else None
    end_dt = _parse_natural_date(end_natural) if end_natural else None

    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    update = _CALENDAR_CONTEXT.get("update_event")

    if not callable(ctx) or not callable(update): return {"error": "Calendar indisponível."}

    access_token = ctx()
    if not access_token: return {"error": "Calendar indisponível."}

    payload = {}
    if summary: payload["summary"] = summary
    if start_dt: payload["start"] = {"dateTime": start_dt.isoformat(), "timeZone": "America/Sao_Paulo"}
    if end_dt: payload["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "America/Sao_Paulo"}
    if description: payload["description"] = description

    if not payload: return {"error": "Nenhum campo para atualizar."}

    res = update(access_token, event_id, payload)
    if res: return {"ok": True, "updated": True}
    return {"error": "Falha ao atualizar."}

def _handler_find_free_slots(date_iso):
    ctx = _CALENDAR_CONTEXT.get("access_token_provider")
    query = _CALENDAR_CONTEXT.get("events_query")

    if not callable(ctx) or not callable(query): return {"error": "Calendar indisponível."}

    access_token = ctx()
    if not access_token: return {"error": "Calendar indisponível."}

    target = _parse_iso(date_iso) or _parse_natural_date(date_iso)
    if not target: return {"error": f"Data inválida: {date_iso}"}

    target = target.astimezone(_get_tz())
    start_of_day = target.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)

    events = query(access_token, time_min=start_of_day.isoformat(), time_max=end_of_day.isoformat(), max_results=50)
    if events is None: return {"error": "Falha ao consultar agenda."}

    work_start = start_of_day.replace(hour=8)
    work_end = start_of_day.replace(hour=20)

    busy = []
    for evt in events:
        try: busy.append((_parse_iso(evt["start"]).astimezone(_get_tz()), _parse_iso(evt["end"]).astimezone(_get_tz())))
        except Exception: continue

    busy.sort(key=lambda x: x[0])
    duration_minutes = 60
    limit = 3
    slots = []
    current = work_start

    for s, e in busy:
        if s > current and (s - current).total_seconds() >= duration_minutes * 60:
            slots.append(current)
            if len(slots) >= limit: break
        if e > current: current = e

    if len(slots) < limit and current < work_end and (work_end - current).total_seconds() >= duration_minutes * 60:
        slots.append(current)

    return {"date": start_of_day.strftime("%Y-%m-%d"), "free_slots": [s.strftime("%H:%M") for s in slots[:limit]]}
