#!/usr/bin/env python3
"""Remote resource reader: fetch a URL the operator sends and extract its
text so Cara can summarize it like a forwarded post.

Security: this runs inside the hardened VPS, so arbitrary-URL fetching is an
SSRF surface. Every URL (and every redirect hop) is validated — http/https
only, and the resolved IP must be public (no loopback/private/link-local/
reserved, and explicitly not the cloud metadata endpoint 169.254.169.254).
The connection is **pinned** to the exact IP we validated (`_pinned_ip` +
`_PinnedHTTP(S)Connection`): urllib would otherwise resolve the host a SECOND
time when it opens the socket, so a TTL=0 host could answer a public IP to the
check and 169.254.169.254 / 127.0.0.1 to the connect (DNS-rebinding TOCTOU).
Redirects are followed manually so each hop is re-validated and re-pinned.
v1 handles HTML/text pages and the public t.me web view; binaries, file
shares, and private channels are out of scope.
"""
import http.client
import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (HTTPRedirectHandler, HTTPHandler, HTTPSHandler, ProxyHandler,
                            Request, build_opener)

METADATA_IPS = {"169.254.169.254", "100.100.100.200", "fd00:ec2::254"}
MAX_TEXT_CHARS = 8000
MAX_REDIRECTS = 5
READ_CHUNK = 64 * 1024
# `timeout` bounds a SINGLE socket operation, not the whole transfer: a server
# that drips a few bytes every few seconds holds the connection for hours or
# days. fetch() runs INLINE in the single-threaded poll loop (and a forwarded
# link post auto-fetches its first URL), so one such server freezes the entire
# bot — reminders included. Every fetch therefore also gets a total wall-clock
# budget of DEADLINE_FACTOR × timeout, started at the FIRST hop and shared by
# every redirect hop.
# What that budget actually bounds, stated honestly: the HOP CHAIN and the BODY.
# It is checked at every hop boundary and before every body chunk, and the
# per-hop socket timeout is clamped to what is left. Inside one hop
# `opener.open()` — DNS, connect, TLS and the response headers — is a single
# opaque blocking call this code cannot interrupt (urllib reads headers with
# `readline` on the buffered socket), so there the only bound is the per-socket-
# operation timeout. A server that dribbles header bytes just under that timeout
# can still outlive the budget within one hop; closing that would need a
# socket-level deadline (a custom handler/connection class).
DEADLINE_FACTOR = 2


class FetchError(Exception):
    def __init__(self, message, reason="fetch_failed"):
        super().__init__(message)
        self.reason = reason  # fetch_failed | fetch_blocked | fetch_private


def _ip_blocked(ip_text):
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return True
    # `not is_global` is the catch-all (it also covers the carrier-grade-NAT /
    # Tailscale range 100.64.0.0/10, which is neither private nor reserved and
    # used to pass every explicit check); the explicit ones stay for clarity and
    # because the metadata set is an address list, not a network property.
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or not ip.is_global
            or ip_text in METADATA_IPS)


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


def _pinned_ip(host, port, scheme):
    """Resolve `host` ONCE, require every returned address to be public, and
    return the IP to pin the socket to. This is the resolution the connection
    actually uses — so validate-then-connect can't diverge (DNS rebinding)."""
    try:
        infos = socket.getaddrinfo(host, port or (443 if scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise FetchError(f"cannot resolve host: {exc}", "fetch_failed") from exc
    ips = [info[4][0] for info in infos]
    if not ips:
        raise FetchError("cannot resolve host", "fetch_failed")
    for ip in ips:
        if _ip_blocked(ip):
            raise FetchError(f"resolves to a non-public address ({ip})", "fetch_private")
    return ips[0]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, *args, pinned_ip=None, **kw):
        super().__init__(host, *args, **kw)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_ip or self.host, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, *args, pinned_ip=None, **kw):
        super().__init__(host, *args, **kw)
        self._pinned_ip = pinned_ip

    def connect(self):
        sock = socket.create_connection(
            (self._pinned_ip or self.host, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            sock = self.sock
        # SNI + certificate validation still use the real hostname, not the pinned IP.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPHandler(HTTPHandler):
    def __init__(self, pinned_ip):
        super().__init__()
        self._pinned_ip = pinned_ip

    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req, pinned_ip=self._pinned_ip)


class _PinnedHTTPSHandler(HTTPSHandler):
    def __init__(self, pinned_ip, context=None):
        super().__init__(context=context)
        self._pinned_ip = pinned_ip

    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req, pinned_ip=self._pinned_ip,
                            context=self._context)


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


class _Redirect(Exception):
    """Carries a redirect target out of the opener so fetch() can re-validate
    and re-pin the next hop itself (auto-following would reuse this hop's pin
    against a different host)."""
    def __init__(self, url):
        self.url = url


class _CaptureRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise _Redirect(newurl)


def _read_bounded(response, max_bytes, deadline):
    """Read the body ONE socket read at a time, giving up as soon as the total
    budget is gone.

    `response.read(n)` blocks until it has n bytes or the server closes — a drip
    feed satisfies the per-read timeout forever, so the loop would not regain
    control (and would not reach the deadline check below) for days: checking the
    deadline between reads is worth nothing if a single read can span them all.
    `http.client.HTTPResponse.read1` is documented as "read with at most one
    underlying system call", so every chunk hands control back here and the
    wall-clock deadline actually bounds the transfer."""
    read = getattr(response, "read1", None) or response.read
    chunks = []
    total = 0
    while total <= max_bytes:
        if deadline is not None and time.monotonic() > deadline:
            raise FetchError("fetch deadline exceeded", "fetch_failed")
        chunk = read(min(READ_CHUNK, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _decode_body(raw, ctype):
    """Decode with the declared charset; an unknown/quoted one must not lose the
    whole page (a bogus `charset=` raised LookupError and aborted the fetch)."""
    charset = "utf-8"
    if "charset=" in ctype:
        charset = ctype.split("charset=")[-1].split(";")[0].strip().strip('"\'') or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _fetch_one(url, timeout, max_bytes, deadline=None):
    """Fetch a single hop with the connection pinned to a validated IP. Returns
    (kind, value): ('ok', (final_url, html)) or ('redirect', newurl). `deadline`
    is a `time.monotonic()` stamp shared by every hop of one fetch."""
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError("fetch deadline exceeded", "fetch_failed")
        # DNS + connect + first byte can't outlive the budget either.
        timeout = min(timeout, remaining)
    validate_url(url)
    parsed = urlparse(url)
    pin = _pinned_ip(parsed.hostname, parsed.port, parsed.scheme)
    # ProxyHandler({}) disables proxy support: build_opener would otherwise install a
    # default ProxyHandler that honours HTTP(S)_PROXY env and routes through a proxy
    # that does its own DNS/connect — bypassing the IP pin (and an SSRF vector itself).
    opener = build_opener(ProxyHandler({}), _PinnedHTTPHandler(pin),
                          _PinnedHTTPSHandler(pin), _CaptureRedirect())
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (cara-assistant)"})
    try:
        with opener.open(request, timeout=timeout) as response:
            ctype = response.headers.get("Content-Type", "")
            if not any(t in ctype for t in ("text/html", "text/plain", "application/xhtml")):
                raise FetchError(f"unsupported content type: {ctype or 'unknown'}", "fetch_failed")
            raw = _read_bounded(response, max_bytes, deadline)
            if len(raw) > max_bytes:
                raw = raw[:max_bytes]
            return "ok", (response.geturl(), _decode_body(raw, ctype))
    except _Redirect as r:
        return "redirect", r.url
    except FetchError:
        raise
    except HTTPError as exc:
        raise FetchError(f"HTTP {exc.code}", "fetch_failed") from exc
    except (URLError, socket.timeout) as exc:
        raise FetchError(f"request failed: {getattr(exc, 'reason', exc)}", "fetch_failed") from exc
    except Exception as exc:
        raise FetchError(f"unexpected: {exc}", "fetch_failed") from exc


def fetch(url, timeout=20, max_bytes=2 * 1024 * 1024):
    """Fetch and extract text from a public HTTP(S) page. Returns
    (final_url, title, text). Raises FetchError on any problem. Redirects are
    followed manually so every hop is independently validated AND pinned.
    The hop chain and the body are capped at DEADLINE_FACTOR × `timeout`
    seconds; inside one hop (DNS, connect, TLS, response headers) only the
    per-socket-operation timeout applies — see the module note."""
    url = normalize_tme(url)
    deadline = time.monotonic() + DEADLINE_FACTOR * max(1, int(timeout or 1))
    for _ in range(MAX_REDIRECTS + 1):
        kind, value = _fetch_one(url, timeout, max_bytes, deadline)
        if kind == "ok":
            final_url, html = value
            title, text = extract_text(html)
            if not text.strip():
                raise FetchError("no readable text extracted", "fetch_failed")
            return final_url, title, text
        url = normalize_tme(value)   # 'redirect' -> re-validate + re-pin next loop
    raise FetchError("too many redirects", "fetch_failed")
