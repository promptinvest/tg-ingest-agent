"""Versioned fleet inference phases; network I/O never runs on the owner loop."""
import json
import secrets
from pathlib import Path

import common
import answer_policy
import fleet_lifecycle
import fleet_context
import fleet_inference
import fleet_protocol as protocol
import fleet_report
import llm
import store
import tasking

VERSION = "cara-fleet-inference/v1"
STATES = tuple(name + "_" + phase for name in ("planning", "synthesis")
               for phase in ("queued", "started", "completed", "consumed"))


class AsyncMixin:
    def _fleet_facts_revision(self):
        # The facts/query integration can supply its independently validated
        # publication revision here; absence is itself an explicit binding.
        path = getattr(self.cfg, "fleet_facts", "/etc/cara-fleet/facts/current.json")
        if not path:
            return protocol.digest(None)
        try:
            Path(path).lstat()
        except FileNotFoundError:
            return protocol.digest(None)
        import fleet_facts
        document = fleet_facts.load(path)
        return protocol.digest({"revision": document["revision"],
                                "integrity": document["integrity"]})

    def _fleet_authority(self, inventory):
        profiles = llm.profiles(self.cfg)
        return {"inventory_hash": protocol.digest(inventory),
                "facts_hash": self._fleet_facts_revision(),
                "capability_hash": protocol.digest({
                    "version": VERSION, "fleet_protocol": protocol.VERSION,
                    "profiles": {name: profiles[name] for name in ("task_planner", "task_synthesis")},
                    "call_limit": getattr(self.cfg, "task_model_call_limit", 4),
                    "cost_limit": self.cfg.task_cost_limit_usd}),
                "policy_version": VERSION}

    def _fleet_planning_messages(self, question, inventory, previous, lang, error=""):
        import fleet
        messages = fleet.planner_messages(question, inventory, previous, lang)
        if error:
            messages[0]["content"] += (" Repair the previous schema error: " + error
                                      + ". Use only the declared IDs, fields and facet names.")
        return messages

    def _fleet_validate_planning(self, raw, inventory, previous):
        import fleet
        return fleet.validate_plan(llm.parse_llm_json(raw), inventory, previous)

    def _fleet_phase_messages(self, payload, inventory):
        if payload["inference"]["name"] == "planning":
            return self._fleet_planning_messages(
                payload["question"], inventory, payload.get("prior_scope", {}),
                payload["lang"], payload.get("planning_error", ""))
        # Selection records a derived optional-check gap on the report. It
        # cannot retroactively change the immutable model input at recovery.
        report = dict(payload["report"])
        report.pop("rejected_optional_checks", None)
        messages = fleet_report.messages(payload["question"], report, payload["lang"])
        hint = answer_policy.hint(self.conn)
        if hint:
            messages[0]["content"] += "\n" + hint
        return messages

    def _fleet_queue_phase(self, row, payload, name, inventory):
        if payload.get("model_calls", 0) >= getattr(self.cfg, "task_model_call_limit", 4):
            raise ValueError("investigation model-call limit reached")
        if payload.get("cost", 0) >= self.cfg.task_cost_limit_usd:
            raise ValueError("investigation budget limit reached")
        if "inference" in payload:
            payload.setdefault("inference_history", []).append(payload["inference"])
        phase = {"version": 1, "name": name, "nonce": secrets.token_hex(16),
                 "queued_at": protocol.now(), "attempts": []}
        payload["version"] = 2
        payload["inference"] = phase
        payload["model_calls"] = payload.get("model_calls", 0) + 1
        authority = self._fleet_authority(inventory)
        phase["binding"] = dict(authority, request_id=row["id"], phase=name,
                                nonce=phase["nonce"], source_hash=payload["source_hash"],
                                input_hash=protocol.digest(self._fleet_phase_messages(payload, inventory)))
        self._fleet_save(row, name + "_queued", payload, "inference_phase_queued")

    def _fleet_begin_async(self, chat_id, lang, text, msg_id, maintenance, update, inventory, previous):
        request_id = secrets.token_hex(16)
        payload = {"version": 2, "question": text, "source_hash": tasking.source_hash(text),
                   "lang": lang, "prior_scope": previous, "allow_maintenance": bool(maintenance),
                   "catalog_hash": protocol.digest(inventory), "cost": 0, "model_calls": 0,
                   "planning_attempt": 1, "source_message": msg_id,
                   "trace_id": common.current_trace()}
        # Canonical intent is durable before either reservation or transport.
        self.conn.execute("INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?)",
                          (request_id, chat_id, update, "planning_queued", protocol.canonical(payload),
                           protocol.now(), protocol.now()))
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (request_id,)).fetchone()
        self._fleet_queue_phase(row, payload, "planning", inventory)
        self.reply(chat_id, "\u041f\u0440\u043e\u0432\u0435\u0440\u044f\u044e \u0444\u043b\u043e\u0442; \u0432\u0435\u0440\u043d\u0443\u0441\u044c \u0441 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u0430\u043c\u0438."
                   if lang == "ru" else "I'm checking the fleet and will return with the findings.",
                   reply_to=msg_id)

    def _fleet_request_authority_current(self, row, payload, binding=None):
        import fleet
        binding = binding or payload["inference"]["binding"]
        source = store.assistant_task_source(self.conn, row["chat_id"], row["source_update"])
        if source is None or tasking.source_hash(source["text"]) != payload["source_hash"]:
            return False
        inventory = fleet.catalog(self.cfg)
        return all(binding[key] == value for key, value in self._fleet_authority(inventory).items())

    def _fleet_phase_current(self, row, payload, binding):
        import fleet
        if row["state"] not in STATES:
            return False
        phase = payload.get("inference", {})
        if phase.get("binding") != binding or phase.get("nonce") != binding["nonce"]:
            return False
        if not self._fleet_request_authority_current(row, payload, binding):
            return False
        inventory = fleet.catalog(self.cfg)
        return binding["input_hash"] == protocol.digest(self._fleet_phase_messages(payload, inventory))

    def _fleet_phase_cost(self, phase):
        ids = [receipt["reservation_id"] for receipt in phase.get("attempts", [])]
        if not ids:
            return 0.0
        return self.conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM llm_usage WHERE id IN ("
                                 + ",".join("?" for _ in ids) + ")", ids).fetchone()[0]

    def _fleet_new_call(self, row, payload, inventory):
        phase = payload["inference"]
        binding = phase["binding"]
        request_id = row["id"]

        def current(bound):
            fresh = self.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (request_id,)).fetchone()
            return bool(fresh and self._fleet_phase_current(fresh, json.loads(fresh["payload"]), bound))

        def before_dispatch(bound, receipt):
            fresh = self.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (request_id,)).fetchone()
            if fresh is None:
                raise fleet_inference.StalePhase("fleet request was removed")
            document = json.loads(fresh["payload"])
            if not self._fleet_phase_current(fresh, document, bound):
                raise fleet_inference.StalePhase("fleet request authority changed")
            item = dict(receipt, started_at=protocol.now())
            document["inference"]["attempts"].append(item)
            self._fleet_save(fresh, phase["name"] + "_started", document, "inference_dispatch_bound")
            if document.get("cost", 0) + self._fleet_phase_cost(document["inference"]) > self.cfg.task_cost_limit_usd:
                raise ValueError("investigation reservation exceeds its budget")

        call = fleet_inference.ProfileCall(
            self.cfg, self.conn, "fleet_planner" if phase["name"] == "planning" else "fleet_synthesis",
            self._fleet_phase_messages(payload, inventory),
            profile="task_planner" if phase["name"] == "planning" else "task_synthesis",
            json_required=True, binding=binding, before_dispatch=before_dispatch, is_current=current)
        call.trace_id = payload.get("trace_id")
        return call

    def _fleet_complete_phase(self, row, payload, call=None):
        phase = payload["inference"]
        if not phase.get("accounted"):
            phase["cost"] = self._fleet_phase_cost(phase)
            payload["cost"] = payload.get("cost", 0) + phase["cost"]
            phase["accounted"] = True
        phase["completed_at"] = protocol.now()
        if call is None:
            phase["error"] = "InterruptedInference"
            phase["usage_unknown"] = True
        else:
            phase["error"] = call.error_summary
            phase["usage_unknown"] = bool(getattr(call.error, "usage_unknown", False))
            if call.error is None:
                phase["raw"] = call.result
        self._fleet_save(row, phase["name"] + "_completed", payload, "inference_phase_completed")

    def _fleet_fail_async(self, row, payload, error, notify=True):
        # Persist terminal state before an optional best-effort notice. A crash
        # never turns that notice or a failed paid phase into an automatic replay.
        payload["inference_gap"] = type(error).__name__
        fresh = self.conn.execute("SELECT state FROM fleet_requests WHERE id=?", (row["id"],)).fetchone()
        if fresh is None:
            return
        state = "ambiguous" if fresh["state"] in {"executing", "delivering"} else "failed"
        self._fleet_cancel_executor(row, payload, "source_or_inference_authority_changed")
        self._fleet_save(row, state, payload, "inference_failed_no_replay")
        if notify:
            self.reply(row["chat_id"], "\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u043e\u0432\u0435\u0440\u043d\u043e\u0433\u043e \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u0430; \u043d\u0443\u0436\u043d\u0430 \u043d\u043e\u0432\u0430\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430."
                       if payload["lang"] == "ru" else "I have no reliable result; a fresh investigation is needed.")

    def _fleet_apply_async_plan(self, row, payload, inventory):
        import fleet
        plan = payload["plan"]
        if plan["clarification"]:
            payload["answer"] = tasking.redact_derived_text(plan["clarification"])
            self._fleet_deliver_result(row, payload)
            return
        if plan["operation"] == "restart" and not payload["allow_maintenance"]:
            raise ValueError("a read-only route cannot request maintenance")
        payload["context"] = self._fleet_context_frame(
            payload["question"], plan, inventory, payload.get("prior_scope"), row["source_update"])
        operation = self._fleet_inspect_operation(plan, inventory)
        self._fleet_submit_operation(row, payload, operation, 1, "preparing", "inspection_submission_bound")
        self._fleet_save(row, "queued", payload, "inspection_queued")

    def _fleet_consume_phase(self, row, payload, inventory):
        phase = payload["inference"]
        if not self._fleet_phase_current(row, payload, phase["binding"]):
            self._fleet_fail_async(row, payload, fleet_inference.StalePhase(), notify=False)
            return
        if phase["name"] == "planning":
            if row["state"] != "planning_consumed":
                if phase.get("error"):
                    self._fleet_fail_async(row, payload, ValueError("planning unavailable"))
                    return
                try:
                    plan = self._fleet_validate_planning(phase["raw"], inventory, payload.get("prior_scope"))
                    if payload["cost"] > self.cfg.task_cost_limit_usd:
                        raise ValueError("investigation planning budget exceeded")
                except ValueError as exc:
                    maximum = max(1, min(2, int(getattr(self.cfg, "task_model_call_limit", 4)) - 1))
                    if payload.get("planning_attempt", 1) >= maximum or payload["cost"] >= self.cfg.task_cost_limit_usd:
                        self._fleet_fail_async(row, payload, exc)
                        return
                    payload["planning_error"] = str(exc)[:160]
                    payload["planning_attempt"] = payload.get("planning_attempt", 1) + 1
                    phase["consumed_at"] = protocol.now()
                    self._fleet_queue_phase(row, payload, "planning", inventory)
                    return
                payload["plan"] = plan
                phase["consumed_at"] = protocol.now()
                self._fleet_save(row, "planning_consumed", payload, "planning_validated")
            self._fleet_apply_async_plan(row, payload, inventory)
        else:
            decision = payload.get("selection") if row["state"] == "synthesis_consumed" else None
            if row["state"] != "synthesis_consumed":
                try:
                    if phase.get("error"):
                        raise ValueError("automatic analysis unavailable")
                    decision = fleet_report.selection(llm.parse_llm_json(phase["raw"]), payload["report"])
                    payload["selection"] = decision
                except ValueError as exc:
                    payload["analysis_gap"] = str(exc)[:160]
                phase["consumed_at"] = protocol.now()
                self._fleet_save(row, "synthesis_consumed", payload, "synthesis_validated_or_fallback")
            self._fleet_after_analysis(row, payload, payload["report"], decision)

    def _fleet_queue_synthesis(self, row, payload, inventory):
        try:
            self._fleet_queue_phase(row, payload, "synthesis", inventory)
        except ValueError as exc:
            payload["analysis_gap"] = str(exc)[:160]
            self._fleet_after_analysis(row, payload, payload["report"], None)

    def _fleet_check_inference(self):
        if fleet_lifecycle.admission_paused(self.conn):
            return 0
        import fleet
        calls = getattr(self, "_fleet_inference_calls", None)
        if calls is None:
            calls = self._fleet_inference_calls = {}
        # Purged/cancelled requests can no longer deliver or recreate content.
        # Settle any in-flight reservation before dropping the in-memory handle.
        for request_id, call in list(calls.items()):
            row = self.conn.execute("SELECT state FROM fleet_requests WHERE id=?", (request_id,)).fetchone()
            if row is None or row["state"] not in STATES:
                call.cancel()
                if call.advance():
                    calls.pop(request_id, None)
        rows = self.conn.execute("SELECT * FROM fleet_requests WHERE state IN ("
                                 + ",".join("?" for _ in STATES) + ") ORDER BY created_at LIMIT 8", STATES).fetchall()
        for original in rows:
            row = self.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (original["id"],)).fetchone()
            payload = json.loads(row["payload"])
            try:
                phase = payload.get("inference")
                if not phase:
                    self._fleet_fail_async(row, payload, ValueError("incomplete phase receipt"))
                    continue
                if not self._fleet_phase_current(row, payload, phase["binding"]):
                    call = calls.get(row["id"])
                    if call:
                        call.cancel()
                        if call.advance():
                            calls.pop(row["id"], None)
                    self._fleet_fail_async(row, payload, fleet_inference.StalePhase(), notify=False)
                    continue
                inventory = fleet.catalog(self.cfg)
                if row["state"].endswith(("_completed", "_consumed")):
                    self._fleet_consume_phase(row, payload, inventory)
                    continue
                call = calls.get(row["id"])
                if call is None and row["state"].endswith("_started"):
                    self._fleet_complete_phase(row, payload)
                else:
                    if call is None:
                        call = calls[row["id"]] = self._fleet_new_call(row, payload, inventory)
                    if not call.advance():
                        continue
                    # The reservation callback may have added attempts since
                    # this iteration loaded the row. Accept only that fresh row.
                    row = self.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (row["id"],)).fetchone()
                    if row is None or row["state"] not in STATES:
                        calls.pop(original["id"], None)
                        continue
                    payload = json.loads(row["payload"])
                    self._fleet_complete_phase(row, payload, call)
                    calls.pop(row["id"], None)
                row = self.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (row["id"],)).fetchone()
                self._fleet_consume_phase(row, json.loads(row["payload"]), inventory)
            except (ValueError, OSError) as exc:
                self._fleet_fail_async(row, payload, exc,
                                       notify=not isinstance(exc, fleet_inference.StalePhase))
        return len(rows)
