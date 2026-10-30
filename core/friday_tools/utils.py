import os
import re
import time
import socket
import ipaddress
import urllib.parse
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    HAS_ZONEINFO = True
except ImportError:
    HAS_ZONEINFO = False

try:
    import dateparser
    HAS_DATEPARSER = True
except ImportError:
    HAS_DATEPARSER = False

def _get_tz():
    if HAS_ZONEINFO:
        return ZoneInfo("America/Sao_Paulo")
    return timezone(timedelta(hours=-3))

def _parse_iso(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_get_tz())
        return dt
    except Exception:
        return None

def _parse_natural_date(text):
    if not HAS_DATEPARSER:
        return None
    try:
        now = datetime.now(_get_tz())
        return dateparser.parse(
            text, languages=['pt', 'en'],
            settings={
                'TIMEZONE': 'America/Sao_Paulo',
                'RETURN_AS_TIMEZONE_AWARE': True,
                'PREFER_DATES_FROM': 'future',
                'RELATIVE_BASE': now,
            }
        )
    except Exception:
        return None

def _format_iso_to_br(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(_get_tz())
        return dt.strftime("%d/%m/%Y às %H:%M")
    except Exception:
        return iso_str

def _truncate_text(text, max_len=5000):
    if not text:
        return ""
    text = str(text)
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    last_space = truncated.rfind(' ')
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "..."

def _as_text(value):
    import json
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if value.get("error"):
            return f"(indisponível: {value['error']})"
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)

def _is_safe_url(url):
    """Bloqueia URLs que apontam para endereços internos (SSRF)."""
    try:
        p = urllib.parse.urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        host = p.hostname
        if not host:
            return False
        for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP):
            ip_str = info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:
        return False

class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
        self.skip = False
        self.ignore_tags = {'script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript'}
    def handle_starttag(self, tag, attrs):
        if tag in self.ignore_tags:
            self.skip = True
    def handle_endtag(self, tag):
        if tag in self.ignore_tags:
            self.skip = False
    def handle_data(self, data):
        if not self.skip:
            text = data.strip()
            if text:
                self.result.append(text)
    def get_text(self):
        return " ".join(self.result)
