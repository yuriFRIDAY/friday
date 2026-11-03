import json
import logging

from .utils import _as_text
from .web import _handler_get_weather, _handler_get_news, _handler_search_web, _handler_deep_search, _handler_read_webpage
from .calendar import _handler_get_calendar_events, _handler_create_calendar_event, _handler_delete_calendar_event, _handler_update_calendar_event, _handler_find_free_slots
from .email import _handler_send_email, _handler_reply_to_email, _handler_read_emails, _handler_read_email_full, _handler_get_email_thread
from .tasks import _handler_create_task, _handler_list_tasks, _handler_complete_task
from .system import _handler_get_current_time, _handler_get_system_status, _handler_get_morning_briefing
from .music import _handler_play_music
from .context import _MEMORY_CONTEXT

logger = logging.getLogger("friday.tools")

def _handler_save_memory(key, value):
    save_func = _MEMORY_CONTEXT.get("save")
    if not callable(save_func): return {"error": "Memória indisponível."}
    save_func(key, value)
    return f"Memória salva: {key} = {value}"

def _handler_recall_memory(key):
    recall = _MEMORY_CONTEXT.get("recall")
    if not callable(recall): return {"error": "Memória indisponível."}
    return recall(key) or "Nada encontrado."

def _handler_delete_memory(key):
    del_func = _MEMORY_CONTEXT.get("delete")
    if not callable(del_func): return {"error": "Memória indisponível."}
    return "Removida." if del_func(key) else "Não encontrada."

def _handler_get_crypto_price(coin="bitcoin"):
    from .context import _get_cache, _set_cache
    cache_key = f"crypto_{coin}"
    cached = _get_cache(cache_key)
    if cached: return cached

    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin.lower()}&vs_currencies=usd,brl&include_24hr_change=true"
        from urllib.request import Request, urlopen
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        if coin.lower() not in data: return {"error": f"Moeda '{coin}' não encontrada."}

        info = data[coin.lower()]
        change = info.get('usd_24h_change', 0)
        direction = "📈" if change > 0 else "📉"
        result = f"{coin.capitalize()}: US$ {info['usd']:,.2f} | R$ {info['brl']:,.2f} ({direction} {change:.1f}% 24h)."

        _set_cache(cache_key, result, ttl_seconds=300)
        return result
    except Exception as e: return {"error": f"Falha ao buscar cotação: {str(e)}"}

def _handler_open_eye_of_god(**kwargs):
    return {"action": "open_eye_of_god", "status": "ativando"}

REQUIRES_CONFIRMATION = {"send_email", "reply_to_email", "delete_calendar_event"}

TOOL_REGISTRY = {
    "get_current_time": {"description": "Retorna data e hora.", "handler": _handler_get_current_time, "parameters": {"type": "object", "properties": {}}},
    "get_system_status": {"description": "Status da CPU/RAM/Disco.", "handler": _handler_get_system_status, "parameters": {"type": "object", "properties": {}}},
    "get_weather": {"description": "Consulta o clima.", "handler": _handler_get_weather, "parameters": {"type": "object", "properties": {"city": {"type": "string", "description": "Nome da cidade"}}, "required": ["city"]}},
    "get_crypto_price": {"description": "Cotações de cripto (bitcoin, ethereum).", "handler": _handler_get_crypto_price, "parameters": {"type": "object", "properties": {"coin": {"type": "string", "description": "Nome da criptomoeda"}}, "required": ["coin"]}},
    "get_news": {"description": "Lê as últimas notícias (mundo, tecnologia, etc).", "handler": _handler_get_news, "parameters": {"type": "object", "properties": {"category": {"type": "string", "description": "Categoria da notícia"}}, "required": ["category"]}},
    "search_web": {"description": "Pesquisa na web.", "handler": _handler_search_web, "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Termo de pesquisa"}}, "required": ["query"]}},
    "deep_search": {"description": "Pesquisa e lê o conteúdo completo do primeiro resultado.", "handler": _handler_deep_search, "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Termo de pesquisa"}}, "required": ["query"]}},
    "read_webpage": {"description": "Lê uma URL.", "handler": _handler_read_webpage, "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "URL da página"}}, "required": ["url"]}},
    "send_email": {"description": "Envia e-mail.", "handler": _handler_send_email, "parameters": {"type": "object", "properties": {"to_email": {"type": "string", "description": "E-mail do destinatário"}, "subject": {"type": "string", "description": "Assunto do e-mail"}, "body": {"type": "string", "description": "Corpo do e-mail"}}, "required": ["to_email", "subject", "body"]}},
    "reply_to_email": {"description": "Responde e-mail.", "handler": _handler_reply_to_email, "parameters": {"type": "object", "properties": {"to_email": {"type": "string", "description": "E-mail do destinatário"}, "subject": {"type": "string", "description": "Assunto do e-mail"}, "body": {"type": "string", "description": "Corpo da resposta"}}, "required": ["to_email", "subject", "body"]}},
    "read_emails": {"description": "Lê e-mails recentes.", "handler": _handler_read_emails, "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Filtro de busca"}, "max_results": {"type": "integer", "description": "Número máximo de e-mails"}}}},
    "read_email_full": {"description": "Lê o corpo e anexos de um e-mail.", "handler": _handler_read_email_full, "parameters": {"type": "object", "properties": {"message_id": {"type": "string", "description": "ID da mensagem"}}, "required": ["message_id"]}},
    "get_email_thread": {"description": "Lê a conversa de e-mail.", "handler": _handler_get_email_thread, "parameters": {"type": "object", "properties": {"thread_id": {"type": "string", "description": "ID da thread"}}, "required": ["thread_id"]}},
    "create_task": {"description": "Cria uma tarefa (to-do) na lista de tarefas.", "handler": _handler_create_task, "parameters": {"type": "object", "properties": {"description": {"type": "string", "description": "Descrição da tarefa"}}, "required": ["description"]}},
    "list_tasks": {"description": "Lista tarefas pendentes.", "handler": _handler_list_tasks, "parameters": {"type": "object", "properties": {}}},
    "complete_task": {"description": "Conclui uma tarefa.", "handler": _handler_complete_task, "parameters": {"type": "object", "properties": {"task_id": {"type": "integer", "description": "ID da tarefa"}}, "required": ["task_id"]}},
    "save_memory": {"description": "Salva memória.", "handler": _handler_save_memory, "parameters": {"type": "object", "properties": {"key": {"type": "string", "description": "Chave da memória"}, "value": {"type": "string", "description": "Valor da memória"}}, "required": ["key", "value"]}},
    "recall_memory": {"description": "Recupera memória.", "handler": _handler_recall_memory, "parameters": {"type": "object", "properties": {"key": {"type": "string", "description": "Chave da memória"}}, "required": ["key"]}},
    "delete_memory": {"description": "Apaga memória.", "handler": _handler_delete_memory, "parameters": {"type": "object", "properties": {"key": {"type": "string", "description": "Chave da memória"}}, "required": ["key"]}},
    "play_music": {"description": "Toca ou controla música (pause, play, nome da música).", "handler": _handler_play_music, "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Nome da música ou comando"}}, "required": ["query"]}},
    "get_morning_briefing": {"description": "Resumo matinal.", "handler": _handler_get_morning_briefing, "parameters": {"type": "object", "properties": {"city": {"type": "string", "description": "Nome da cidade"}}, "required": ["city"]}},
    "get_calendar_events": {"description": "Consulta agenda.", "handler": _handler_get_calendar_events, "parameters": {"type": "object", "properties": {"range": {"type": "string", "enum": ["today", "upcoming"]}}, "required": ["range"]}},
    "create_calendar_event": {"description": "Cria um evento na agenda do Google Calendar.", "handler": _handler_create_calendar_event, "parameters": {"type": "object", "properties": {"summary": {"type": "string", "description": "Título do evento"}, "start_natural": {"type": "string", "description": "Data/hora de início"}, "end_natural": {"type": "string", "description": "Data/hora de término"}}, "required": ["summary", "start_natural"]}},
    "delete_calendar_event": {"description": "Exclui evento do calendário.", "handler": _handler_delete_calendar_event, "parameters": {"type": "object", "properties": {"event_id": {"type": "string", "description": "ID do evento"}}, "required": ["event_id"]}},
    "update_calendar_event": {"description": "Atualiza evento do calendário.", "handler": _handler_update_calendar_event, "parameters": {"type": "object", "properties": {"event_id": {"type": "string", "description": "ID do evento"}, "summary": {"type": "string", "description": "Novo título"}, "start_natural": {"type": "string", "description": "Nova data de início"}, "end_natural": {"type": "string", "description": "Nova data de término"}}, "required": ["event_id"]}},
    "find_free_slots": {"description": "Encontra horários livres na agenda.", "handler": _handler_find_free_slots, "parameters": {"type": "object", "properties": {"date_iso": {"type": "string", "description": "Data (YYYY-MM-DD)"}}, "required": ["date_iso"]}},
    "open_eye_of_god": {
        "description": "Ativa a interface global de monitoramento 'Olho de Deus' no painel do usuário.",
        "handler": _handler_open_eye_of_god,
        "parameters": {"type": "object", "properties": {}}
    }
}

def public_tool_specs():
    return {name: {k: v for k, v in spec.items() if k != "handler"} for name, spec in TOOL_REGISTRY.items()}

def execute_tool(tool_name, arguments, user_role="owner", session=None, confirmed=False):
    tool = TOOL_REGISTRY.get(tool_name)
    if not tool:
        return {"ok": False, "error": "Ferramenta não encontrada."}

    if tool_name in REQUIRES_CONFIRMATION and not confirmed:
        return {"ok": False, "error": "Confirmação necessária para executar esta ação."}

    try:
        result = tool["handler"](**arguments)
        if isinstance(result, dict) and result.get("error"):
            return {"ok": False, "error": result["error"]}
        return {"ok": True, "result": result}
    except Exception as e:
        logger.exception(f"Falha ao executar ferramenta {tool_name}")
        return {"ok": False, "error": f"Falha: {e}"}
