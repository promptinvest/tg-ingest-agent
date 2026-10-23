"""Closed, versioned fleet requests. Model output is never an address or command."""
import hashlib
import json
import math
import re
from datetime import datetime, timezone

VERSION = "cara-fleet/v1"
MAX_BYTES = 60000
FACETS = frozenset({"resources", "services", "projects", "errors"})
ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
TOKEN = re.compile(r"[a-f0-9]{32}\Z")
SCHEMA = """
CREATE TABLE IF NOT EXISTS fleet_requests (
 id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, source_update INTEGER NOT NULL,
 state TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, UNIQUE(chat_id, source_update)
);
CREATE TABLE IF NOT EXISTS fleet_events (
 id INTEGER PRIMARY KEY, request_id TEXT NOT NULL REFERENCES fleet_requests(id),
 kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fleet_state ON fleet_requests(state, updated_at);
"""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def age(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def inventory(value):
    if not isinstance(value, dict) or set(value) != {"version", "hosts", "projects"}:
        raise ValueError("invalid public fleet inventory")
    if value["version"] != VERSION or not 1 <= len(value["hosts"]) <= 8:
        raise ValueError("invalid fleet inventory version/size")
    for key, host in value["hosts"].items():
        if not ID.fullmatch(key) or not isinstance(host, dict):
            raise ValueError("invalid host id")
        if set(host) != {"label", "role", "aliases", "restart_services"}:
            raise ValueError("invalid host fields")
        if not isinstance(host["aliases"], list) or len(host["aliases"]) > 12:
            raise ValueError("invalid aliases")
        for text in [host["label"], host["role"], *host["aliases"]]:
            if not isinstance(text, str) or not 1 <= len(text) <= 300:
                raise ValueError("invalid host description")
        if not isinstance(host["restart_services"], list):
            raise ValueError("invalid maintenance policy")
        for unit in host["restart_services"]:
            if not re.fullmatch(r"[a-zA-Z0-9_.@-]{1,80}\.service", unit):
                raise ValueError("invalid service")
    if not isinstance(value["projects"], dict) or len(value["projects"]) > 20:
        raise ValueError("invalid project map")
    for key, project in value["projects"].items():
        if not ID.fullmatch(key) or set(project) != {"label", "hosts", "recorded_at", "notes"}:
            raise ValueError("invalid project")
        if (not isinstance(project["hosts"], list) or not project["hosts"]
                or any(h not in value["hosts"] for h in project["hosts"])):
            raise ValueError("unknown project host")
        for field, maximum in (("label", 120), ("notes", 2400), ("recorded_at", 80)):
            if not isinstance(project[field], str) or len(project[field]) > maximum:
                raise ValueError("invalid project record")
    if len(canonical(value).encode()) > 20000:
        raise ValueError("inventory too large")
    return value


def plan(value, catalog):
    """Only inventory IDs and read facets may be chosen by the model."""
    if not isinstance(value, dict) or set(value) != {"hosts", "facets", "clarification"}:
        raise ValueError("invalid investigation plan")
    hosts, facets = value["hosts"], value["facets"]
    if (not isinstance(hosts, list) or len(hosts) > 8
            or len(set(hosts)) != len(hosts)
            or any(h not in catalog["hosts"] for h in hosts)):
        raise ValueError("unknown/duplicate investigation target")
    if (not isinstance(facets, list) or not facets or len(facets) > 4
            or any(f not in FACETS for f in facets)):
        raise ValueError("unknown investigation facet")
    question = value["clarification"]
    if not isinstance(question, str) or len(question) > 350 or (not hosts and not question):
        raise ValueError("missing target clarification")
    return {"hosts": hosts, "facets": sorted(set(facets)), "clarification": question}


def request(value, catalog):
    if not isinstance(value, dict) or value.get("operation") not in {"inspect", "restart"}:
        raise ValueError("unknown fleet operation")
    if value["operation"] == "inspect":
        if set(value) != {"operation", "hosts", "facets", "inventory_hash"}:
            raise ValueError("invalid inspection fields")
        plan({"hosts": value["hosts"], "facets": value["facets"], "clarification": ""}, catalog)
    else:
        if set(value) != {"operation", "host", "service", "fingerprint", "token", "expires", "inventory_hash"}:
            raise ValueError("invalid maintenance fields")
        if (value["host"] not in catalog["hosts"] or value["service"] not in
                catalog["hosts"][value["host"]]["restart_services"]):
            raise ValueError("maintenance target unavailable")
        if not TOKEN.fullmatch(str(value["token"])) or not re.fullmatch(r"[a-f0-9]{64}", str(value["fingerprint"])):
            raise ValueError("invalid maintenance binding")
        if not -610 <= age(value["expires"]) < 0:
            raise ValueError("maintenance authorization expired")
    if value["inventory_hash"] != digest(catalog):
        raise ValueError("inventory changed; start a new investigation")
    return value


def safe_tree(value, depth=0):
    """Host replies contain structured measurements, never arbitrary log prose."""
    if depth > 8:
        raise ValueError("fleet result too deep")
    if isinstance(value, dict):
        if len(value) > 40:
            raise ValueError("fleet object too large")
        return {str(k)[:80]: safe_tree(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) > 24:
            raise ValueError("fleet list too large")
        return [safe_tree(v, depth + 1) for v in value]
    if isinstance(value, str):
        if len(value) > 200:
            raise ValueError("fleet string too long")
        return value
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("invalid fleet value")


def result(value, expected):
    if (not isinstance(value, dict) or set(value) != {"version", "observed_at", "hosts"}
            or value["version"] != VERSION or not -30 <= age(value["observed_at"]) <= 180):
        raise ValueError("stale or malformed fleet result")
    if not isinstance(value["hosts"], dict) or set(value["hosts"]) != set(expected):
        raise ValueError("fleet result target mismatch")
    safe_tree(value)
    if len(canonical(value).encode()) > MAX_BYTES:
        raise ValueError("fleet result too large")
    return value
