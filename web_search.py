#!/usr/bin/env python3
"""Provider-neutral, bounded web search for Cara's task broker.

Search results are discovery data only.  Every returned URL remains
``external_untrusted`` and may be opened only by the existing SSRF-hardened
``source.fetch`` tool.
"""
import html
import http.client
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import (
    HTTPRedirectHandler, ProxyHandler, Request, build_opener,
)


BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
SENSITIVE_QUERY_KEYS = frozenset({
    "access_token", "api_key", "apikey", "auth", "authorization", "key",
    "password", "signature", "sig", "token",
})
TRANSPORT_ERRORS = (
    TimeoutError, http.client.HTTPException, OSError, UnicodeError, ValueError,
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


# Fixed provider endpoint, no ambient proxy and no redirect that could carry
# X-Subscription-Token to another origin.
_OPENER = build_opener(ProxyHandler({}), _NoRedirect())


class WebSearchError(RuntimeError):
    def __init__(self, message, *, transient=False):
        super().__init__(message)
        self.transient = bool(transient)


def search(cfg, query, *, count=5, search_lang=None, freshness=None, timeout=None):
    """Dispatch one bounded provider request and return normalized results."""
    provider = str(getattr(cfg, "web_search_provider", "") or "").strip().lower()
    if provider == "brave":
        return _search_brave(
            api_key=getattr(cfg, "web_search_api_key", ""),
            query=query,
            count=count,
            search_lang=search_lang,
            freshness=freshness,
            timeout=timeout or getattr(cfg, "web_search_timeout", 12),
            max_bytes=getattr(cfg, "web_search_max_bytes", 512 * 1024),
        )
    if not provider or provider in {"disabled", "off", "none"}:
        raise WebSearchError("Web search is not configured")
    raise WebSearchError(f"Unsupported web search provider: {provider}")


def _search_brave(*, api_key, query, count, search_lang, freshness, timeout,
                  max_bytes):
    key = str(api_key or "").strip()
    if not key:
        raise WebSearchError("Brave Search API key is not configured")
    params = {
        "q": str(query),
        "count": max(3, min(int(count), 8)),
        "safesearch": "moderate",
        "spellcheck": "1",
    }
    if search_lang:
        params["search_lang"] = str(search_lang)
    if freshness:
        params["freshness"] = str(freshness)
    request = Request(
        BRAVE_ENDPOINT + "?" + urlencode(params),
        headers={
            "Accept": "application/json",
            "User-Agent": "Cara-Research/1.0",
            "X-Subscription-Token": key,
        },
        method="GET",
    )
    try:
        with _OPENER.open(
                request, timeout=max(1, min(int(timeout), 25))) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > int(max_bytes):
                raise WebSearchError("Search response exceeds the configured size cap")
            raw = response.read(int(max_bytes) + 1)
    except HTTPError as exc:
        transient = exc.code == 429 or exc.code >= 500
        raise WebSearchError(
            f"Brave Search returned HTTP {exc.code}", transient=transient) from exc
    except URLError as exc:
        raise WebSearchError(
            f"Brave Search transport failed: {exc.reason}", transient=True) from exc
    except TRANSPORT_ERRORS as exc:
        raise WebSearchError(
            f"Brave Search transport failed: {exc!r}", transient=True) from exc
    if len(raw) > int(max_bytes):
        raise WebSearchError("Search response exceeds the configured size cap")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise WebSearchError("Brave Search returned invalid JSON", transient=True) from exc
    rows = ((payload.get("web") or {}).get("results")
            if isinstance(payload, dict) else None)
    if not isinstance(rows, list):
        raise WebSearchError("Brave Search response has no web results")
    results, seen = [], set()
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        url = _safe_result_url(raw_row.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        results.append({
            "rank": len(results) + 1,
            "title": _plain_text(raw_row.get("title"), 300),
            "url": url,
            "snippet": _plain_text(raw_row.get("description"), 1200),
        })
        if len(results) >= params["count"]:
            break
    if len(results) < 3:
        raise WebSearchError(
            "Search returned fewer than three usable sources; no multi-source "
            "brief can be produced")
    return results


def _plain_text(value, maximum):
    text = re.sub(r"<[^>]*>", " ", html.unescape(str(value or "")))
    return " ".join(text.split())[:maximum]


def _safe_result_url(value):
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password):
        return ""
    clean_query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() in SENSITIVE_QUERY_KEYS:
            continue
        clean_query.append((key, item))
    return urlunsplit((
        parsed.scheme.lower(), parsed.netloc, parsed.path,
        urlencode(clean_query, doseq=True), "",
    ))
