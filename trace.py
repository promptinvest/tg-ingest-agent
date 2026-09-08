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

# Stable stage names (see spec §28.6). Only the stages something EMITS are
# named here (ADR-0019, 2026-09-08): eight constants that nothing ever wrote
# were removed, so a reader of a trace no longer looks for stages that cannot
# occur. Free-form stage strings ("llm.all_benched", "grounding.ranked", …) are
# emitted directly by their call sites.
ROUTER_COMPLETED = "router.completed"
LLM_FALLBACK = "llm.fallback"
ISSUE_LOGGED = "issue.logged"


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
