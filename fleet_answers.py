"""Question-aligned presentation of already validated fleet evidence."""
import answer_policy
import fleet_report


def _relevant(field, focus):
    if "overview" in focus or "resources" in focus:
        return True
    prefixes = []
    for item in focus:
        prefixes.extend({"cpu": ["resources.cpu_percent", "resources.top_cpu"],
                         "memory": ["resources.memory_used_percent", "resources.top_memory"],
                         "disk": ["resources.disk_used_percent"], "services": ["services"],
                         "errors": ["errors"], "projects": ["projects"], "release": ["projects"],
                         "progress": ["capability.progress", "projects"],
                         "backups": ["capability.backups", "projects"],
                         "trend": ["history", "resources"]}.get(item, []))
    return field == "availability" or any(field == prefix or field.startswith(prefix + ".") for prefix in prefixes)


def _exception(observation):
    if observation["status"] not in {"observed", "recorded"}:
        return True
    value = str(observation.get("value", "")).casefold()
    return any(word in value for word in ("'failed'", "'unhealthy'", "'oomkilled': true", "'status': false"))


def render(report, decision, plan, lang="en", conn=None):
    """Retain the full report; display requested coverage and relevant exceptions.

    Model selections are independently validated by fleet_report.selection. This
    renderer never publishes model prose or permits a citation to invent a claim.
    """
    selected = fleet_report.selection(decision, report) if decision is not None else dict(highlights=[], hypotheses=[], next_checks=[])
    detail = plan.get("detail") or (answer_policy.default_detail(conn) if conn is not None else "concise")
    if detail == "detailed":
        # Original exhaustive coverage remains available on explicit request.
        return fleet_report.render(report, dict(selected, highlights=[]), lang)
    field, ru = ("ru", True) if lang == "ru" else ("en", False)
    focus = set(report.get("focus", ["overview"]))
    lines = []
    presentation = plan.get("presentation", "prose")
    table = presentation == "table"
    if table:
        lines.extend(["| " + ("Сервер | Ответ" if ru else "Server | Answer") + " |", "| --- | --- |"])
    for host in report["hosts"]:
        coverage = report["coverage"][host]
        keys = [key for key in coverage["findings"] if _relevant(report["observations"][key]["field"], focus)]
        # Focused CPU/memory questions use their scalar/process observations,
        # not the composite CPU+RAM+disk sentence retained in coverage.
        if focus <= {"cpu", "memory", "disk"}:
            keys = [key for key, item in report["observations"].items()
                    if item["host"] == host and _relevant(item["field"], focus) and item["field"] != "resources"]
        for key in selected["highlights"]:
            item = report["observations"][key]
            if item["host"] == host and _relevant(item["field"], focus) and key not in keys:
                keys.append(key)
        # Failures and unavailable requested capabilities cannot be hidden by a
        # model highlight list. Unrelated observations remain in the stored report.
        for key, item in report["observations"].items():
            if item["host"] == host and _relevant(item["field"], focus) and _exception(item) and key not in keys:
                keys.append(key)
        values = list(dict.fromkeys(report["findings"][key][field] for key in keys))
        if not values:
            values = ["нет сведений по запрошенной проверке" if ru else "requested evidence is unavailable"]
        label = coverage["label"]
        if table:
            lines.append("| " + label.replace("|", "/") + " | " + "; ".join(values).replace("|", "/") + " |")
        else:
            prefix = "• " if presentation == "list" else ""
            lines.append(prefix + label + ": " + "; ".join(values))
    # Hypotheses are useful for diagnostic causes, never for catalog/status
    # questions. Their evidence prerequisites were checked above.
    if focus & {"cpu", "memory", "trend"}:
        for item in selected["hypotheses"]:
            rule = fleet_report.HYPOTHESES[item["kind"]]
            if not _relevant(rule[0], focus):
                continue
            lines.append(("Возможная причина — " if ru else "Possible cause — ") +
                         report["coverage"][item["host"]]["label"] + ": " + rule[1 if ru else 2])
    lines.append(("Снимок: " if ru else "Snapshot: ") + fleet_report.stamp(report["observed_at"]))
    pages, current = [], ""
    for line in lines:
        while len(line) > 3500:
            if current:
                pages.append(current)
                current = ""
            cut = line.rfind("; ", 0, 3400)
            cut = cut if cut > 0 else 3400
            pages.append(line[:cut])
            line = line[cut:].lstrip("; ")
        if len(current) + len(line) + 1 > 3700:
            pages.append(current)
            current = ""
        current += ("\n" if current else "") + line
    if current:
        pages.append(current)
    return pages