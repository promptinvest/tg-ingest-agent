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
# Input handed to zlib in ONE decompress call. Whatever zlib does not consume it
# copies back OUT — `unconsumed_tail`, and at Z_STREAM_END CPython memcpy's the
# entire remainder into `unused_data` — so a call's hidden cost is the size of
# what it was FED, not of what it read. Measuring each stream from its payload
# start to the end of the file (what "look past `endstream`" naively means) then
# costs one copy of the rest of the document PER STREAM: a 20 MB forward built
# from ~800 000 eight-byte VALID streams turns a bounded pre-scan into terabytes
# of memcpy on the one poll thread. A fixed input window keeps every copy small.
_SCAN_INPUT_CHUNK = 64 * 1024
# ...and reading past an `endstream` marker at all is budgeted across the whole
# document, because a payload can consume input while producing nothing (empty
# stored blocks), which the inflated-bytes ceiling alone would never stop. A
# well-formed PDF never spends any of this: its streams end where they say they
# do, and zlib reports that within the regex's own group.
MAX_SCAN_TAIL_BYTES = 16 * 1024 * 1024
# ...and the regex SEARCH itself is bounded BEFORE it runs (2026-07-27).
# `_STREAM`'s terminator starts with `\r?` — an optional repeat, not a literal —
# so CPython's literal-skip optimisation does not apply and each failed match
# walks `.*?` one position at a time to end-of-file. A failed attempt happens
# exactly at the `stream\n` markers with NO `endstream` anywhere after them,
# i.e. past the LAST `endstream`: a 20 MB forward of `stream\n` repeats made
# `finditer` itself the bomb (millions of full-buffer scans, zero matches, the
# guard concluding «not a bomb») — poll thread frozen past WatchdogSec, restart,
# replay, again. `bytes.count`/`rfind` are memchr-fast, and a well-formed PDF
# has nothing stream-like after its last `endstream` (only xref/trailer), so a
# tail whose failed-attempt work would exceed this budget is unverifiable by
# construction and refused like a bomb.
MAX_SCAN_MATCH_WORK = 64 * 1024 * 1024
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


def _inflated_bytes(view, start, stop, budget, tail_budget):
    """How many bytes the stream at `start` inflates to, counted up to `budget`
    and never kept — the point is to learn the SIZE without ever holding it. A
    stream that isn't flate-encoded inflates to nothing.

    `stop` is where the regex found `endstream`. When zlib has NOT finished by
    then, the payload really does run past that marker — the regex is non-greedy
    and a stored deflate block copies its input verbatim, so a bomb can carry the
    literal bytes «endstream» inside the compressed data and have this pre-scan
    measure a harmless prefix while pdfminer, which takes the length from the
    object dictionary, still inflates the whole thing. So the count continues
    into the rest of the file — but in `_SCAN_INPUT_CHUNK` windows, and only
    while `tail_budget` allows: both the per-call copy and the total past-marker
    read stay bounded, which is what keeps this scan O(document) rather than
    O(streams × document).

    A stream that breaks half-way still reports what it had already produced —
    the count is evidence about a bomb, and discarding it would hand the bomb a
    second bypass (append garbage, get measured at zero).

    Returns `(inflated, tail_bytes_read)`."""
    seen = 0
    tail_used = 0
    pos = start
    end = stop
    total = len(view)
    try:
        d = zlib.decompressobj()
        while seen < budget:
            chunk = view[pos:min(end, pos + _SCAN_INPUT_CHUNK)]
            if not len(chunk):
                # zlib wants more input: this stream did not end at the marker.
                grow = min(_SCAN_INPUT_CHUNK, total - end, tail_budget - tail_used)
                if grow <= 0 or pos <= start:
                    break   # nothing left, no allowance, or no progress at all
                end += grow
                tail_used += grow
                continue
            out = d.decompress(chunk, _INFLATE_CHUNK)
            seen += len(out)
            if d.eof:
                break       # zlib knows its own framing: the stream ended here
            consumed = len(chunk) - len(d.unconsumed_tail)
            if consumed <= 0 and not out:
                break       # no progress: truncated/corrupt stream
            pos += consumed
    except Exception:
        return seen, tail_used
    return seen, tail_used


def _scan_work_excessive(data):
    """True when merely SEARCHING for stream markers would freeze the thread.

    A failed `_STREAM` attempt walks `.*?` to end-of-file, and failed attempts
    happen exactly at the `stream\\n` markers with no `endstream` after them —
    i.e. past the LAST `endstream` (everywhere, when there is none). The
    product below is that failed-attempt work, prechecked with memchr-fast
    byte counts before any regex runs. A well-formed PDF ends in xref/trailer
    and spends none of it."""
    if b"endstream" not in data:
        tail_start = 0
    else:
        tail_start = data.rfind(b"endstream") + len(b"endstream")
    tail_len = len(data) - tail_start
    orphan_starts = (data.count(b"stream\n", tail_start)
                     + data.count(b"stream\r\n", tail_start))
    return orphan_starts * tail_len > MAX_SCAN_MATCH_WORK


def _is_decompression_bomb(data):
    """True when the document's FlateDecode streams inflate past
    MAX_INFLATED_BYTES in total. Counting stops at the ceiling, so the check
    itself is bounded in both time and memory — including the regex SEARCH
    (2026-07-27, see MAX_SCAN_MATCH_WORK): the match-finding cost is prechecked
    with memchr-fast byte counts before `finditer` is allowed to run at all."""
    if _scan_work_excessive(data):
        return True  # unverifiable-by-construction: refused like a bomb
    if b"endstream" not in data:
        # Nothing the regex could ever match (it needs the terminator): the
        # same «found no streams» verdict finditer would reach, at memchr
        # price instead of one failed end-of-file scan per `stream\n` marker.
        return False
    budget = MAX_INFLATED_BYTES
    tail_budget = MAX_SCAN_TAIL_BYTES
    view = memoryview(data)
    for m in _STREAM.finditer(data):
        inflated, tail_used = _inflated_bytes(view, m.start(1), m.end(1),
                                              budget + 1, tail_budget)
        budget -= inflated
        tail_budget -= tail_used
        if budget < 0:
            return True
        if tail_budget <= 0:
            # Streams that do not end where they claim have used up the whole
            # past-marker allowance, so the rest of the document cannot be
            # measured. Unverifiable is refused like a bomb: a well-formed PDF
            # never spends any of this budget, and a false positive costs a
            # «не смогла прочитать», not a wrong answer.
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
    # Same regex, same search-cost bomb as the pre-scan (2026-07-27): the
    # production path is already guarded by _is_decompression_bomb, but this
    # keeps a direct call — and any future reordering — bounded too.
    if _scan_work_excessive(data):
        return ""
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
