"""Source-bound fleet conversation frames; assistant prose is never scope authority."""
import json

import fleet_protocol as protocol


def frame(question, plan, inventory, previous=None, update=None):
    previous = previous or {}
    inherited = plan.get("scope") == "inherit" and previous
    return {"version": 1, "hosts": list(plan["hosts"]),
            "projects": list(plan.get("projects", [])),
            "facets": list(plan.get("facets", [])),
            "focus": list(plan.get("focus", ["overview"])),
            "original_question": previous.get("original_question", question) if inherited else question,
            "question": question, "source_update": update,
            "inventory_hash": protocol.digest(inventory)}


def recent(conn, chat_id, text=""):
    row = conn.execute("SELECT * FROM fleet_requests WHERE chat_id=? AND state IN ('done','queued','ready','approval') ORDER BY created_at DESC LIMIT 1", (chat_id,)).fetchone()
    if not row or not -30 <= protocol.age(row["created_at"]) < 7200:
        return {}
    payload = json.loads(row["payload"])
    source = conn.execute("SELECT id,text FROM conversation WHERE chat_id=? AND update_id=? AND role='user' AND source='boss' ORDER BY id DESC LIMIT 1", (chat_id, row["source_update"])).fetchone()
    if not source or source["text"] != payload.get("question"):
        return {}
    turns = conn.execute("SELECT id,text FROM conversation WHERE chat_id=? AND role='user' AND source='boss' AND id>? ORDER BY id LIMIT 101", (chat_id, source["id"])).fetchall()
    if turns and text and turns[-1]["text"] == text:
        turns = turns[:-1]
    if turns:
        return {}  # An intervening owner task ends this frame.
    context = payload.get("context")
    if context:
        return context
    # Read old receipts additively, without importing old assistant assertions.
    plan = payload.get("plan", {})
    return {"version": 1, "hosts": plan.get("hosts", []), "projects": [],
            "facets": plan.get("facets", []), "focus": ["overview"],
            "original_question": payload["question"], "question": payload["question"],
            "source_update": row["source_update"], "inventory_hash": payload.get("catalog_hash")}


FOCUS = frozenset({"overview", "resources", "memory", "cpu", "disk", "services", "projects",
                   "errors", "release", "progress", "backups", "trend"})


def scope(value, inventory, previous):
    """Resolve declared relationships in code; a model cannot shrink inherited scope."""
    mode = value.get("scope", "explicit")
    if not isinstance(mode, str) or mode not in {"explicit", "all", "inherit"}:
        raise ValueError("unknown fleet scope")
    if mode == "all":
        value["hosts"] = list(inventory["hosts"])
    if mode == "inherit":
        if not previous or previous.get("inventory_hash") != protocol.digest(inventory):
            raise ValueError("no current fleet conversation scope")
        value["hosts"] = list(previous["hosts"])
    projects = value.get("projects", [])
    focus = value.get("focus", ["overview"])
    # A single closed hint has the same meaning as a one-element hint array.
    # Focus is a report hint, never an address, command or maintenance authority.
    if isinstance(focus, str):
        focus = [focus]
    if (not isinstance(projects, list) or len(projects) > 20 or any(not isinstance(p, str) for p in projects) or len(set(projects)) != len(projects)
            or any(p not in inventory["projects"] for p in projects)):
        raise ValueError("unknown fleet project")
    if (not isinstance(focus, list) or not 1 <= len(focus) <= 5
            or any(not isinstance(f, str) or f not in FOCUS for f in focus)):
        raise ValueError("unknown investigation focus")
    if projects and mode == "inherit" and set(projects) != set(previous.get("projects", [])):
        # A newly selected named project starts a new subject. It must not retain
        # every host of a prior fleet overview because the model said inherit.
        mode = "explicit"
        value["hosts"] = sorted({h for p in projects for h in inventory["projects"][p]["hosts"]})
    elif mode == "explicit" and projects and not value["hosts"]:
        value["hosts"] = sorted({h for p in projects for h in inventory["projects"][p]["hosts"]})
    return mode, projects, list(dict.fromkeys(focus))
