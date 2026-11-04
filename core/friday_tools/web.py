import os
import json
import urllib.parse
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen

from .utils import _is_safe_url, _HTMLTextExtractor, _truncate_text
from .context import _get_cache, _set_cache

def _handler_get_weather(city="São Paulo"):
    cache_key = f"weather_{city}"
    cached = _get_cache(cache_key)
    if cached: return cached

    city_encoded = urllib.parse.quote(city)
    try:
        req = Request(f"https://wttr.in/{city_encoded}?format=j1&lang=pt", headers={"User-Agent": "FRIDAY/1.0"})
        with urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))

        current = data["current_condition"][0]
        temp = current["temp_C"]
        desc = current["lang_pt"][0]["value"] if "lang_pt" in current and current["lang_pt"] else current["weatherDesc"][0]["value"]
        today = (data.get("weather") or [{}])[0]

        result = f"Em {city}: {desc}, {temp}°C. Máx {today.get('maxtempC', '?')}°C / Mín {today.get('mintempC', '?')}°C."
        _set_cache(cache_key, result, ttl_seconds=1800)
        return result
    except Exception as e:
        return {"error": f"Falha no clima: {str(e)}"}

def _handler_get_news(category="mundo"):
    cache_key = f"news_{category}"
    cached = _get_cache(cache_key)
    if cached: return cached

    try:
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(category)}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as response:
            xml = response.read().decode("utf-8")

        root = ET.fromstring(xml)
        items = root.findall(".//item")[:3]
        if not items: return "Nenhuma notícia encontrada."

        news_list = [f"- {item.find('title').text}" for item in items]
        result = "Últimas notícias:\n" + "\n".join(news_list)
        _set_cache(cache_key, result, ttl_seconds=3600)
        return result
    except Exception as e:
        return {"error": f"Falha ao buscar notícias: {str(e)}"}

def _handler_get_crypto_price(coin="bitcoin"):
    cache_key = f"crypto_{coin}"
    cached = _get_cache(cache_key)
    if cached: return cached

    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin.lower()}&vs_currencies=usd,brl&include_24hr_change=true"
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
    except Exception as e:
        return {"error": f"Falha ao buscar cotação: {str(e)}"}

def _handler_search_web(query, max_results=3):
    cache_key = f"search_{query}_{max_results}"
    cached = _get_cache(cache_key)
    if cached: return cached

    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    if tavily_key:
        try:
            payload = json.dumps({"query": query, "search_depth": "basic", "max_results": max_results, "include_answer": False}).encode("utf-8")
            req = Request("https://api.tavily.com/search", data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {tavily_key}"}, method="POST")
            with urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))

            results = [{"title": item.get("title", ""), "url": item.get("url", ""), "snippet": _truncate_text(item.get("content", ""), 500)} for item in data.get("results", [])[:max_results]]
            if results:
                final = {"query": query, "results": results, "source": "tavily"}
                _set_cache(cache_key, final, ttl_seconds=600)
                return final
        except Exception:
            pass

    try:
        params = urllib.parse.urlencode({"q": query, "format": "json", "no_html": 1, "skip_disambig": 1})
        req = Request(f"https://api.duckduckgo.com/?{params}", headers={"User-Agent": "FRIDAY/1.0"})
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        abstract = data.get("AbstractText") or data.get("Answer")
        if abstract:
            final = {"query": query, "results": [{"title": data.get("Heading", "Resultado"), "url": data.get("AbstractURL", ""), "snippet": _truncate_text(abstract, 800)}], "source": "duckduckgo"}
            _set_cache(cache_key, final, ttl_seconds=600)
            return final

        related = data.get("RelatedTopics", [])
        results = [{"title": "Tópico relacionado", "url": t.get("FirstURL", ""), "snippet": _truncate_text(t["Text"], 300)} for t in related[:max_results] if isinstance(t, dict) and "Text" in t]
        if results:
            final = {"query": query, "results": results, "source": "duckduckgo"}
            _set_cache(cache_key, final, ttl_seconds=600)
            return final

        return {"error": "Nenhum resultado encontrado."}
    except Exception as e:
        return {"error": f"Falha na pesquisa: {str(e)}"}

def _handler_deep_search(query):
    search = _handler_search_web(query, max_results=3)
    if isinstance(search, dict) and "error" in search: return search
    
    results = search.get("results", []) if isinstance(search, dict) else []
    if not results: return {"error": "Nenhum resultado encontrado."}

    top = results[0]
    top_url = top.get("url", "")
    if not top_url: return {"query": query, "results": results, "note": "Sem URL para leitura."}

    page = _handler_read_webpage(top_url)
    content = page if isinstance(page, str) else ""
    return {"query": query, "top_result": {"title": top.get("title", ""), "url": top_url, "content": content}, "other_results": results[1:]}

def _handler_read_webpage(url):
    if not _is_safe_url(url):
        return {"error": "URL bloqueada por política de segurança (endereço interno ou inválido)."}
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urlopen(req, timeout=15) as response:
            parser = _HTMLTextExtractor()
            parser.feed(response.read().decode("utf-8", errors="replace"))
            return f"Conteúdo de {url}:\n\n{_truncate_text(parser.get_text(), 6000)}"
    except Exception as e:
        return {"error": f"Falha ao ler a página: {str(e)}"}
