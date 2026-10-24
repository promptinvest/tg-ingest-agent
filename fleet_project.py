"""CF-05 semantic project-read dispatch over the existing bounded executor.

Project IDs/capabilities are selected by the existing planner; only root-owned
host mappings choose resources. Legacy host diagnostics and exact maintenance
cards remain on their existing path. No MCP dependency or new transport exists.
"""
import copy

import fleet_protocol as protocol
import fleet_project_report
import llm

STYLE = {"presentation": frozenset({"prose", "list", "table", "names"}),
         "detail": frozenset({"concise", "detailed"})}


def contract_hash():
    return protocol.digest(protocol.capability_contract())


def focused_capabilities(focus):
    """Closed defaults for old valid project plans; explicit scope may be narrower."""
    selected = []
    mapping = {"release": ("release",), "backups": ("backups",), "progress": ("development",),
               "services": ("runtime",), "errors": ("runtime",),
               "memory": ("runtime", "containers"), "cpu": ("runtime", "containers"),
               "resources": ("runtime", "containers"), "disk": ("runtime", "containers"),
               "trend": ("runtime", "containers")}
    for item in focus:
        selected.extend(mapping.get(item, ()))
    return list(dict.fromkeys(selected or ["release", "http_health", "containers", "runtime"]))


def _inherited_scope(plan, previous, explicit_focus):
    if plan.get("scope") != "inherit" or not previous or "read_scope" not in previous:
        return None
    if not protocol.read_contract_compatible(previous.get("read_contract_hash")):
        raise ValueError("project read capability contract changed")
    scope = protocol.read_scope(previous["read_scope"])
    if scope["kind"] == "projects" and set(scope["projects"]) != set(plan.get("projects", [])):
        return None
    # A new concrete focus changes requested observations; a generic overview or
    # formatting correction retains the original capability obligations.
    if explicit_focus and set(explicit_focus) != {"overview"} and set(explicit_focus) != set(previous.get("focus", [])):
        return None
    return copy.deepcopy(scope)


def _inherited_answer_fields(plan, previous, explicit_focus, answer_mode):
    """Only a format-only correction can retain canonical field obligations."""
    if (answer_mode != "reformat" or plan.get("scope") != "inherit" or not previous
            or "answer_fields" not in previous
            or not protocol.read_contract_compatible(previous.get("read_contract_hash"))):
        return None
    if explicit_focus and set(explicit_focus) != {"overview"} and set(explicit_focus) != set(previous.get("focus", [])):
        return None
    previous_scope = protocol.read_scope(previous.get("read_scope"))
    if (plan.get("read_scope") != previous_scope
            or set(plan["hosts"]) != set(previous.get("hosts", []))
            or set(plan.get("projects", [])) != set(previous.get("projects", []))):
        return None
    return fleet_project_report.validate_answer_fields(previous["answer_fields"])


class ProjectReadMixin:
    def _fleet_planning_messages(self, question, inventory, previous, lang, error=""):
        messages = super()._fleet_planning_messages(question, inventory, previous, lang, error)
        messages[0]["content"] += (
            "\nFor a runtime request about selected project(s), include their canonical projects IDs "
            "and facets:[projects]. A self-contained named-project request uses scope:explicit, "
            "including requests for the latest recorded/dated application observation. "
            "Evidence recency never supplies conversational prior scope. "
            "Optional read_scope is {version:'" + protocol.READ_VERSION + "',kind:'host'|'projects'|'all_projects',"
            "projects:[canonical project IDs],capabilities:[" + ",".join(protocol.PROJECT_CAPABILITIES) + "]}. "
            "A projects read collects only those projects, over their configured hosts; never collect unrelated "
            "host resources/services/errors or colocated projects. all_projects requires an explicit all-project request. "
            "A host scope has projects:[] and capabilities:[]. No paths, URLs, unit names or commands. "
            "Capabilities are distinct: release is installed release evidence; http_health is application checks; "
            "containers are process evidence; runtime is an installed component/application observation, including "
            "existing generation/reserve/budget or authenticated-route/connection summaries when configured. "
            "backups and development are dated evidence; any capability can remain not configured. "
            "Process activity cannot prove application readiness, client traffic, verified backups or development completion. "
            "Only with a valid nonempty canonical prior investigation, a genuine correction or "
            "format/detail-only follow-up uses answer_mode:reformat with scope:inherit; "
            "reuse prior evidence without another host read, keeping its original timestamp and gaps. "
            "A self-contained named project asking for a concise/table/dated answer still uses "
            "answer_mode:inspect and scope:explicit; these presentation words do not imply reformat. "
            "Without valid prior scope never inherit; clarify a genuinely missing target instead. "
            "Generation/reserves/daily budget requests select runtime, focus:[projects], not host resources. "
            "Optional answer_fields is an ordered unique JSON array of 1..9 of "
            + "|".join(fleet_project_report.ANSWER_FIELD_ORDER) + ". It selects reply content from an already "
            "selected project runtime observation, never capabilities, extra reads or authority. "
            "answer_fields IDs are not focus values: keep the existing focus enum unchanged; runtime is a capability, "
            "not a focus. Use focus:[projects] for a narrow application-state question. "
            "These answer_fields describe application monitor/business data. application_health means application/Postgres "
            "health, never authenticated proxy protocol/route health or connection summaries. For proxy protocol, route "
            "or connection questions ordinarily omit answer_fields and use the existing typed runtime observations; "
            "only an actual request for unsupported application fields receives their specific evidence gaps. "
            "For supported monitor business questions include only the requested answer_fields: for generation, "
            "reserves and daily budget use [generation,reserves,day_budget], retaining their evidence dates and "
            "material gaps. day_budget is the daily limit, never an inferred spend or remaining balance. "
            "Select project_metrics only for an actual project load/traffic/CPU/RAM/disk question; do not add it "
            "to generation/reserves/budget questions. Omit unrequested app health, unfinished work, release and "
            "monitor internals from a narrow reply. A detailed request adds relevant detail to the selected fields; "
            "a genuinely broad runtime overview can omit answer_fields. A format-only correction keeps the same "
            "field obligations; a new question or named target selects its own fields. "
            "Fresh checks use answer_mode:inspect (the default). "
            "Optional presentation is prose|list|table|names and detail is concise|detailed; choose the owner's explicit "
            "format/detail first, then confirmed preference, concise by default. Formatting corrections retain prior "
            "project/capability obligations and need no new evidence. Never alter the exact maintenance confirmation contract."
        )
        return messages

    def _fleet_validate_planning(self, raw, inventory, previous):
        value = copy.deepcopy(raw) if isinstance(raw, dict) else llm.parse_llm_json(raw)
        if not isinstance(value, dict):
            raise ValueError("invalid runtime project plan")
        answer_mode = value.pop("answer_mode", "inspect")
        if answer_mode not in {"inspect", "reformat"}:
            raise ValueError("invalid evidence reuse mode")
        read_scope = value.pop("read_scope", None)
        answer_fields = (fleet_project_report.validate_answer_fields(value.pop("answer_fields"))
                         if "answer_fields" in value else None)
        styles = {}
        for key, allowed in STYLE.items():
            if key in value:
                item = value.pop(key)
                if not isinstance(item, str) or item not in allowed:
                    raise ValueError("invalid runtime answer presentation")
                styles[key] = item
        explicit_focus = value.get("focus", [])
        if isinstance(explicit_focus, str):
            explicit_focus = [explicit_focus]
        if read_scope is not None:
            read_scope = copy.deepcopy(protocol.read_scope(read_scope))
            if value.get("operation") != "inspect":
                raise ValueError("project read scope cannot alter maintenance")
            if read_scope["kind"] == "projects":
                if value.get("projects") and (not isinstance(value["projects"], list) or any(not isinstance(project, str) for project in value["projects"])):
                    raise ValueError("invalid semantic project identifiers")
                if value.get("projects") and set(value["projects"]) != set(read_scope["projects"]):
                    raise ValueError("semantic project and read scopes disagree")
                value["projects"] = list(read_scope["projects"])
            elif read_scope["kind"] == "host" and value.get("projects"):
                raise ValueError("host read scope cannot contain project targets")
        if value.get("projects") and (not isinstance(value["projects"], list) or any(not isinstance(project, str) for project in value["projects"])):
            raise ValueError("invalid semantic project identifiers")
        if read_scope is None and value.get("scope") == "inherit" and previous and previous.get("projects") and not value.get("projects"):
            value["projects"] = list(previous["projects"])
        plan = super()._fleet_validate_planning(protocol.canonical(value), inventory, previous)
        plan.update(styles)
        if answer_mode == "reformat":
            if plan.get("operation") != "inspect" or plan.get("scope") != "inherit":
                raise ValueError("reformat requires the canonical prior read scope")
            plan["answer_mode"] = "reformat"
        if plan.get("operation") != "inspect" or plan.get("clarification"):
            if answer_fields is not None:
                raise ValueError("answer fields require a resolved project runtime read")
            return plan
        if read_scope is None:
            read_scope = _inherited_scope(plan, previous, explicit_focus)
        if read_scope is None and plan.get("projects"):
            read_scope = {"version": protocol.READ_VERSION, "kind": "projects", "projects": list(plan["projects"]),
                          "capabilities": focused_capabilities(plan.get("focus", ["overview"]))}
        if read_scope is None and explicit_focus and set(explicit_focus) <= {
                "memory", "cpu", "disk", "services", "errors", "trend"}:
            # New metric-focused plans need no project observations. The base
            # validator remains compatible with stored legacy diagnostics;
            # only new semantic reads acquire this narrower wire contract.
            read_scope = {"version": protocol.READ_VERSION, "kind": "host",
                          "projects": [], "capabilities": []}
        if read_scope is None:
            if answer_fields is not None:
                raise ValueError("answer fields require a resolved project runtime read")
            return plan  # Preserve supported legacy and explicit broad diagnostics.
        if read_scope["kind"] == "projects":
            # Complete configured coverage of a multi-host project is code-owned.
            if any(project not in inventory["projects"] for project in read_scope["projects"]):
                raise ValueError("unknown semantic project")
            plan["hosts"] = list(dict.fromkeys(host for project in read_scope["projects"] for host in inventory["projects"][project]["hosts"]))
            plan["projects"] = list(read_scope["projects"])
            plan["facets"] = ["projects"]
        elif read_scope["kind"] == "host":
            plan["projects"] = []
            plan["facets"] = [facet for facet in plan["facets"] if facet != "projects"]
            if not plan["facets"]:
                raise ValueError("host infrastructure read has no requested observations")
        else:
            projects = [project for project, record in inventory["projects"].items() if set(record["hosts"]) & set(plan["hosts"])]
            if read_scope["projects"] and set(read_scope["projects"]) != set(projects):
                raise ValueError("explicit all-project scope is incomplete")
            read_scope["projects"] = projects
            plan["projects"] = projects
        protocol.read_scope(read_scope, inventory, plan["hosts"])
        plan["read_scope"] = read_scope
        plan["read_contract_hash"] = contract_hash()
        if answer_fields is None:
            answer_fields = _inherited_answer_fields(plan, previous, explicit_focus, answer_mode)
        if answer_fields is not None:
            if read_scope["kind"] not in {"projects", "all_projects"} or "runtime" not in read_scope["capabilities"]:
                raise ValueError("answer fields require a resolved project runtime read")
            plan["answer_fields"] = answer_fields
        return plan

    def _fleet_context_frame(self, question, plan, inventory, previous=None, update=None):
        frame = super()._fleet_context_frame(question, plan, inventory, previous=previous, update=update)
        for key in ("read_scope", "read_contract_hash", "answer_fields", *STYLE):
            if key in plan:
                frame[key] = copy.deepcopy(plan[key])
        return frame

    def _fleet_inspect_operation(self, plan, inventory):
        if "read_scope" not in plan:
            return super()._fleet_inspect_operation(plan, inventory)
        if not protocol.read_contract_compatible(plan.get("read_contract_hash")):
            raise ValueError("project read capability contract changed before submission")
        scope = protocol.read_scope(plan["read_scope"], inventory, plan["hosts"])
        return protocol.scoped_inspection(inventory, plan["hosts"], scope["projects"], scope["capabilities"],
                                          kind=scope["kind"], facets=plan["facets"])

    def _fleet_additional_operation(self, payload, checks, inventory):
        plan = payload["plan"]
        if "read_scope" not in plan:
            return super()._fleet_additional_operation(payload, checks, inventory)
        if payload["catalog_hash"] != protocol.digest(inventory):
            raise ValueError("inventory changed before additional scoped observation")
        hosts = sorted({check["host"] for check in checks})
        if not hosts or any(host not in plan["hosts"] for host in hosts):
            raise ValueError("additional read cannot expand selected hosts")
        selected = copy.deepcopy(plan)
        selected["hosts"] = hosts
        selected["facets"] = sorted({check["facet"] for check in checks})
        scope = selected["read_scope"]
        if scope["kind"] == "projects":
            if selected["facets"] != ["projects"]:
                raise ValueError("additional project read cannot collect host metadata")
            scope["projects"] = [project for project in scope["projects"] if set(inventory["projects"][project]["hosts"]) & set(hosts)]
        elif scope["kind"] == "all_projects":
            if not set(selected["facets"]) <= set(plan["facets"]):
                raise ValueError("additional all-project read cannot expand evidence obligations")
            scope["projects"] = [project for project, record in inventory["projects"].items() if set(record["hosts"]) & set(hosts)]
        return self._fleet_inspect_operation(selected, inventory)

