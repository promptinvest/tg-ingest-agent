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
        if set(value) not in ({"operation", "hosts", "facets", "inventory_hash"}, {"operation", "hosts", "facets", "inventory_hash", "read_scope"}):
            raise ValueError("invalid inspection fields")
        plan({"hosts": value["hosts"], "facets": value["facets"], "clarification": ""}, catalog)
        if "read_scope" in value:
            scope = read_scope(value["read_scope"], catalog, value["hosts"])
            if scope["kind"] == "projects" and value["facets"] != ["projects"]:
                raise ValueError("selected project read cannot collect host-wide facets")
            if scope["kind"] == "host" and "projects" in value["facets"]:
                raise ValueError("host infrastructure read cannot collect projects")
            if scope["kind"] != "host" and "projects" not in value["facets"]:
                raise ValueError("project read facet missing")
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


# Additive read contract. The legacy inventory, spool and maintenance versions
# remain unchanged; these IDs never supply a path, URL, unit or command.
READ_VERSION = "cara-fleet-project-read/v1"
PROJECT_CAPABILITIES = {
    "release": {"implementation_version": "project-release/v1", "outputs": ["schema", "source_revision", "source", "build_version", "image", "installed_at", "verified_at", "release_status", "policy_version"]},
    "http_health": {"implementation_version": "project-http-health/v1", "outputs": ["http_status", "health"]},
    "containers": {"implementation_version": "project-containers/v1", "outputs": ["containers"]},
    "runtime": {"implementation_version": "project-runtime/v1", "outputs": ["units"]},
    "backups": {"implementation_version": "project-backups-gap/v1", "outputs": []},
    "development": {"implementation_version": "project-development-gap/v1", "outputs": []},
}
READ_STATUSES = frozenset({"observed", "unavailable", "not_found", "not_configured", "recorded"})
READ_REASONS = frozenset({"not_configured", "observation_unavailable", "resource_not_found", "gateway_contract_unavailable", "time_limit"})


def capability_contract():
    """Transport-independent allowlisted reads; no model-supplied resource inputs."""
    return {name: dict(spec, outputs=list(spec["outputs"]), inputs=[], policy_version=READ_VERSION, location="host-gateway",
                       freshness_seconds=90, window_seconds=None, command_timeout_seconds=5, request_timeout_seconds=30, maximum_bytes=10000, maximum_source_bytes=65536,
                       redaction="fixed-field-allowlist") for name, spec in PROJECT_CAPABILITIES.items()}


def read_scope(value, catalog=None, hosts=None):
    if not isinstance(value, dict) or set(value) != {"version", "kind", "projects", "capabilities"} or value["version"] != READ_VERSION:
        raise ValueError("unsupported project read contract")
    kind, projects, capabilities = value["kind"], value["projects"], value["capabilities"]
    if not isinstance(kind, str) or kind not in {"host", "projects", "all_projects"}:
        raise ValueError("unknown fleet read scope")
    for items, maximum in ((projects, 20), (capabilities, len(PROJECT_CAPABILITIES))):
        if not isinstance(items, list) or len(items) > maximum or any(not isinstance(item, str) for item in items) or len(items) != len(set(items)):
            raise ValueError("invalid scoped read identifiers")
    if any(not ID.fullmatch(project) for project in projects) or any(capability not in PROJECT_CAPABILITIES for capability in capabilities):
        raise ValueError("unknown project/capability identifier")
    if (kind == "host" and (projects or capabilities)) or (kind != "host" and not capabilities) or (kind == "projects" and not projects):
        raise ValueError("inconsistent fleet read scope")
    if catalog is not None:
        if any(project not in catalog["projects"] for project in projects):
            raise ValueError("unknown project read target")
        selected_hosts = set(hosts or [])
        if kind == "projects" and (any(not selected_hosts & set(catalog["projects"][project]["hosts"]) for project in projects) or
                                   any(not any(host in catalog["projects"][project]["hosts"] for project in projects) for host in selected_hosts)):
            raise ValueError("project read host mismatch")
        if kind == "all_projects" and set(projects) != {project for project, record in catalog["projects"].items() if set(record["hosts"]) & selected_hosts}:
            raise ValueError("incomplete explicit all-project scope")
    return value


validate_read_scope = read_scope


def scoped_inspection(catalog, hosts, projects, capabilities, kind="projects", facets=None):
    scope = {"version": READ_VERSION, "kind": kind, "projects": list(projects), "capabilities": list(capabilities)}
    operation = {"operation": "inspect", "hosts": list(hosts), "facets": list(facets if facets is not None else (["resources", "services", "errors"] if kind == "host" else ["projects"])),
                 "inventory_hash": digest(catalog), "read_scope": scope}
    return request(operation, catalog)


def scope_for_host(scope, catalog, host):
    selected = dict(scope)
    selected["projects"] = [project for project in scope["projects"] if host in catalog["projects"][project]["hosts"]]
    selected["capabilities"] = list(scope["capabilities"])
    return selected


def capability_observation(capability, status, reason=None, **fields):
    value = {"status": status, "implementation_version": PROJECT_CAPABILITIES[capability]["implementation_version"], "observed_at": now(), **fields}
    if reason is not None:
        value["reason"] = reason
    return value


def _capability_payload(capability, observation):
    fields = set(observation) - {"status", "observed_at", "implementation_version", "reason"}
    if observation["status"] != "observed":
        if fields:
            raise ValueError("unavailable capability carries unsupported data")
        return
    if not fields:
        raise ValueError("observed capability has no evidence")
    if capability == "release":
        if any(not isinstance(observation[field], (str, bool, int)) or len(str(observation[field])) > 150 for field in fields):
            raise ValueError("invalid release observation")
    elif capability == "http_health":
        if type(observation.get("http_status")) is not int or not 100 <= observation["http_status"] <= 599:
            raise ValueError("invalid application HTTP observation")
        health = observation.get("health", {})
        if not isinstance(health, dict) or set(health) - {"status", "database", "delivery", "intake"} or any(not isinstance(value, (str, bool, int)) or len(str(value)) >= 100 for value in health.values()):
            raise ValueError("invalid application health observation")
    elif capability in {"containers", "runtime"}:
        mapping = observation.get("containers" if capability == "containers" else "units")
        if not isinstance(mapping, dict) or not 1 <= len(mapping) <= (4 if capability == "containers" else 8):
            raise ValueError("invalid project resource observation")
        allowed = ({"Status", "Running", "Restarting", "OOMKilled", "StartedAt", "health", "image_id", "available"} if capability == "containers" else
                   {"LoadState", "ActiveState", "SubState", "Type", "NRestarts", "InvocationID", "ExecMainStartTimestampMonotonic", "MainPID", "MemoryCurrent", "CPUUsageNSec", "Result", "expected_state", "available"})
        for resource, state in mapping.items():
            pattern = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}" if capability == "containers" else r"[A-Za-z0-9_.@-]{1,80}\.(?:service|timer)"
            if not isinstance(resource, str) or not re.fullmatch(pattern, resource) or not isinstance(state, dict) or set(state) - allowed:
                raise ValueError("invalid scoped resource fields")
            if any(not isinstance(value, (str, bool, int)) or len(str(value)) > 100 for value in state.values()):
                raise ValueError("invalid scoped resource values")
            if "available" in state and state["available"] is not False:
                raise ValueError("invalid resource availability")
            if capability == "containers" and any(field in state and type(state[field]) is not bool for field in ("Running", "Restarting", "OOMKilled")):
                raise ValueError("invalid container state")
            if capability == "runtime" and state.get("expected_state") not in {"active", "idle", "disabled", "standby", "unknown"}:
                raise ValueError("invalid expected service state")
    else:
        raise ValueError("unconnected capability cannot claim observed evidence")

def scoped_host_result(value, host, scope, facets):
    """Reject broad/mismatched gateway output before it enters a durable receipt."""
    read_scope(scope)
    if (not isinstance(value, dict) or set(value) != {"host", "observed_at", "read_scope", *facets}
            or value.get("host") != host or value.get("read_scope") != scope or not -30 <= age(value.get("observed_at")) <= 90):
        raise ValueError("scoped host binding mismatch")
    if "projects" in facets:
        projects = value["projects"]
        if not isinstance(projects, dict) or set(projects) != set(scope["projects"]):
            raise ValueError("scoped project result mismatch")
        for project in projects.values():
            if not isinstance(project, dict) or set(project) != set(scope["capabilities"]):
                raise ValueError("scoped capability result mismatch")
            for capability, observation in project.items():
                allowed = {"status", "observed_at", "implementation_version", "reason", *PROJECT_CAPABILITIES[capability]["outputs"]}
                if (not isinstance(observation, dict) or not {"status", "observed_at", "implementation_version"} <= set(observation) or set(observation) - allowed
                        or not isinstance(observation["status"], str) or observation["status"] not in READ_STATUSES or observation["implementation_version"] != PROJECT_CAPABILITIES[capability]["implementation_version"]
                        or not -30 <= age(observation["observed_at"]) <= 90 or ("reason" in observation and observation["reason"] not in READ_REASONS)):
                    raise ValueError("invalid scoped capability observation")
                _capability_payload(capability, observation)
    safe_tree(value)
    return value


def read_discovery(value, host, scope):
    if (not isinstance(value, dict) or set(value) != {"host", "observed_at", "read_version", "read_scope", "capabilities"}
            or value["host"] != host or value["read_version"] != READ_VERSION or value["read_scope"] != scope
            or not -30 <= age(value["observed_at"]) <= 90 or not isinstance(value["capabilities"], dict)
            or set(value["capabilities"]) != set(scope["projects"])):
        raise ValueError("unsupported scoped gateway")
    for project in value["capabilities"].values():
        if not isinstance(project, dict) or set(project) != set(scope["capabilities"]):
            raise ValueError("invalid gateway capability discovery")
        for capability, metadata in project.items():
            if not isinstance(metadata, dict) or set(metadata) != {"configured", "implementation_version"} or type(metadata["configured"]) is not bool or metadata["implementation_version"] != PROJECT_CAPABILITIES[capability]["implementation_version"]:
                raise ValueError("unsupported gateway capability version")
    safe_tree(value)
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


def result(value, expected, read_scope=None):
    fields = {"version", "observed_at", "hosts"} | ({"read_scope"} if read_scope is not None else set())
    if (not isinstance(value, dict) or set(value) != fields
            or value["version"] != VERSION or not -30 <= age(value["observed_at"]) <= 180):
        raise ValueError("stale or malformed fleet result")
    if not isinstance(value["hosts"], dict) or set(value["hosts"]) != set(expected):
        raise ValueError("fleet result target mismatch")
    if read_scope is not None and value["read_scope"] != read_scope:
        raise ValueError("fleet result read scope mismatch")
    if read_scope is not None:
        # The executor binds per-host project intersections. The owner side also
        # rejects any broad/foreign IDs or host metadata in selected-project reads.
        validate_read_scope(read_scope)
        for host, raw in value["hosts"].items():
            selected = raw.get("read_scope", {}) if isinstance(raw, dict) else {}
            if (not isinstance(selected, dict) or selected.get("kind") != read_scope["kind"] or selected.get("version") != read_scope["version"]
                    or selected.get("capabilities") != read_scope["capabilities"] or not isinstance(selected.get("projects"), list)
                    or not set(selected["projects"]) <= set(read_scope["projects"])):
                raise ValueError("scoped host result exceeds requested projects")
            facets = [field for field in raw if field not in {"host", "observed_at", "read_scope"}]
            if not set(facets) <= FACETS or (read_scope["kind"] == "projects" and facets != ["projects"]) or (read_scope["kind"] == "host" and "projects" in facets):
                raise ValueError("scoped result includes unrelated host facets")
            scoped_host_result(raw, host, selected, facets)
    safe_tree(value)
    if len(canonical(value).encode()) > MAX_BYTES:
        raise ValueError("fleet result too large")
    return value
