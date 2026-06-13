#!/usr/bin/env python3
"""Remote resource reader: fetch a URL the operator sends and extract its
text so Cara can summarize it like a forwarded post.

Security: this runs inside the hardened VPS, so arbitrary-URL fetching is an
SSRF surface. Every URL (and every redirect hop) is validated — http/https
only, and the resolved IP must be public (no loopback/private/link-local/
reserved, and explicitly not the cloud metadata endpoint 169.254.169.254).
v1 handles HTML/text pages and the public t.me web view; binaries, file
shares, and private channels are out of scope.
"""
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

METADATA_IPS = {"169.254.169.254", "100.100.100.200", "fd00:ec2::254"}
MAX_TEXT_CHARS = 8000


class FetchError(Exception):
    def __init__(self, message, reason="fetch_failed"):
        super().__init__(message)
        self.reason = reason  # fetch_failed | fetch_blocked | fetch_private


def _ip_blocked(ip_text):
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return True
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or ip_text in METADATA_IPS)


def validate_url(url):
    """Return the safe URL to fetch, or raise FetchError. Resolves the host
    and rejects any non-public address."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"unsupported scheme: {parsed.scheme}", "fetch_blocked")
    if parsed.username or parsed.password:
        raise FetchError("credentials in URL not allowed", "fetch_blocked")
    host = parsed.hostname
    if not host:
        raise FetchError("no host in URL", "fetch_blocked")
    if host in METADATA_IPS:
        raise FetchError("metadata endpoint blocked", "fetch_private")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise FetchError(f"cannot resolve host: {exc}", "fetch_failed") from exc
    for info in infos:
        if _ip_blocked(info[4][0]):
            raise FetchError(f"resolves to a non-public address ({info[4][0]})", "fetch_private")
    return url.strip()


_TME_RE = re.compile(r"^https?://t\.me/([A-Za-z0-9_]+)/(\d+)/?$")


def normalize_tme(url):
    """Public channel post -> public web-view URL (t.me/s/<channel>/<id>),
    which is server-rendered HTML we can read. Private/joinchat links are
    left as-is (and will simply not yield content)."""
    m = _TME_RE.match(url.strip())
    if m and m.group(1) not in ("s", "c", "joinchat", "addstickers", "proxy"):
        return f"https://t.me/s/{m.group(1)}/{m.group(2)}"
    return url


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "head", "svg"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        # Title lives inside <head> (which we skip for body text), so capture
        # it before the skip guard.
        if self._in_title and not self.title:
            self.title = text[:200]
            return
        if self._skip:
            return
        self.parts.append(text)


def extract_text(html):
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    text = re.sub(r"\s+\n", "\n", "\n".join(parser.parts))
    text = re.sub(r"[ \t]{2,}", " ", text)
    return parser.title, text[:MAX_TEXT_CHARS]


class _NoPrivateRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)  # re-check every hop; raises FetchError if unsafe
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, timeout=20, max_bytes=2 * 1024 * 1024):
    """Fetch and extract text from a public HTTP(S) page. Returns
    (final_url, title, text). Raises FetchError on any problem."""
    url = normalize_tme(url)
    validate_url(url)
    opener = build_opener(_NoPrivateRedirect())
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (cara-assistant)"})
    try:
        with opener.open(request, timeout=timeout) as response:
            ctype = response.headers.get("Content-Type", "")
            if not any(t in ctype for t in ("text/html", "text/plain", "application/xhtml")):
                raise FetchError(f"unsupported content type: {ctype or 'unknown'}", "fetch_failed")
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raw = raw[:max_bytes]
            charset = "utf-8"
            if "charset=" in ctype:
                charset = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
            html = raw.decode(charset, errors="replace")
            final_url = response.geturl()
    except FetchError:
        raise
    except HTTPError as exc:
        raise FetchError(f"HTTP {exc.code}", "fetch_failed") from exc
    except (URLError, socket.timeout) as exc:
        raise FetchError(f"request failed: {getattr(exc, 'reason', exc)}", "fetch_failed") from exc
    except Exception as exc:
        raise FetchError(f"unexpected: {exc}", "fetch_failed") from exc
    title, text = extract_text(html)
    if not text.strip():
        raise FetchError("no readable text extracted", "fetch_failed")
    return final_url, title, text
