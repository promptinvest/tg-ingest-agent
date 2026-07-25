#!/usr/bin/env python3
"""Best-effort PDF text extraction, stdlib-only.

Pulls the text layer out of simple PDFs (FlateDecode content streams + the
Tj/TJ text-showing operators). It deliberately returns "" rather than guess
when the result doesn't look like real text — so scanned/image PDFs (which need
OCR) and CID-font PDFs (common for Cyrillic) fall back to honest "couldn't read
it" handling instead of feeding garbage to the summarizer. Never raises.
"""
import io
import re
import zlib

try:  # pdfminer.six (apt: python3-pdfminer) — handles real/ObjStm PDFs the
    from pdfminer.high_level import extract_text as _pdfminer_extract  # regex can't.
except Exception:  # not installed (e.g. local dev) -> regex fallback only
    _pdfminer_extract = None

_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\n?endstream", re.DOTALL)
# A forwarded PDF is attacker-supplied: a few KB of crafted FlateDecode inflates
# to gigabytes, and an OOM kill of this single-process service is followed by a
# restart straight back into the same retry. Bound both dimensions of the bomb —
# how much ONE stream may inflate to, and how many streams are read at all.
MAX_STREAMS = 200
# ...and the same bomb has to be refused BEFORE pdfminer, which is the PRIMARY
# path on the box (apt python3-pdfminer) and decodes FlateDecode with an
# unbounded zlib.decompress: bounding only the stdlib fallback would leave the
# OOM fully live, because the fallback runs only AFTER pdfminer returned. The
# pre-scan below only ever COUNTS inflated bytes (it keeps at most one chunk) and
# stops the moment the ceiling is crossed, so it costs a bomb ~1 s and an
# ordinary document the price of inflating its own content streams once.
# 128 MB is far above any real text PDF (Telegram caps a download at 20 MB) and
# far below what would kill the service. Best effort: it reads the streams this
# module's regex can find, so a PDF that hides its streams from that regex is
# still handed to pdfminer unbounded.
MAX_INFLATED_BYTES = 128 * 1024 * 1024
_INFLATE_CHUNK = 1024 * 1024
_TJ = re.compile(r"\((?:\\.|[^()\\])*\)\s*Tj")
_TJ_ARRAY = re.compile(r"\[(.*?)\]\s*TJ", re.DOTALL)
_PAREN = re.compile(r"\((?:\\.|[^()\\])*\)")


def extract_text(data, max_chars=20000):
    """Return extracted text, or "" if the PDF has no usable text layer.
    Tries pdfminer first (real PDFs, compressed/object streams), then the
    lightweight regex extractor; returns "" if neither yields readable text.
    A decompression bomb is refused before either of them runs."""
    data = data or b""
    if _is_decompression_bomb(data):
        return ""
    if _pdfminer_extract is not None:
        try:
            text = _pdfminer_extract(io.BytesIO(data), maxpages=20) or ""
        except Exception:
            text = ""
        text = re.sub(r"[ \t]+", " ", text).strip()[:max_chars]
        if _looks_like_text(text):
            return text
    try:
        text = _extract(data, max_chars)
    except Exception:
        return ""
    return text if _looks_like_text(text) else ""


def _inflated_bytes(raw, budget):
    """How many bytes this stream inflates to, counted up to `budget` and never
    kept — the point is to learn the SIZE without ever holding it. A stream that
    isn't flate-encoded inflates to nothing."""
    seen = 0
    pending = raw
    try:
        d = zlib.decompressobj()
        while pending and seen < budget:
            out = d.decompress(pending, _INFLATE_CHUNK)
            tail = d.unconsumed_tail
            if not out and tail == pending:
                break  # no progress: truncated/corrupt stream
            seen += len(out)
            pending = tail
    except Exception:
        return 0
    return seen


def _is_decompression_bomb(data):
    """True when the document's FlateDecode streams inflate past
    MAX_INFLATED_BYTES in total. Counting stops at the ceiling, so the check
    itself is bounded in both time and memory."""
    budget = MAX_INFLATED_BYTES
    for m in _STREAM.finditer(data):
        budget -= _inflated_bytes(m.group(1), budget + 1)
        if budget < 0:
            return True
    return False


def _inflate_bounded(raw, limit):
    """Decompress at most `limit` bytes. `zlib.decompress` is unbounded (the
    decompression-bomb path); the incremental decompressor stops at the cap and
    the rest of the stream is simply treated as end-of-stream — a text layer we
    can only read up to the cap is still read up to the cap."""
    try:
        # max_length=0 means UNLIMITED in the zlib API — never pass it through.
        return zlib.decompressobj().decompress(raw, max(1, int(limit or 0)))
    except Exception:
        return raw  # not flate-encoded; may already be plain content


def _extract(data, max_chars):
    chunks = []
    total = 0
    for streams, m in enumerate(_STREAM.finditer(data)):
        if streams >= MAX_STREAMS:
            break
        raw = m.group(1)
        decoded = _inflate_bounded(raw, 4 * max_chars)
        piece = _text_from_content(decoded)
        if piece:
            chunks.append(piece)
            total += len(piece)
            if total > max_chars:
                break
    return re.sub(r"[ \t]+", " ", " ".join(chunks)).strip()[:max_chars]


def _text_from_content(content):
    try:
        s = content.decode("latin-1", errors="ignore")
    except Exception:
        return ""
    parts = []
    for m in _TJ.finditer(s):
        parts.append(_pdf_string(m.group(0)))
    for m in _TJ_ARRAY.finditer(s):
        for sm in _PAREN.finditer(m.group(1)):
            parts.append(_pdf_string(sm.group(0)))
    return "".join(parts)


def _pdf_string(token):
    m = re.search(r"\((.*)\)", token, re.DOTALL)
    if not m:
        return ""
    s = m.group(1)
    for a, b in ((r"\(", "("), (r"\)", ")"), (r"\n", "\n"), (r"\r", "\r"),
                 (r"\t", "\t"), (r"\\", "\\")):
        s = s.replace(a, b)
    return s


def _looks_like_text(s):
    """True only if the result reads like real prose, not glyph-code noise:
    enough letters, and letters dominate the non-space characters."""
    if len(s) < 20:
        return False
    letters = sum(1 for c in s if c.isalpha())
    nonspace = sum(1 for c in s if not c.isspace())
    return letters >= 20 and nonspace and letters / nonspace >= 0.5
