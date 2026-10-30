import re
import urllib.parse
from urllib.request import Request, urlopen

def _handler_play_music(query):
    if query.lower() in ["pause", "pausar", "para"]: return {"action": "pause"}
    if query.lower() in ["play", "tocar", "continuar"]: return {"action": "play"}

    try:
        req = Request(f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}", headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as response:
            m = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', response.read().decode("utf-8"))
        if m: return {"action": "play_new", "url": f"https://www.youtube.com/embed/{m.group(1)}?autoplay=1&enablejsapi=1"}
        return {"error": "Vídeo não encontrado."}
    except Exception as e:
        return {"error": str(e)}
