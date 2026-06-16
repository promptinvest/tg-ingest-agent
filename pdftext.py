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
_TJ = re.compile(r"\((?:\\.|[^()\\])*\)\s*Tj")
_TJ_ARRAY = re.compile(r"\[(.*?)\]\s*TJ", re.DOTALL)
_PAREN = re.compile(r"\((?:\\.|[^()\\])*\)")


def extract_text(data, max_chars=20000):
    """Return extracted text, or "" if the PDF has no usable text layer.
    Tries pdfminer first (real PDFs, compressed/object streams), then the
    lightweight regex extractor; returns "" if neither yields readable text."""
    if _pdfminer_extract is not None:
        try:
            text = _pdfminer_extract(io.BytesIO(data or b""), maxpages=20) or ""
        except Exception:
            text = ""
        text = re.sub(r"[ \t]+", " ", text).strip()[:max_chars]
        if _looks_like_text(text):
            return text
    try:
        text = _extract(data or b"", max_chars)
    except Exception:
        return ""
    return text if _looks_like_text(text) else ""


def _extract(data, max_chars):
    chunks = []
    total = 0
    for m in _STREAM.finditer(data):
        raw = m.group(1)
        try:
            decoded = zlib.decompress(raw)
        except Exception:
            decoded = raw  # not flate-encoded; may already be plain content
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
