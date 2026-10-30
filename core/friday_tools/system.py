import os
import shutil
from datetime import datetime

from .utils import _get_tz, _as_text
from .calendar import _handler_get_calendar_events
from .web import _handler_get_weather, _handler_get_news

def _handler_get_current_time():
    now = datetime.now(_get_tz())
    dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
    return now.strftime(f"Hoje é {dias[now.weekday()]}, %d/%m/%Y. Agora são %H:%M (Horário de Brasília).")

def _handler_get_system_status():
    try:
        try:
            load1, load5, load15 = os.getloadavg()
            cpu_info = f"CPU (carga 1m/5m/15m): {load1:.2f} / {load5:.2f} / {load15:.2f}"
        except OSError: cpu_info = "CPU: indisponível"
        
        try:
            with open('/proc/meminfo', 'r') as f: lines = f.readlines()
            mem_total = int(lines[0].split()[1])
            mem_avail = int(lines[2].split()[1]) if len(lines) > 2 else int(lines[1].split()[1])
            mem_percent = round((mem_total - mem_avail) / mem_total * 100, 1)
            mem_total_gb = round(mem_total / (1024 ** 2), 2)
            mem_info = f"RAM: {mem_percent}% em uso (Total: {mem_total_gb} GB)"
        except Exception: mem_info = "RAM: indisponível"
        
        try:
            total, used, free = shutil.disk_usage("/")
            disk_percent = round((used / total) * 100, 1)
            disk_total_gb = round(total / (1024 ** 3), 2)
            disk_info = f"Disco: {disk_percent}% em uso (Total: {disk_total_gb} GB)"
        except Exception: disk_info = "Disco: indisponível"
        
        return f"Status do Servidor:\n- {cpu_info}\n- {mem_info}\n- {disk_info}"
    except Exception as e:
        return {"error": f"Falha ao ler status: {str(e)}"}

def _handler_get_morning_briefing(city="Criciúma"):
    briefing = [_as_text(_handler_get_weather(city))]
    agenda = _handler_get_calendar_events("today")
    if isinstance(agenda, dict) and agenda.get("events"):
        briefing.append(f"Agenda: {len(agenda['events'])} compromissos. Primeiro: '{agenda['events'][0]['summary']}'.")
    else:
        briefing.append("Agenda livre.")
    briefing.append(_as_text(_handler_get_news()))
    return " ".join(briefing)
