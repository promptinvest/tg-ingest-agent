"""Catalog branch of the existing fleet skill; descriptive data never grants tools."""
import answer_policy
import json
import re
import secrets

import fleet_context
import fleet_protocol as protocol
import fleet_queries as queries
import fleet_report
import persona
import store
import tasking


CATALOG_WORDS = re.compile(r"\bip(?:v4)?\b|айпи|адрес|address|провайдер|providers?|hosted|размещ|проект", re.I)


def document(cfg):
    try:
        value = queries.load(cfg)
        cfg._fleet_last_good_facts = value
        return value
    except (ValueError, OSError):
        # This process retains only a previously validated descriptive snapshot.
        # The publisher's immutable history/current pointer remains authoritative.
        return getattr(cfg, "_fleet_last_good_facts", None)


def prior_catalog(cfg, conn, chat_id, question, facts, previous=None):
    previous = fleet_context.recent(conn, chat_id, question) if previous is None else previous
    if queries.prior_valid(facts, previous):
        return previous
    if not previous or not previous.get("hosts"):
        return {}
    import fleet
    try:
        if previous.get("inventory_hash") != protocol.digest(fleet.catalog(cfg)):
            return {}
    except (ValueError, OSError):
        return {}
    row = conn.execute("SELECT source_update,created_at FROM fleet_requests WHERE chat_id=? AND source_update=?",
                       (chat_id, previous.get("source_update"))).fetchone()
    if not row:
        return {}
    known = {subject["id"] for subject in facts["subjects"]}
    subjects = [host for host in previous["hosts"] if host in known]
    if not subjects:
        return {}
    raw = dict(kind="catalog", subjects=subjects, scope="explicit", fields=["label"],
               presentation="list", detail="concise", clarification="")
    plan = queries.validate_plan(raw, facts)
    frame = queries.frame(previous.get("question", question), plan, row["source_update"])
    frame.update(created_at=row["created_at"],
                 original_question=previous.get("original_question", previous.get("question", question)))
    return frame if queries.prior_valid(facts, frame) else {}


def route_plan(cfg, conn, chat_id, question):
    facts = document(cfg)
    if facts is None:
        return None
    previous = fleet_context.recent(conn, chat_id, question)
    # Reformatting a runtime diagnosis is still a runtime correction. Cross-mode
    # inheritance needs an explicitly descriptive field or a semantic plan.
    prior = prior_catalog(cfg, conn, chat_id, question, facts, previous)
    if previous.get("kind") != "catalog" and not CATALOG_WORDS.search(question or ""):
        prior = {}
    return queries.fallback(question, facts, prior)


class CatalogMixin:
    def _fleet_catalog_prior(self, question, facts, previous):
        # Async prompts carry their canonical prior frame, independent of the
        # poller's current inbound update. Find its exact owner row, never prose.
        if queries.prior_valid(facts, previous):
            return previous
        update = previous.get("source_update") if isinstance(previous, dict) else None
        if update is None:
            return {}
        row = self.conn.execute("SELECT chat_id FROM fleet_requests WHERE source_update=? ORDER BY created_at DESC LIMIT 1", (update,)).fetchone()
        if not row:
            return {}
        return prior_catalog(self.cfg, self.conn, row["chat_id"], question, facts, previous)

    def _fleet_planning_messages(self, question, inventory, previous, lang, error=""):
        runtime_previous = self._fleet_runtime_prior(previous, inventory)
        messages = super()._fleet_planning_messages(question, inventory, runtime_previous, lang, error)
        facts = document(self.cfg)
        prior = self._fleet_catalog_prior(question, facts, previous) if facts else {}
        messages[0]["content"] += "\nAlternative LOCAL answer contract, before choosing inspection: " + queries.plan_instruction()
        messages[0]["content"] += (
            " For mixed catalog+live requests return {kind:'mixed',catalog:<catalog plan>,runtime:<original inspect plan>}. "
            "The runtime branch selects only the requested current evidence. Do not substitute CPU for a missing IP/provider. "
            "If published_facts is unavailable, use a catalog plan to disclose its field-specific gap, not an inspection. "
            "Corrections retain the original factual question and scope. "
            "Answer the question directly. Use confirmed style preferences; explicit detail/table requests apply to this turn.\n"
            + answer_policy.hint(self.conn))
        data = json.loads(messages[1]["content"])
        data["published_facts"] = queries.planner_context(facts, prior) if facts else None
        messages[1]["content"] = protocol.canonical(data)
        return messages

    def _fleet_runtime_prior(self, previous, inventory):
        previous = previous or {}
        if previous.get("kind") != "catalog":
            return previous
        facts = document(self.cfg)
        if facts is None or not queries.prior_valid(facts, previous):
            return {}
        hosts, projects = [], []
        for identity in previous["subjects"]:
            if identity in inventory["hosts"]:
                hosts.append(identity)
            elif identity in inventory["projects"]:
                projects.append(identity)
                hosts.extend(inventory["projects"][identity]["hosts"])
        return {"version": 1, "hosts": list(dict.fromkeys(hosts)), "projects": projects,
                "facets": [], "focus": ["overview"], "inventory_hash": protocol.digest(inventory),
                "source_update": previous["source_update"], "question": previous["question"],
                "original_question": previous["original_question"]}

    def _fleet_validate_planning(self, raw, inventory, previous):
        previous = previous or {}
        value = llm_json(raw)
        facts = document(self.cfg)
        kind = value.get("kind") if isinstance(value, dict) else None
        if kind == "mixed":
            if set(value) != {"kind", "catalog", "runtime"} or facts is None:
                raise ValueError("invalid mixed answer contract")
            catalog_plan = queries.validate_plan(value["catalog"], facts, self._fleet_catalog_prior("", facts, previous))
            if catalog_plan["kind"] != "catalog":
                raise ValueError("mixed catalog needs resolved scope")
            plan = super()._fleet_validate_planning(protocol.canonical(value["runtime"]), inventory,
                                                  self._fleet_runtime_prior(previous, inventory))
            plan["catalog_plan"] = catalog_plan
            return plan
        if kind in {"catalog", "clarification"}:
            if facts is not None:
                return queries.validate_plan(value, facts, self._fleet_catalog_prior("", facts, previous))
            if (set(value) != queries.RAW_KEYS or not isinstance(value["fields"], list)
                    or not value["fields"] or any(field not in queries.FIELDS for field in value["fields"])
                    or not isinstance(value["subjects"], list)
                    or any(identity not in inventory["hosts"] and identity not in inventory["projects"] for identity in value["subjects"])):
                raise ValueError("invalid unavailable catalog obligations")
            return {"kind": "catalog_gap", "fields": list(dict.fromkeys(value["fields"])), "clarification": ""}
        return super()._fleet_validate_planning(raw, inventory, self._fleet_runtime_prior(previous, inventory))

    def _fleet_catalog_answer(self, row, payload, plan, facts):
        if plan["kind"] == "catalog_gap":
            labels = queries._LABELS["ru" if payload["lang"] == "ru" else "en"]
            fields = ", ".join(labels[field] for field in plan["fields"])
            pages = [("Записанные сведения недоступны: " if payload["lang"] == "ru" else "Recorded facts are unavailable: ") + fields + "."]
        else:
            if facts is None or plan.get("facts_integrity") != facts["integrity"]:
                raise ValueError("descriptive facts changed before answer delivery")
            pages = queries.render(facts, plan, payload["lang"])
            if plan["kind"] == "catalog":
                previous = self._fleet_catalog_prior(payload["question"], facts, payload.get("prior_scope", {}))
                payload["context"] = queries.frame(payload["question"], plan, row["source_update"], previous)
                payload["answer_obligations"] = queries.cells(facts, plan)
            payload["facts_integrity"] = facts["integrity"]
        payload.update(plan=plan, kind="catalog", answer_pages=pages, answer="\n\n".join(pages))
        self._fleet_deliver_result(row, payload)

    def _fleet_try_catalog_direct(self, chat_id, lang, text, msg_id, update):
        plan = route_plan(self.cfg, self.conn, chat_id, text)
        if plan is None:
            return False
        facts = document(self.cfg)
        previous = self._fleet_recent(chat_id, text)
        payload = {"version": 2, "question": text, "source_hash": tasking.source_hash(text),
                   "lang": lang, "cost": 0, "model_calls": 0, "prior_scope": previous}
        request_id = secrets.token_hex(16)
        self.conn.execute("INSERT INTO fleet_requests VALUES(?,?,?,?,?,?,?)",
                          (request_id, chat_id, update, "preparing", protocol.canonical(payload), protocol.now(), protocol.now()))
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM fleet_requests WHERE id=?", (request_id,)).fetchone()
        try:
            self._fleet_catalog_answer(row, payload, plan, facts)
        except (ValueError, OSError):
            self._fleet_save(row, "ambiguous", payload, "catalog_delivery_unavailable_no_replay")
        return True

    def _fleet_apply_async_plan(self, row, payload, inventory):
        plan = payload["plan"]
        if plan.get("kind") in {"catalog", "clarification", "catalog_gap"}:
            self._fleet_catalog_answer(row, payload, plan, document(self.cfg))
            return
        if plan.get("answer_mode") == "reformat":
            self._fleet_reformat_answer(row, payload, inventory)
            return
        if plan.get("catalog_plan"):
            facts = document(self.cfg)
            payload["catalog_answer_pages"] = queries.render(facts, plan["catalog_plan"], payload["lang"])
            payload["catalog_obligations"] = queries.cells(facts, plan["catalog_plan"])
        return super()._fleet_apply_async_plan(row, payload, inventory)

    def _fleet_reformat_answer(self, row, payload, inventory):
        previous = payload.get("prior_scope", {})
        prior = self.conn.execute("SELECT * FROM fleet_requests WHERE chat_id=? AND source_update=? AND state='done' ORDER BY created_at DESC LIMIT 1",
                                  (row["chat_id"], previous.get("source_update"))).fetchone()
        if prior is None or previous.get("inventory_hash") != protocol.digest(inventory):
            raise ValueError("no compatible retained evidence for this correction")
        saved = json.loads(prior["payload"])
        source = store.assistant_task_source(self.conn, prior["chat_id"], prior["source_update"])
        if not source or source["text"] != saved.get("question") or not saved.get("result"):
            raise ValueError("prior evidence source changed")
        current, original = payload["plan"], saved["plan"]
        for key in ("hosts", "projects", "read_scope", "read_contract_hash"):
            if current.get(key, []) != original.get(key, []):
                raise ValueError("correction cannot change retained evidence scope")
        if not set(current["facets"]) <= set(original["facets"]):
            raise ValueError("correction requests evidence not collected previously")
        report = fleet_report.build(saved["result"], inventory, current)
        decision = saved.get("selection")
        if decision is not None:
            decision = dict(fleet_report.selection(decision, report), next_checks=[])
        payload.update(result=saved["result"], report=report, retained_evidence=True,
                       retained_source_update=prior["source_update"], additional_checks=1,
                       context=self._fleet_context_frame(payload["question"], current, inventory, previous,
                                                         row["source_update"]))
        self._fleet_after_analysis(row, payload, report, decision)

    def _fleet_fail_async(self, row, payload, error, notify=True):
        if notify and payload.get("inference", {}).get("name") == "planning":
            source = store.assistant_task_source(self.conn, row["chat_id"], row["source_update"])
            if source and tasking.source_hash(source["text"]) == payload["source_hash"]:
                facts = document(self.cfg)
                previous = self._fleet_catalog_prior(payload["question"], facts, payload.get("prior_scope", {})) if facts else {}
                source_prior = payload.get("prior_scope") or {}
                descriptive = source_prior.get("kind") == "catalog" or CATALOG_WORDS.search(payload["question"])
                plan = queries.fallback(payload["question"], facts, previous) if facts and descriptive else None
                if plan is not None:
                    payload["planning_gap"] = type(error).__name__
                    self._fleet_catalog_answer(row, payload, plan, facts)
                    return
        return super()._fleet_fail_async(row, payload, error, notify=notify)


def llm_json(raw):
    import llm
    return llm.parse_llm_json(raw)