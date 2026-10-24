"""Source-bound local fleet facts for the shared durable task broker.

No executor, MCP transport, network, model, credentials or effect path lives here.
The task's existing owner/source and receipt are the authority/durability boundary.
"""
import copy
import hashlib
import json
import re

import common
import fleet_facts as facts
import fleet_queries as queries
import store

SCHEMA = "fleet.lookup/v1"
VERSION = "cara-fleet-task-lookup/v1"
INPUTS = {"query", "fields", "subjects", "scope", "presentation", "detail", "lang"}
LOOKUP_KEYS = {"version", "revision", "facts_integrity", "authority", "plan", "cells", "sources", "pages", "coverage"}
AUTHORITY_KEYS = {"task_id", "step_id", "chat_id", "source_update", "source_hash", "input_hash", "subjects"}
CELL_KEYS = {"subject_id", "label", "field", "status", "value", "evidence_dates", "claim_ids", "source_ids", "completeness"}


def digest(value):
    return hashlib.sha256(facts.canonical(value).encode("utf-8")).hexdigest()


def _text(value, maximum, name):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("fleet.lookup: invalid " + name)
    return value.strip()


def _choices(value, available, name, empty=False, maximum=12):
    if (not isinstance(value, list) or len(value) > maximum or (not empty and not value)
            or any(not isinstance(item, str) or item not in available for item in value)
            or len(set(value)) != len(value)):
        raise ValueError("fleet.lookup: invalid " + name)
    return list(value)


def validate_input(value):
    if not isinstance(value, dict) or not {"query", "fields"} <= set(value) or set(value) - INPUTS:
        raise ValueError("fleet.lookup: invalid input fields")
    subjects = value.get("subjects", [])
    if (not isinstance(subjects, list) or len(subjects) > 8
            or any(not isinstance(item, str) or not item.strip() or len(item) > 120 for item in subjects)
            or len(set(subjects)) != len(subjects)):
        raise ValueError("fleet.lookup: invalid subjects")
    result = {"query": _text(value["query"], 16000, "query"),
              "fields": _choices(value["fields"], queries.FIELDS, "fields"),
              "subjects": list(subjects), "scope": value.get("scope", "explicit"),
              "presentation": value.get("presentation", "list"),
              "detail": value.get("detail", "concise"), "lang": value.get("lang", common.detect_lang(value["query"]) or "en")}
    for key, allowed in (("scope", {"explicit", "all_hosts", "all_projects"}),
                         ("presentation", queries.PRESENTATIONS),
                         ("detail", {"concise", "detailed"}), ("lang", {"ru", "en"})):
        if not isinstance(result[key], str) or result[key] not in allowed:
            raise ValueError("fleet.lookup: invalid " + key)
    return result


def _terms(document, subject):
    aliases = facts.lookup(document, subject["id"], "aliases")["value"] or []
    return [subject["id"], subject["label"], *aliases]


def _named(text, term):
    return bool(re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)", text.casefold()))


def selected(document, inputs):
    """Model selections cannot expand identities authorized by the bound source."""
    query, scope = inputs["query"], inputs["scope"]
    subjects = document["subjects"]
    eligible = [subject["id"] for subject in subjects if any(_named(query, term) for term in _terms(document, subject))]
    if scope != "explicit":
        kind = "host" if scope == "all_hosts" else "project"
        words = r"(?:servers?|hosts?|machines?|vps)" if kind == "host" else r"projects?"
        ru = r"(?:\u0441\u0435\u0440\u0432\u0435\u0440\w*|\u043c\u0430\u0448\u0438\u043d\w*|vps)" if kind == "host" else r"\u043f\u0440\u043e\u0435\u043a\u0442\w*"
        pattern = (r"\b(?:all|my|our)\s+(?:(?:my|our|known|current)\s+)?" + words + r"\b|"
                   r"(?:(?:\u0432\u0441\u0435|\u0432\u0441\u0435\u0445|\u043c\u043e\u0438|\u043c\u043e\u0438\u0445|\u043d\u0430\u0448\u0438|\u043d\u0430\u0448\u0438\u0445)\s+){1,2}" + ru)
        if not re.search(pattern, query, re.I):
            raise ValueError("fleet.lookup: all scope is not authorized by the owner source")
        eligible = [subject["id"] for subject in facts.subjects(document, kind)]
    selectors = inputs["subjects"]
    chosen = []
    for selector in selectors:
        matches = [subject["id"] for subject in subjects
                   if any(selector.casefold() == term.casefold() for term in _terms(document, subject))]
        if len(matches) != 1 or matches[0] not in eligible or matches[0] in chosen:
            raise ValueError("fleet.lookup: unknown, ambiguous or source-unapproved subject")
        chosen.append(matches[0])
    if not selectors:
        if scope == "explicit":
            matched_terms = {}
            for subject in subjects:
                for term in _terms(document, subject):
                    if _named(query, term):
                        matched_terms.setdefault(term.casefold(), set()).add(subject["id"])
            if any(len(identities) > 1 for identities in matched_terms.values()):
                raise ValueError("fleet.lookup: ambiguous owner alias; name the exact host or project")
        chosen = eligible
    if not chosen:
        raise ValueError("fleet.lookup: name a catalog host/project or explicitly request all hosts/projects")
    if scope != "explicit" and chosen != eligible:
        raise ValueError("fleet.lookup: all scope must preserve the complete authorized class")
    if len(chosen) > 8 or len(chosen) * len(inputs["fields"]) > 32:
        raise ValueError("fleet.lookup: request exceeds the bounded read; split the requested facts")
    return chosen


def authority(cfg, conn, task, step, inputs):
    import tasking
    allowed = getattr(cfg, "allowed_chat_ids", ())
    if (type(task["chat_id"]) is not int or task["chat_id"] not in allowed
            or step["task_id"] != task["id"] or step["tool"] != "fleet.lookup"):
        raise ValueError("fleet.lookup: owner principal or task binding is invalid")
    row = store.assistant_task_source(conn, task["chat_id"], task["source_update"])
    if row is None or tasking.source_hash(row["text"]) != task["source_hash"]:
        raise ValueError("fleet.lookup: canonical owner source is missing or changed")
    bindings = json.loads(step["bindings_json"] or "{}")
    binding = bindings.get("query")
    if (not isinstance(binding, dict) or binding.get("source") != "boss_span"
            or binding.get("transform") != "literal" or binding.get("source_hash") != task["source_hash"]):
        raise ValueError("fleet.lookup: query must come directly from the canonical owner source")
    start, end = binding.get("start"), binding.get("end")
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(row["text"])
            or row["text"][start:end].strip() != inputs["query"]):
        raise ValueError("fleet.lookup: owner source span does not match the query")
    return {"task_id": task["id"], "step_id": step["id"], "chat_id": task["chat_id"],
            "source_update": task["source_update"], "source_hash": task["source_hash"],
            "input_hash": digest(inputs), "subjects": []}


def build(document, inputs, binding):
    document = facts.validate(document, allow_fixture=True)
    identities = selected(document, inputs)
    plan = queries.validate_plan({"kind": "catalog", "subjects": identities, "scope": "explicit",
        "fields": inputs["fields"], "presentation": inputs["presentation"],
        "detail": inputs["detail"], "clarification": ""}, document)
    raw_cells = queries.cells(document, plan)
    cells, used = [], set()
    for cell in raw_cells:
        claims = list(cell["claims"])
        if "completeness" in cell:
            claims += facts.completeness(document, cell["subject_id"], cell["field"])["claims"]
        source_ids = list(dict.fromkeys(source for claim in claims for source in claim["source_ids"]))
        used.update(source_ids)
        cells.append({key: copy.deepcopy(cell[key]) for key in
                      ("subject_id", "label", "field", "status", "value", "evidence_dates")} | {
            "claim_ids": [claim.get("fact_id") or claim.get("relation_id") or claim.get("claim_id") or claim.get("id") for claim in claims],
            "source_ids": source_ids, "completeness": cell.get("completeness")})
    pages = queries.render(document, plan, inputs["lang"])
    coverage = None
    if inputs["scope"] != "explicit":
        kind = "hosts" if inputs["scope"] == "all_hosts" else "projects"
        completeness = facts.completeness(document, predicate=kind)
        coverage = {"kind": kind, "status": completeness["status"],
                    "claim_ids": [claim["claim_id"] for claim in completeness["claims"]],
                    "source_ids": list(dict.fromkeys(source for claim in completeness["claims"] for source in claim["source_ids"])),
                    "evidence_dates": list(dict.fromkeys(claim["evidence_at"] for claim in completeness["claims"]))}
        used.update(source for claim in completeness["claims"] for source in claim["source_ids"])
        if completeness["status"] != "complete":
            text = ("\u041f\u043e\u043b\u043d\u043e\u0442\u0430 \u043a\u0430\u0442\u0430\u043b\u043e\u0433\u0430: " if inputs["lang"] == "ru" else "Catalog completeness: ") + completeness["status"]
            pages.append(text)
    # Shared task answers currently have a 3500-character delivery contract.
    # Reject oversized obligations instead of reporting a silently truncated success.
    if sum(len(page) for page in pages) + max(0, len(pages) - 1) > 3000:
        raise ValueError("fleet.lookup: answer exceeds the bounded task reply; request fewer fields or subjects")
    binding = copy.deepcopy(binding)
    binding["subjects"] = identities
    return {"version": VERSION, "revision": document["revision"], "facts_integrity": document["integrity"],
            "authority": binding, "plan": plan, "cells": cells,
            "sources": [copy.deepcopy(source) for source in document["sources"] if source["id"] in used],
            "pages": pages, "coverage": coverage}


def evidence(lookup):
    integrity = lookup["facts_integrity"]
    return [{"id": "fleet:" + integrity[:16] + ":" + cell["subject_id"] + ":" + cell["field"],
             "source": "fleet-facts:" + integrity + ":" + cell["subject_id"] + ":" + cell["field"],
             "label": cell["label"] + " / " + cell["field"] + " / " + cell["status"],
             "trust": "confirmed_local"} for cell in lookup["cells"]]


def validate_shape(value):
    if (not isinstance(value, dict) or set(value) != LOOKUP_KEYS or value["version"] != VERSION
            or type(value["revision"]) is not int or value["revision"] < 1
            or not isinstance(value["facts_integrity"], str) or not facts.SHA256.fullmatch(value["facts_integrity"])):
        raise ValueError("fleet.lookup: malformed output envelope")
    binding = value["authority"]
    if (not isinstance(binding, dict) or set(binding) != AUTHORITY_KEYS
            or any(type(binding[key]) is not int for key in ("task_id", "step_id", "chat_id", "source_update"))
            or any(not isinstance(binding[key], str) or not facts.SHA256.fullmatch(binding[key]) for key in ("source_hash", "input_hash"))):
        raise ValueError("fleet.lookup: malformed owner/source binding")
    plan = value["plan"]
    if (not isinstance(plan, dict) or set(plan) != queries.RAW_KEYS | {"facts_integrity"}
            or plan["kind"] != "catalog" or plan["scope"] != "explicit" or plan["clarification"] != ""
            or plan["facts_integrity"] != value["facts_integrity"]):
        raise ValueError("fleet.lookup: malformed facts obligations")
    _choices(plan["fields"], queries.FIELDS, "output fields")
    if (not isinstance(plan["presentation"], str) or plan["presentation"] not in queries.PRESENTATIONS
            or not isinstance(plan["detail"], str) or plan["detail"] not in {"concise", "detailed"}):
        raise ValueError("fleet.lookup: malformed presentation obligations")
    ids = binding["subjects"]
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 8
            or any(not isinstance(identity, str) or not facts.ID.fullmatch(identity) for identity in ids)
            or len(set(ids)) != len(ids) or plan["subjects"] != ids):
        raise ValueError("fleet.lookup: malformed subject binding")
    cells = value["cells"]
    expected = [(identity, field) for identity in ids for field in plan["fields"]]
    if (not isinstance(cells, list) or len(cells) != len(expected) or len(cells) > 32
            or any(not isinstance(cell, dict) or set(cell) != CELL_KEYS for cell in cells)
            or [(cell["subject_id"], cell["field"]) for cell in cells] != expected):
        raise ValueError("fleet.lookup: incomplete output coverage")
    for cell in cells:
        if (not isinstance(cell["label"], str) or not 1 <= len(cell["label"]) <= 120
                or (cell["completeness"] is not None and cell["completeness"] not in {"complete", "partial", "unknown", "conflict"})):
            raise ValueError("fleet.lookup: malformed cell label/completeness")
        if cell["field"] in {"hosted_projects", "project_hosts", "dependencies"}:
            if (not isinstance(cell["value"], list) or any(not isinstance(item, dict) or set(item) != {"id", "label"}
                    or not isinstance(item["id"], str) or not facts.ID.fullmatch(item["id"])
                    or not isinstance(item["label"], str) or not 1 <= len(item["label"]) <= 120 for item in cell["value"])):
                raise ValueError("fleet.lookup: malformed relationship values")
        elif cell["value"] is not None and not isinstance(cell["value"], str):
            raise ValueError("fleet.lookup: malformed scalar fact value")
        if not isinstance(cell["status"], str) or cell["status"] not in {"recorded", "unknown", "conflict", "not_applicable"}:
            raise ValueError("fleet.lookup: malformed cell status")
        for key in ("source_ids", "claim_ids", "evidence_dates"):
            if not isinstance(cell[key], list) or any(not isinstance(item, str) and not (key == "evidence_dates" and item is None) for item in cell[key]):
                raise ValueError("fleet.lookup: malformed cell provenance")
    pages = value["pages"]
    if (not isinstance(pages, list) or not pages or any(not isinstance(page, str) or not page for page in pages)
            or len("\n".join(pages)) > 3000 or not isinstance(value["sources"], list)):
        raise ValueError("fleet.lookup: malformed rendered facts")
    coverage = value["coverage"]
    if coverage is not None:
        if (not isinstance(coverage, dict) or set(coverage) != {"kind", "status", "claim_ids", "source_ids", "evidence_dates"}
                or not isinstance(coverage["kind"], str) or coverage["kind"] not in {"hosts", "projects"}
                or not isinstance(coverage["status"], str) or coverage["status"] not in {"complete", "partial", "unknown", "conflict"}
                or any(not isinstance(coverage[key], list) for key in ("claim_ids", "source_ids", "evidence_dates"))):
            raise ValueError("fleet.lookup: malformed catalog coverage")
    for source in value["sources"]:
        if (not isinstance(source, dict) or set(source) != {"id", "kind", "status", "recorded_at", "checked_at", "sha256"}
                or not isinstance(source["id"], str) or not facts.ID.fullmatch(source["id"])
                or not isinstance(source["kind"], str) or source["kind"] not in facts.KINDS
                or not isinstance(source["status"], str) or source["status"] not in facts.SOURCE_STATUS
                or not isinstance(source["sha256"], str) or not facts.SHA256.fullmatch(source["sha256"])):
            raise ValueError("fleet.lookup: malformed source manifest")
        facts._date(source["recorded_at"])
        facts._date(source["checked_at"], timestamp=True)
    return value


def validate_current(value, document, inputs, binding, returned_evidence):
    validate_shape(value)
    expected = build(document, inputs, binding)
    if value != expected or returned_evidence != evidence(expected):
        raise ValueError("fleet.lookup: cells/provenance do not match the current published facts")
    return value


def safe_receipt(data, support):
    """Preserve only validated typed hashes; reject secret-shaped free text.

    Legacy redaction stays unchanged. The source/evidence hashes here are known
    code-owned metadata, checked against actual facts/source before persistence;
    arbitrary strings named sha256 or hidden in values receive no exemption.
    """
    import tasking
    if (not isinstance(data, dict) or set(data) != {"schema", "value"} or data["schema"] != SCHEMA
            or not isinstance(data["value"], dict) or set(data["value"]) != {"lookup"}):
        raise ValueError("fleet.lookup: malformed safe receipt")
    value = validate_shape(data["value"]["lookup"])
    if support != evidence(value):
        raise ValueError("fleet.lookup: evidence does not match typed fact cells")
    base = ("value", "lookup")
    hashes = {base + ("facts_integrity",), base + ("plan", "facts_integrity"),
              base + ("authority", "source_hash"), base + ("authority", "input_hash")}
    hashes.update(base + ("sources", index, "sha256") for index in range(len(value["sources"])))
    def checked(item, path=()):
        if path in hashes:
            if not isinstance(item, str) or not facts.SHA256.fullmatch(item):
                raise ValueError("fleet.lookup: invalid typed receipt hash")
            return item
        if isinstance(item, str):
            if tasking.redact_derived_text(item) != item:
                raise ValueError("fleet.lookup: secret-shaped free text is not allowed in a receipt")
            return item
        if isinstance(item, dict):
            return {key: checked(child, path + (key,)) for key, child in item.items()}
        if isinstance(item, list):
            return [checked(child, path + (index,)) for index, child in enumerate(item)]
        return item
    safe_data = checked(data)
    safe_support = []
    for item in support:
        # evidence(value) fixes the entire source, including its typed integrity
        # hash. Never accept a handler/model supplied source or arbitrary hash.
        safe_support.append({key: child if key == "source" else checked(child)
                             for key, child in item.items()})
    return safe_data, safe_support


def validate_result(cfg, conn, task, step, inputs, status, summary, data, support, artifact_id, effect_id):
    """Independent invocation boundary before the existing receipt transaction."""
    if (status != "ok" or artifact_id is not None or effect_id is not None
            or not isinstance(data, dict) or set(data) != {"schema", "value"}
            or data["schema"] != SCHEMA or not isinstance(data["value"], dict)
            or set(data["value"]) != {"lookup"}):
        raise ValueError("fleet.lookup: invalid read-only receipt")
    inputs = validate_input(inputs)
    value = data["value"]["lookup"]
    validate_current(value, queries.load(cfg), inputs, authority(cfg, conn, task, step, inputs), support)
    if summary != "\n".join(value["pages"]):
        raise ValueError("fleet.lookup: summary does not match code-owned facts")
    return data


def execute(cfg, conn, task, step, inputs):
    inputs = validate_input(inputs)
    binding = authority(cfg, conn, task, step, inputs)
    document = queries.load(cfg)
    value = build(document, inputs, binding)
    support = evidence(value)
    # Re-open the real installed snapshot; a swapped revision is never accepted
    # merely because model or handler output repeated an old integrity string.
    validate_current(value, queries.load(cfg), inputs, authority(cfg, conn, task, step, inputs), support)
    summary = "\n".join(value["pages"])
    return "ok", summary, {"schema": SCHEMA, "value": {"lookup": value}}, support, None, None
