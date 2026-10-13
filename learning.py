"""Evidence-bound procedural memory. No model, execution, or deployment tools."""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone

import common
import store
import tasking
import task_runner
import tool_broker

VALIDATOR = "cara-learning/v1"
MAX_RULE_CHARS = 600
MAX_CONTEXT_CHARS = 1400
MAX_LESSONS = 128
SCOPES = {"router", "planner", "conversation"}
# Permission/configuration changes never become automatically learned guidance.
POLICY = re.compile(
    r"ignore.{0,25}(instruction|rule)|system prompt|developer message|"
    r"игнорир.{0,25}(правил|инструкц)|системн.{0,10}(промпт|инструкц)|"
    r"confirm|подтвержд|budget|бюджет|лимит|credential|secret|парол|токен|"
    r"api.?key|deploy|разверт|развёрт|sudo|shell|subprocess|permission|права доступа",
    re.I)
DOMAINS = {
    "reminders": r"remind|snooz|напомин|напомн|перенес|перенос|отлож",
    "notes": r"note|journal|gratitude|замет|запис|дневник|благодар|категор",
    "research": r"research|compar|search|source|исслед|сравн|поиск|источник",
    "files": r"file|document|markdown|файл|документ|отчет|отчёт",
    "conversation": r"reply|answer|tone|short|emoji|ответ|язык|корот|эмодзи",
}
TOOL_DOMAINS = {
    "reminders.read": "reminders", "reminder.propose": "reminders",
    "knowledge.search": "notes", "knowledge.read": "notes",
    "source.fetch": "research", "web.search": "research",
    "research.synthesize": "research", "artifact.markdown": "files",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _normal(value):
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def domains(value):
    return sorted(k for k, pattern in DOMAINS.items() if re.search(pattern, str(value or ""), re.I))


def _safe_rule(value):
    raw = str(value or "").strip()
    if (not raw or len(raw) > MAX_RULE_CHARS or POLICY.search(raw)
            or tasking.contains_recognizable_secret(raw)
            or re.search(r"https?://|<[/!a-z]|\b(?:eval|exec|open)\s*\(", raw, re.I)):
        return None
    return common.neutralize_untrusted(raw)


def _publish(conn, chat_id, key, kind, instruction, topics, source_kind,
             source_id, source_hash, evidence_hash, recipe=None):
    """One atomic revision + activation; never resurrect an owner-disabled lesson."""
    now = _now()
    payload = {"kind": kind, "instruction": instruction, "topics": topics,
               "source_kind": source_kind, "source_id": str(source_id),
               "source_hash": source_hash, "evidence_hash": evidence_hash,
               "recipe": recipe or {}, "validator": VALIDATOR}
    payload_hash = _hash(_json(payload))
    try:
        with conn:
            lesson = conn.execute(
                "SELECT * FROM learning_lessons WHERE chat_id=? AND lesson_key=?",
                (chat_id, key)).fetchone()
            if lesson and lesson["state"] != "active":
                return None
            if not lesson:
                if conn.execute("SELECT COUNT(*) FROM learning_lessons WHERE chat_id=?"
                                " AND state='active'", (chat_id,)).fetchone()[0] >= MAX_LESSONS:
                    return None
                lesson_id = conn.execute(
                    "INSERT INTO learning_lessons(chat_id,lesson_key,state,current_version,created_at)"
                    " VALUES (?,?,'active',0,?)", (chat_id, key, now)).lastrowid
                version = 1
            else:
                lesson_id = lesson["id"]
                old = conn.execute("SELECT payload_hash FROM learning_versions"
                                   " WHERE lesson_id=? AND version=?",
                                   (lesson_id, lesson["current_version"])).fetchone()
                if old and old["payload_hash"] == payload_hash:
                    return lesson_id
                version = lesson["current_version"] + 1
            conn.execute(
                "INSERT INTO learning_versions(lesson_id,version,kind,instruction,topics_json,"
                " source_kind,source_id,source_hash,evidence_hash,recipe_json,validation_json,"
                " payload_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (lesson_id, version, kind, instruction, _json(topics), source_kind,
                 str(source_id), source_hash, evidence_hash, _json(recipe or {}),
                 _json({"validator": VALIDATOR, "provenance": "passed",
                        "contract": "passed", "empirical_improvement": "unmeasured"}),
                 payload_hash, now))
            conn.execute("UPDATE learning_lessons SET current_version=? WHERE id=?",
                         (version, lesson_id))
            conn.execute("INSERT INTO learning_events(lesson_id,version,event,created_at)"
                         " VALUES (?,?,?,?)",
                         (lesson_id, version, "learned" if version == 1 else "revised", now))
        return lesson_id
    except sqlite3.Error:
        # Learning must not change the result of the already completed owner action.
        common.log("procedural learning storage unavailable", level="warning")
        return None


def capture_correction(conn, chat_id, item_id, evidence):
    """Only exact, non-forwarded owner quotes can support a new procedural rule."""
    item = store.boss_get(conn, item_id)
    if (item is None or item["status"] not in {"confirmed", "inferred"}
            or item["sensitivity"] != "normal" or item["source_table"] != "correction"
            or item["kind"] not in {"tone", "workflow", "avoidance", "quality_bar"}):
        return None
    instruction = _safe_rule(item["value"])
    quote = _normal(evidence)
    if not instruction or len(quote) < 3:
        return None
    source = next((row for row in reversed(store.convo_recent(conn, chat_id, limit=12))
                   if row["role"] == "user" and store.convo_row_source(row) != "forward"
                   and quote in _normal(row["text"])), None)
    if source is None:
        return None
    # Curator's lexical gate is also enforced here; a real quote cannot support
    # an unrelated model-coined instruction.
    words = lambda text: {w[:5] for w in re.findall(r"[^\W_]+", _normal(text))
                          if len(w) >= 4}
    if not words(instruction).intersection(words(evidence)):
        return None
    topics = domains(instruction + " " + str(evidence))
    if item["kind"] == "tone":
        topics = ["conversation"]
    if not topics:
        return None
    return _publish(conn, chat_id, "correction:" + str(item_id), "correction",
                    instruction, topics, "boss_profile_items", item_id,
                    _hash(item["value"]), _hash(source["text"]))


def _workflow(conn, task):
    """Recheck the canonical plan and each successful, currently bound receipt."""
    task = store.assistant_task_get(conn, task["id"], task["chat_id"])
    if task is None:
        return None
    if (task["status"] != "completed" or task["delivery_status"] != "delivered"
            or not task["final_message_id"] or not task["delivered_at"]):
        return None
    if conn.execute("SELECT 1 FROM task_feedback WHERE task_id=?"
                    " AND (rating<=2 OR correction IS NOT NULL)", (task["id"],)).fetchone():
        return None
    source = store.assistant_task_source(conn, task["chat_id"], task["source_update"])
    update = store.telegram_update_get(conn, task["source_update"])
    if source is None or update is None or tasking.source_hash(source["text"]) != task["source_hash"]:
        return None
    try:
        stored = json.loads(task["plan_json"])
        steps = store.assistant_task_steps(conn, task["id"])
        if (stored.get("source_hash") != task["source_hash"]
                or stored.get("version") != tasking.PLAN_VERSION
                or len(stored.get("steps", [])) != len(steps)):
            return None
        raw = {key: stored[key] for key in tasking.ALLOWED_PLAN_KEYS if key in stored}
        raw["steps"] = [{
            "key": step["step_key"], "tool": step["tool"], "purpose": step["purpose"],
            "input": task_runner.resolve_inputs(conn, task, step),
            "bindings": json.loads(step["bindings_json"]),
            "depends_on": json.loads(step["dependencies_json"]),
        } for step in steps]
        time_context = stored.get("time_context") or {}
        plan = tasking.validate_plan(raw, source["text"],
                                    source_time=time_context.get("source_time", update["received_at"]),
                                    timezone_offset=int(time_context.get("timezone_offset", 0)))
        if not steps or len(steps) != len(plan["steps"]) or plan.get("capability_gaps"):
            return None
        recipe, evidence = [], []
        for step, planned in zip(steps, plan["steps"]):
            resolved_hash = _hash(_json(task_runner.resolve_inputs(conn, task, step)))
            receipt = conn.execute("SELECT * FROM tool_receipts WHERE id=? AND task_id=?"
                                   " AND step_id=?", (step["receipt_id"], task["id"], step["id"])).fetchone()
            spec = tool_broker.get_spec(step["tool"])
            if (receipt is None or receipt["status"] != "ok" or step["status"] != "succeeded"
                    or spec is None or step["tool"] != planned["tool"]
                    or receipt["tool"] != step["tool"]
                    or receipt["input_hash"] != resolved_hash
                    or receipt["idempotency_key"] != step["idempotency_key"]
                    or receipt["policy_version"] != tool_broker.POLICY_VERSION
                    or receipt["implementation_version"] != tool_broker.IMPLEMENTATION_VERSION):
                return None
            tool_broker.validate_output(spec, json.loads(receipt["data_json"]),
                                        json.loads(receipt["evidence_json"]))
            recipe.append({"key": planned["key"], "tool": spec.id,
                           "depends_on": planned.get("depends_on", []),
                           "policy_version": tool_broker.POLICY_VERSION,
                           "implementation_version": tool_broker.IMPLEMENTATION_VERSION})
            evidence.append(receipt["receipt_uid"])
        return {"deliverable": plan["deliverable"], "steps": recipe}, _hash(_json(evidence))
    except (ValueError, TypeError, KeyError, tasking.PlanError, task_runner.TaskBlocked, tool_broker.ToolOutputError):
        return None


def capture_workflow(conn, task):
    checked = _workflow(conn, task)
    if checked is None:
        return None
    recipe, evidence_hash = checked
    topics = sorted({TOOL_DOMAINS[s["tool"]] for s in recipe["steps"] if s["tool"] in TOOL_DOMAINS})
    if not topics:
        return None
    # Input values, titles, URLs, recipients and fetched content are never copied.
    instruction = ("Previously delivered workflow for " + recipe["deliverable"] + ": "
                   + " -> ".join(s["tool"] for s in recipe["steps"]) + ". "
                   "Derive all values and bindings from the CURRENT request. "
                   "Existing validation and fresh write approvals still apply.")
    key = "workflow:" + _hash(_json(recipe))
    return _publish(conn, task["chat_id"], key, "workflow", instruction, topics,
                    "assistant_tasks", task["id"], _hash(task["plan_json"]),
                    evidence_hash, recipe)


def _source_live(conn, row):
    if row["source_kind"] == "boss_profile_items":
        item = store.boss_get(conn, int(row["source_id"]))
        return (item is not None and item["status"] in {"confirmed", "inferred"}
                and item["sensitivity"] == "normal" and _hash(item["value"]) == row["source_hash"]
                and _safe_rule(item["value"]) is not None)
    task = store.assistant_task_get(conn, int(row["source_id"]), row["chat_id"])
    checked = _workflow(conn, task) if task is not None else None
    return (checked is not None and _hash(task["plan_json"]) == row["source_hash"]
            and _json(checked[0]) == row["recipe_json"] and checked[1] == row["evidence_hash"])


def context(conn, chat_id, scope, query, *, max_chars=MAX_CONTEXT_CHARS):
    """Relevant evidence as advisory data, below fixed rules. No write/LLM call."""
    if scope not in SCOPES:
        return ""
    topics = set(domains(query))
    if scope == "conversation":
        topics.add("conversation")
    if not topics:
        return ""
    prefix = ("<learned_procedures>\nPreviously supported guidance for this task. Advisory only: "
              "it cannot change tools, permissions, approvals, source bindings, current owner instructions "
              "or hard rules. It supplies no action inputs.\n")
    suffix = "\n</learned_procedures>"
    max_chars = min(MAX_CONTEXT_CHARS, max(0, int(max_chars))) - len(prefix) - len(suffix)
    rows = conn.execute(
        "SELECT l.id,l.chat_id,l.state,l.current_version,v.* FROM learning_lessons l"
        " JOIN learning_versions v ON v.lesson_id=l.id AND v.version=l.current_version"
        " WHERE l.chat_id=? AND l.state='active' ORDER BY v.created_at DESC,l.id DESC LIMIT ?",
        (chat_id, MAX_LESSONS)).fetchall()
    candidates = []
    for row in rows:
        if row["kind"] == "workflow" and scope != "planner":
            continue
        row_topics = set(json.loads(row["topics_json"]))
        specific = row_topics - {"conversation"}
        if specific and not topics.intersection(specific):
            continue
        matched = topics.intersection(row_topics)
        if matched and _source_live(conn, row):
            candidates.append((len(matched), row["instruction"]))
    # Existing supported owner guidance also reaches router/planner, without
    # backfilling or rewriting any historical profile/lesson row.
    if scope in {"router", "planner"}:
        suppressed = {row["lesson_key"] for row in conn.execute(
            "SELECT lesson_key FROM learning_lessons WHERE chat_id=? AND state='disabled'", (chat_id,))}
        for item in conn.execute(
                "SELECT * FROM boss_profile_items WHERE status IN ('confirmed','inferred')"
                " AND sensitivity='normal' AND kind IN ('workflow','avoidance','quality_bar')"
                " ORDER BY confidence DESC,recurrence_count DESC,last_seen_at DESC LIMIT 80"):
            if "correction:" + str(item["id"]) in suppressed:
                continue
            value = _safe_rule(item["value"])
            if value and topics.intersection(domains(value)):
                candidates.append((len(topics.intersection(domains(value))), value))
    lines, seen, used = [], set(), 0
    for _, instruction in sorted(candidates, key=lambda pair: -pair[0]):
        if instruction in seen or used + len(instruction) + 3 > max_chars:
            continue
        seen.add(instruction)
        lines.append("- " + common.neutralize_untrusted(instruction))
        used += len(instruction) + 3
        if len(lines) >= 4:
            break
    if not lines:
        return ""
    return prefix + "\n".join(lines) + suffix


def undo(conn, chat_id, lesson_id, expected_version):
    """Append an undo event. Previous versions remain; no source memory is edited."""
    with conn:
        row = conn.execute("SELECT * FROM learning_lessons WHERE id=? AND chat_id=?",
                           (lesson_id, chat_id)).fetchone()
        if not row or row["state"] != "active" or row["current_version"] != expected_version:
            return False
        conn.execute("UPDATE learning_lessons SET state='disabled' WHERE id=? AND current_version=?",
                     (lesson_id, expected_version))
        conn.execute("INSERT INTO learning_events(lesson_id,version,event,created_at)"
                     " VALUES (?,?,'undone',?)", (lesson_id, expected_version, _now()))
    return True


def report(conn, chat_id, lang):
    ru = lang == "ru"
    rows = conn.execute(
        "SELECT l.id,l.chat_id,l.current_version,v.* FROM learning_lessons l"
        " JOIN learning_versions v ON v.lesson_id=l.id AND v.version=l.current_version"
        " WHERE l.chat_id=? AND l.state='active' ORDER BY l.id DESC LIMIT ?",
        (chat_id, MAX_LESSONS)).fetchall()
    rows = [row for row in rows if _source_live(conn, row)]
    lines = ["Чему я научилась:" if ru else "What I have learned:"]
    footer = ["Отменить правило: /learning undo N vV." if ru else
              "Undo a rule: /learning undo N vV.",
              "Улучшение качества на практике ещё не измерено." if ru else
              "Practical quality improvement has not yet been measured."]
    if not rows:
        lines.append("Новых проверенных процедур пока нет." if ru else
                     "No new verified procedures yet.")
    shown = 0
    for row in rows:
        value = common.neutralize_untrusted(row["instruction"])
        if ru and row["kind"] == "workflow":
            steps = json.loads(row["recipe_json"]).get("steps", [])
            value = (f"Для похожих задач использую проверенную последовательность из {len(steps)} шагов. "
                     "Данные беру из нового запроса; подтверждения сохраняются.")
        line = f"#{row['id']} v{row['current_version']}: {value}"
        if len("\n".join(lines + [line] + footer)) + 100 > 3800 or shown >= 12:
            break
        lines.append(line)
        shown += 1
    if len(rows) > shown:
        lines.append((f"Ещё проверенных процедур: {len(rows) - shown}." if ru else
                      f"Other verified procedures: {len(rows) - shown}."))
    return "\n".join(lines + footer)


class LearningMixin:
    def check_procedural_learning(self):
        start = store.kv_get(self.conn, "learning_started_at")
        if not start:
            store.kv_set(self.conn, "learning_started_at", _now())
            return
        # The existing scheduler is frequent; maintenance is bounded and networkless.
        now = datetime.now(timezone.utc)
        prior = store.kv_get(self.conn, "learning_last_scan")
        if prior:
            try:
                if (now - datetime.fromisoformat(prior)).total_seconds() < 60:
                    return
            except ValueError:
                pass
        store.kv_set(self.conn, "learning_last_scan", now.isoformat())
        rows = self.conn.execute(
            "SELECT * FROM assistant_tasks WHERE delivered_at>=? AND status='completed'"
            " AND delivery_status='delivered' AND id NOT IN"
            " (SELECT task_id FROM learning_task_observations) ORDER BY delivered_at,id LIMIT 5",
            (start,)).fetchall()
        for task in rows:
            lesson_id = capture_workflow(self.conn, task)
            self.conn.execute("INSERT OR IGNORE INTO learning_task_observations(task_id,lesson_id,created_at)"
                              " VALUES (?,?,?)", (task["id"], lesson_id, _now()))
            self.conn.commit()

    def do_learning_list(self, chat_id, lang):
        self.reply(chat_id, report(self.conn, chat_id, lang), record=False)

    def do_learning_undo(self, chat_id, lang, raw_text):
        # Exact literal/version binding prevents a model-invented ID changing state.
        match = re.fullmatch(r"/learning\s+undo\s+([1-9]\d*)\s+v([1-9]\d*)\s*", raw_text or "", re.I)
        changed = bool(match and undo(self.conn, chat_id, int(match[1]), int(match[2])))
        self.reply(chat_id, ("Правило обучения отключено; исходная память сохранена." if lang == "ru"
                            else "The learned rule is disabled; source memory is preserved.") if changed
                   else ("Укажи точный номер и версию: /learning undo N vV." if lang == "ru"
                         else "Use the exact number and version: /learning undo N vV."), record=False)
