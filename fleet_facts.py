"""Validated descriptive fleet facts; no addresses here become execution authority.

The producer supplies a reviewed, allowlisted projection, never raw KB text.
``publish`` is called only inside an authenticated operator configuration channel;
its explicit authentication argument records that boundary, not a network login.
All retrieval is local. The existing privileged fleet inventory remains separate.
"""
import copy
import hashlib
import ipaddress
import json
import os
import re
import stat
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA = "cara-fleet-facts/v1"
PUBLICATION_SCHEMA = "cara-fleet-facts-publication/v1"
MAX_BYTES = 256000
ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
KINDS = frozenset({"owner-decision", "operational-record", "project-record", "derived-register", "synthetic"})
SOURCE_STATUS = frozenset({"accepted", "historical", "unverified"})
CLAIM_STATUS = frozenset({"recorded", "unknown", "conflict"})
PREDICATES = frozenset({"public_ipv4", "outbound_ipv4", "public_ipv6", "outbound_ipv6",
                        "provider", "region", "role", "purpose", "repository", "lifecycle",
                        "aliases", "public_url", "service_url"})
RELATIONS = frozenset({"hosted_on", "depends_on"})
LIFECYCLE = {"host": frozenset({"active", "retired", "excluded", "unverified", "testing"}),
             "project": frozenset({"deployed", "development", "retired", "excluded", "unverified", "testing"})}
COMPLETENESS = frozenset({"hosts", "projects", "hosted_projects", "project_hosts", "dependencies"})
_BAD_TEXT = re.compile(
    r"(?:[\x00-\x1f\x7f]|(?:https?|ssh|file|s3|tg|postgres(?:ql)?|mysql)://|"
    r"(?:^|\s)(?:/[^\s]+|[a-z]:[\\/]|~[\\/])|[\\`]|\$\(|[;&|<>]|"
    r"(?:bearer|password|passwd|secret|api[_ -]?key|access[_ -]?token|credential|"
    r"private[_ -]?key|secret[_ -]?canary)\s*[:=_-]|"
    r"(?:^|[\s(/])(?:\.ssh(?:/|$)|\.env(?:\.[\w.-]+)?(?:\b|$)|id_(?:rsa|ed25519|ecdsa)(?:\b|$)|known_hosts(?:\b|$)|authorized_keys(?:\b|$)|credentials?[/\.]|secrets?[/\.])|"
    r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b|\b\d{7,}\b|"
    r"-----BEGIN |\b(?:sk|ghp|github_pat|xox[baprs])[-_][a-z0-9_-]{8,}|"
    r"\b\d{5,}:[a-z0-9_-]{15,}|\b(?:root|admin)@|"
    r"\b(?:chat|telegram|user)[_-]?id\s*[:=]|\b(?:ssh|sudo|scp|rsync|systemctl)\s)", re.I)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _enum(value, choices):
    return isinstance(value, str) and value in choices


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError("invalid fleet facts fields")


def _identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value) or re.search(r"(?:user|chat|telegram)[_-]?\d{4,}", value):
        raise ValueError("invalid fleet facts identifier")
    return value


def _text(value, maximum=300):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or value != value.strip() or _BAD_TEXT.search(value):
        raise ValueError("unsafe fleet facts text")
    return value


def _date(value, timestamp=False):
    if value is None and not timestamp:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid fleet facts date")
    try:
        if not timestamp and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value):
            raise ValueError("UTC date required")
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid fleet facts date") from None


def _moment(value):
    parsed = _date(value)
    if parsed is None or isinstance(parsed, datetime):
        return parsed
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)


def _authority(claim, sources):
    ranks = {"owner-decision": 3, "operational-record": 2, "project-record": 2,
             "derived-register": 1, "synthetic": 2}
    return max((ranks[sources[source]["kind"]] for source in claim["source_ids"]
                if sources[source]["status"] == "accepted"), default=0)


def _ids(values, available, maximum=64, empty=False):
    if (not isinstance(values, list) or not (0 if empty else 1) <= len(values) <= maximum
            or any(not isinstance(v, str) for v in values) or len(values) != len(set(values))
            or any(v not in available for v in values)):
        raise ValueError("invalid fleet facts references")


def _provenance(claim, sources):
    _ids(claim["source_ids"], sources)
    _date(claim["evidence_at"])


def _value(predicate, value, kind, fixture):
    if predicate.endswith("ipv4") or predicate.endswith("ipv6"):
        if not isinstance(value, str):
            raise ValueError("invalid descriptive IP address")
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise ValueError("invalid descriptive IP address") from None
        version = 4 if predicate.endswith("ipv4") else 6
        documentation = (address.version == 4 and any(address in ipaddress.ip_network(net)
                         for net in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")))
        documentation = documentation or (address.version == 6 and address in ipaddress.ip_network("2001:db8::/32"))
        if address.version != version or not (address.is_global or (fixture and documentation)):
            raise ValueError("nonpublic descriptive IP address")
    elif predicate in {"public_url", "service_url"}:
        if not isinstance(value, str) or len(value) > 250:
            raise ValueError("invalid public service address")
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("invalid public service address") from None
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment
                or port not in {None, 443} or parsed.path not in {"", "/"}
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,200}[a-z0-9])?", host)
                or "." not in host or host.endswith((".local", ".internal", ".localhost"))):
            raise ValueError("invalid public service address")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("nonpublic service address")
    elif predicate == "repository":
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value):
            raise ValueError("invalid repository identity")
    elif predicate == "lifecycle":
        if not _enum(value, LIFECYCLE[kind]):
            raise ValueError("invalid fleet lifecycle")
    elif predicate == "aliases":
        if not isinstance(value, list) or not 1 <= len(value) <= 12 or any(not isinstance(v, str) for v in value) or len(set(value)) != len(value):
            raise ValueError("invalid fleet aliases")
        for alias in value:
            _text(alias, 120)
    else:
        _text(value, 800 if predicate == "purpose" else 300)


def seal(document):
    """Add content integrity to an explicit producer projection; infer no facts."""
    value = copy.deepcopy(document)
    value.pop("integrity", None)
    value["integrity"] = _hash(value)
    validate(value, allow_fixture=True)
    return value


def validate(document, allow_fixture=False):
    """Validate the complete v1 boundary and return an independent JSON tree."""
    top = {"schema", "fixture_only", "revision", "generated_at", "sources", "subjects",
           "facts", "relations", "completeness", "integrity"}
    _fields(document, top)
    if (document["schema"] != SCHEMA or type(document["fixture_only"]) is not bool
            or (document["fixture_only"] and allow_fixture is not True)
            or type(document["revision"]) is not int or not 1 <= document["revision"] <= 2**53-1):
        raise ValueError("invalid fleet facts schema/revision")
    _date(document["generated_at"], timestamp=True)
    if not isinstance(document["integrity"], str) or not SHA256.fullmatch(document["integrity"]):
        raise ValueError("invalid fleet facts integrity")
    unsigned = {k: v for k, v in document.items() if k != "integrity"}
    try:
        encoded = canonical(document).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("invalid fleet facts encoding") from None
    if len(encoded) > MAX_BYTES or document["integrity"] != _hash(unsigned):
        raise ValueError("fleet facts integrity/size mismatch")
    limits = {"sources": (1, 256), "subjects": (1, 128), "facts": (0, 1024),
              "relations": (0, 1024), "completeness": (0, 256)}
    for field, (minimum, maximum) in limits.items():
        if not isinstance(document[field], list) or not minimum <= len(document[field]) <= maximum:
            raise ValueError("invalid fleet facts collection")
    sources, subjects = {}, {}
    for source in document["sources"]:
        _fields(source, {"id", "kind", "status", "recorded_at", "checked_at", "sha256"})
        identity = _identifier(source["id"])
        if (identity in sources or not _enum(source["kind"], KINDS) or not _enum(source["status"], SOURCE_STATUS)
                or not isinstance(source["sha256"], str) or not SHA256.fullmatch(source["sha256"])
                or (source["kind"] == "synthetic" and not document["fixture_only"])):
            raise ValueError("invalid fleet facts source manifest")
        _date(source["recorded_at"])
        _date(source["checked_at"], timestamp=True)
        sources[identity] = source
    for subject in document["subjects"]:
        _fields(subject, {"id", "kind", "label", "source_ids", "evidence_at"})
        identity = _identifier(subject["id"])
        if identity in subjects or not _enum(subject["kind"], {"host", "project"}):
            raise ValueError("invalid fleet facts subject")
        _text(subject["label"], 120)
        _provenance(subject, sources)
        subjects[identity] = subject
    all_claims = {}
    groups = []
    for field, identity_field in (("facts", "fact_id"), ("relations", "relation_id"), ("completeness", "claim_id")):
        group = {}
        for claim in document[field]:
            common = {identity_field, "status", "evidence_at", "source_ids", "supersedes", "conflicts"}
            required = common | ({"scope", "coverage"} if field == "completeness" else {"subject_id", "predicate", "value" if field == "facts" else "object_id"})
            _fields(claim, required)
            identity = _identifier(claim[identity_field])
            if identity in all_claims:
                raise ValueError("duplicate fleet facts claim")
            _provenance(claim, sources)
            if field == "completeness":
                _fields(claim["scope"], {"subject_id", "predicate"})
                scope = claim["scope"]
                if (not _enum(scope["predicate"], COMPLETENESS) or (scope["subject_id"] is not None and (not isinstance(scope["subject_id"], str) or scope["subject_id"] not in subjects))
                        or (scope["predicate"] in {"hosts", "projects"}) != (scope["subject_id"] is None)
                        or not _enum(claim["status"], {"complete", "partial", "unknown", "conflict"})):
                    raise ValueError("invalid fleet completeness scope")
                if scope["subject_id"] is not None:
                    kind = subjects[scope["subject_id"]]["kind"]
                    if (scope["predicate"] == "hosted_projects" and kind != "host") or (scope["predicate"] == "project_hosts" and kind != "project"):
                        raise ValueError("invalid fleet completeness subject")
                _ids(claim["coverage"], subjects, maximum=128, empty=True)
                coverage_kind = {"hosts": "host", "projects": "project", "hosted_projects": "project", "project_hosts": "host"}.get(scope["predicate"])
                if coverage_kind is not None and any(subjects[identity]["kind"] != coverage_kind for identity in claim["coverage"]):
                    raise ValueError("invalid fleet completeness coverage kind")
                if claim["status"] == "complete" and claim["evidence_at"] is None:
                    raise ValueError("undated completeness cannot authorize all/only")
                key = (field, canonical(scope))
            else:
                if not isinstance(claim["subject_id"], str) or claim["subject_id"] not in subjects or not _enum(claim["status"], CLAIM_STATUS):
                    raise ValueError("invalid fleet facts claim subject/status")
                predicate = claim["predicate"]
                kind = subjects[claim["subject_id"]]["kind"]
                if field == "facts":
                    if not _enum(predicate, PREDICATES):
                        raise ValueError("unknown descriptive predicate")
                    if claim["status"] == "unknown":
                        if claim["value"] is not None:
                            raise ValueError("unknown fleet fact has a value")
                    else:
                        _value(predicate, claim["value"], kind, document["fixture_only"])
                    if predicate in {"purpose", "repository"} and kind != "project":
                        raise ValueError("project fact on nonproject")
                    if predicate in {"public_ipv4", "outbound_ipv4", "public_ipv6", "outbound_ipv6", "provider", "region"} and kind != "host":
                        raise ValueError("host fact on nonhost")
                else:
                    if not _enum(predicate, RELATIONS):
                        raise ValueError("unknown fleet relation")
                    target = claim["object_id"]
                    if claim["status"] == "unknown":
                        if target is not None:
                            raise ValueError("unknown fleet relation has a target")
                    elif not isinstance(target, str) or target not in subjects or target == claim["subject_id"]:
                        raise ValueError("invalid fleet relation target")
                    if predicate == "hosted_on" and (kind != "project" or (target is not None and subjects[target]["kind"] != "host")):
                        raise ValueError("invalid project placement")
                key = (field, claim["subject_id"], predicate)
            group[identity] = (claim, key)
            all_claims[identity] = claim
        groups.append(group)
    for group in groups:
        for identity, (claim, key) in group.items():
            for edge in ("supersedes", "conflicts"):
                _ids(claim[edge], group, empty=True)
                for target in claim[edge]:
                    other, other_key = group[target]
                    if target == identity or key != other_key:
                        raise ValueError("cross-property fleet history link")
                    if edge == "supersedes":
                        newer, older = _moment(claim["evidence_at"]), _moment(other["evidence_at"])
                        if newer is None or (older is not None and newer <= older) or _authority(claim, sources) < _authority(other, sources):
                            raise ValueError("fleet supersession lacks newer authoritative evidence")
                    if edge == "conflicts" and identity not in other["conflicts"]:
                        raise ValueError("asymmetric fleet conflict")
            if set(claim["supersedes"]) & set(claim["conflicts"]):
                raise ValueError("conflicting fleet history link")
        visiting, visited = set(), set()
        def visit(identity):
            if len(visiting) >= 128:
                raise ValueError("fleet supersession history too deep")
            if identity in visiting:
                raise ValueError("cyclic fleet supersession")
            if identity in visited:
                return
            visiting.add(identity)
            for target in group[identity][0]["supersedes"]:
                visit(target)
            visiting.remove(identity)
            visited.add(identity)
        for identity in group:
            visit(identity)
    return copy.deepcopy(document)


def _active(claims):
    superseded = {identity for claim in claims for identity in claim["supersedes"]}
    return [claim for claim in claims if (claim.get("fact_id") or claim.get("relation_id") or claim.get("claim_id")) not in superseded]


def lookup(document, subject_id, predicate):
    """Return all current support; never choose a fact by date or list order."""
    claims = _active([claim for claim in document["facts"] if claim["subject_id"] == subject_id and claim["predicate"] == predicate])
    values = {canonical(claim["value"]) for claim in claims}
    status = ("unknown" if not claims or all(c["status"] == "unknown" for c in claims) else
              "conflict" if len(values) != 1 or any(c["status"] == "conflict" or c["conflicts"] for c in claims) else "recorded")
    source_map = {source["id"]: source for source in document["sources"]}
    if status == "recorded" and not any(source_map[source]["status"] != "unverified" for claim in claims for source in claim["source_ids"]):
        status = "unknown"
    return {"status": status, "value": copy.deepcopy(claims[0]["value"]) if status == "recorded" else None,
            "claims": copy.deepcopy(claims)}


def subjects(document, kind=None, include_inactive=False):
    result = []
    source_map = {source["id"]: source for source in document["sources"]}
    for subject in document["subjects"]:
        if kind is not None and subject["kind"] != kind:
            continue
        lifecycle = lookup(document, subject["id"], "lifecycle")
        if not include_inactive and (lifecycle["status"] != "recorded" or lifecycle["value"] not in {"active", "deployed", "development"} or not all(_authority(claim, source_map) > 0 for claim in lifecycle["claims"])):
            continue
        result.append(copy.deepcopy(subject))
    return result


def relations(document, subject_id=None, predicate=None, object_id=None):
    claims = _active([claim for claim in document["relations"]
                      if (subject_id is None or claim["subject_id"] == subject_id)
                      and (predicate is None or claim["predicate"] == predicate)])
    # Conflict members and explicit unknowns are retained for the caller to explain.
    if object_id is not None:
        claims = [claim for claim in claims if claim["object_id"] == object_id]
    return copy.deepcopy(claims)


def completeness(document, subject_id=None, predicate="hosts"):
    claims = _active([claim for claim in document["completeness"] if claim["scope"] == {"subject_id": subject_id, "predicate": predicate}])
    states = {claim["status"] for claim in claims}
    status = ("unknown" if not claims else "conflict" if len(states) != 1 or any(c["conflicts"] for c in claims) else claims[0]["status"])
    coverage = {canonical(claim["coverage"]) for claim in claims}
    if status == "complete":
        source_map = {source["id"]: source for source in document["sources"]}
        uncertain = any(_authority(claim, source_map) == 0 for claim in claims)
        if predicate in {"hosts", "projects"}:
            expected = {subject["id"] for subject in subjects(document, "host" if predicate == "hosts" else "project")}
        else:
            selected = (relations(document, predicate="hosted_on", object_id=subject_id) if predicate == "hosted_projects" else
                        relations(document, subject_id, "hosted_on" if predicate == "project_hosts" else "depends_on"))
            uncertain = uncertain or any(claim["status"] != "recorded" or claim["conflicts"] for claim in selected)
            if predicate == "hosted_projects":
                uncertain = uncertain or any(claim["object_id"] is None for claim in relations(document, predicate="hosted_on"))
            expected = {claim["subject_id"] if predicate == "hosted_projects" else claim["object_id"] for claim in selected if claim["object_id"] is not None}
        if len(coverage) != 1 or set(claims[0]["coverage"]) != expected or uncertain:
            status = "conflict"
    return {"status": status, "coverage": copy.deepcopy(claims[0]["coverage"]) if status == "complete" else [],
            "claims": copy.deepcopy(claims)}


def _no_symlinks(path):
    candidate = Path(path).absolute()
    if any(part.is_symlink() for part in (candidate, *candidate.parents)):
        raise ValueError("fleet facts symlink refused")


def _read(path):
    path = Path(path)
    _no_symlinks(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode) or os.fstat(descriptor).st_size > MAX_BYTES:
            raise ValueError("invalid fleet facts file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("fleet facts file too large")
        def pairs(items):
            out = {}
            for key, value in items:
                if key in out:
                    raise ValueError("duplicate fleet facts JSON key")
                out[key] = value
            return out
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs,
                          parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite fleet facts JSON")))
    finally:
        os.close(descriptor)


def _publication(path):
    value = _read(path)
    if isinstance(value, dict) and value.get("schema") == PUBLICATION_SCHEMA:
        _fields(value, {"schema", "artifact", "receipt"})
        if not isinstance(value["artifact"], str) or not re.fullmatch(r"[0-9]{16}-[a-f0-9]{64}\.json", value["artifact"]):
            raise ValueError("invalid fleet facts publication pointer")
        receipt = value["receipt"]
        _fields(receipt, {"revision", "integrity", "generated_at", "installed_at", "previous_revision"})
        if type(receipt["revision"]) is not int or type(receipt["previous_revision"]) is not int or not 0 <= receipt["previous_revision"] < receipt["revision"]:
            raise ValueError("invalid fleet facts installation receipt")
        _date(receipt["installed_at"], timestamp=True)
        document = _read(Path(path).parent / "revisions" / value["artifact"])
        if not isinstance(document, dict):
            raise ValueError("invalid fleet facts pointed artifact")
        for key in ("revision", "integrity", "generated_at"):
            if document.get(key) != receipt[key]:
                raise ValueError("fleet facts receipt mismatch")
        if value["artifact"] != f'{document["revision"]:016d}-{document["integrity"]}.json':
            raise ValueError("fleet facts revision pointer mismatch")
        return document, value
    return value, None


def load(path, allow_fixture=False):
    return validate(_publication(path)[0], allow_fixture=allow_fixture)


def publication_status(path, allow_fixture=False):
    document, pointer = _publication(path)
    validate(document, allow_fixture=allow_fixture)
    return {"revision": document["revision"], "integrity": document["integrity"],
            "generated_at": document["generated_at"],
            "installed_at": pointer["receipt"]["installed_at"] if pointer else None,
            "source_checks": {source["id"]: source["checked_at"] for source in document["sources"]}}


def _forward(previous, candidate):
    if candidate["revision"] <= previous["revision"]:
        raise ValueError("fleet facts revision rollback/conflict")
    for field, identity in (("facts", "fact_id"), ("relations", "relation_id"), ("completeness", "claim_id"), ("sources", "id")):
        current = {claim[identity]: claim for claim in candidate[field]}
        if any(current.get(claim[identity]) != claim for claim in previous[field]):
            raise ValueError("fleet facts history/provenance must be retained")
    current_subjects = {subject["id"]: subject for subject in candidate["subjects"]}
    if any(subject["id"] not in current_subjects or current_subjects[subject["id"]]["kind"] != subject["kind"] for subject in previous["subjects"]):
        raise ValueError("fleet facts subject/tombstone must be retained")
    source_map = {source["id"]: source for source in candidate["sources"]}
    lifecycle_claims = {claim["fact_id"]: claim for claim in candidate["facts"] if claim["predicate"] == "lifecycle"}
    def ancestors(claim):
        seen, pending = set(), list(claim["supersedes"])
        while pending:
            identity = pending.pop()
            if identity not in seen:
                seen.add(identity)
                pending.extend(lifecycle_claims[identity]["supersedes"])
        return seen
    for subject in previous["subjects"]:
        tombstones = {claim["fact_id"] for claim in previous["facts"] if claim["subject_id"] == subject["id"]
                      and claim["predicate"] == "lifecycle" and claim["status"] != "unknown"
                      and claim["value"] in {"retired", "excluded", "unverified", "testing"}}
        new = lookup(candidate, subject["id"], "lifecycle")
        if tombstones and new["status"] == "recorded" and new["value"] in {"active", "deployed", "development"}:
            if not all(tombstones <= ancestors(claim) and any(source_map[source]["kind"] == "owner-decision" and source_map[source]["status"] == "accepted" for source in claim["source_ids"]) for claim in new["claims"]):
                raise ValueError("fleet lifecycle reactivation requires explicit owner evidence")


def _directory(path):
    path = Path(path)
    _no_symlinks(path)
    path.mkdir(mode=0o750, parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError("invalid fleet facts directory")


def _write_new(path, data):
    if path.exists() or path.is_symlink():
        if path.is_symlink() or canonical(_read(path)).encode("utf-8") != data:
            raise ValueError("fleet facts archive collision")
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _sync_directory(path):
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def publish(document, directory, authenticated=False, allow_fixture=False):
    """Forward-only atomic local installation after authenticated operator delivery.

    Current is one atomically replaced pointer containing its installation receipt.
    New immutable artifacts are durable before that switch; a failed preparation
    leaves current unchanged. Recovery publishes a new revision, never an old one.
    This function creates no connection, executable target, task or schedule.
    """
    if authenticated is not True:
        raise ValueError("authenticated operator configuration channel required")
    candidate = validate(document, allow_fixture=allow_fixture)
    directory = Path(directory)
    _directory(directory)
    _directory(directory / "revisions")
    _directory(directory / "receipts")
    lock = directory / ".publish-lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError("fleet facts publication already in progress") from None
    try:
        current = directory / "current.json"
        previous, prior_pointer = (None, None)
        if current.exists() or current.is_symlink():
            previous, prior_pointer = _publication(current)
            validate(previous, allow_fixture=allow_fixture)
            if prior_pointer is None:
                raise ValueError("fleet facts publisher requires a managed current pointer")
            if previous["revision"] == candidate["revision"] and previous == candidate:
                return copy.deepcopy(prior_pointer["receipt"])
            _forward(previous, candidate)
        filename = f'{candidate["revision"]:016d}-{candidate["integrity"]}.json'
        _write_new(directory / "revisions" / filename, canonical(candidate).encode("utf-8"))
        _sync_directory(directory / "revisions")
        if prior_pointer is not None:
            _write_new(directory / "receipts" / prior_pointer["artifact"], canonical(prior_pointer).encode("utf-8"))
            _sync_directory(directory / "receipts")
        receipt = {"revision": candidate["revision"], "integrity": candidate["integrity"],
                   "generated_at": candidate["generated_at"], "installed_at": datetime.now(timezone.utc).isoformat(),
                   "previous_revision": previous["revision"] if previous else 0}
        pointer = {"schema": PUBLICATION_SCHEMA, "artifact": filename, "receipt": receipt}
        pending = directory / (".pending-" + uuid.uuid4().hex + ".json")
        _write_new(pending, canonical(pointer).encode("utf-8"))
        os.replace(pending, current)
        _sync_directory(directory)
        return copy.deepcopy(receipt)
    finally:
        lock.rmdir()





