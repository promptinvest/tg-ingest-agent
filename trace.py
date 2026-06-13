#!/usr/bin/env python3
"""Structured tracing: one trace per inbound update / scheduler tick, with
staged events. Makes Cara debuggable as routing, memory, and personality
grow. Trace ids also stamp llm_usage and issues (via common.current_trace).

Stdlib only; single-threaded poll loop, so the "current trace" is a module
global in common.py.
"""
import secrets
import time

import store
from common import set_current_trace

# Stable stage names (see spec §28.6)
INBOUND_PERSISTED = "inbound.persisted"
ROUTER_COMPLETED = "router.completed"
SKILL_STARTED = "skill.started"
SKILL_COMPLETED = "skill.completed"
LLM_FALLBACK = "llm.fallback"
LLM_FAILED = "llm.failed"
STATE_WRITE = "state.write"
TELEGRAM_SEND = "telegram.send"
ISSUE_LOGGED = "issue.logged"
PROACTIVE_SUPPRESSED = "proactive.suppressed"
FINISHED = "trace.finished"


def new_trace_id(prefix="tr"):
    # time.time() is available on the host; ids only need to be unique + sortable.
    return f"{prefix}_{int(time.time())}_{secrets.token_hex(5)}"


def start(conn, kind, chat_id=None, prefix="tr"):
    trace_id = new_trace_id(prefix)
    store.trace_start(conn, trace_id, kind, chat_id)
    set_current_trace(trace_id)
    return trace_id


def event(conn, trace_id, stage, message, level="info", skill=None, data=None):
    if not trace_id:
        return
    store.trace_event(conn, trace_id, stage, message, level=level, skill=skill, data=data)


def finish(conn, trace_id, status, summary=None):
    if trace_id:
        store.trace_finish(conn, trace_id, status, summary)
    set_current_trace(None)
