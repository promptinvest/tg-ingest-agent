"""Owner-loop continuation for the existing typed all-content purge.

No inbound update is replayed. Prepared intent holds new fleet admission, the
executor safe point stops transports, and live model calls settle their original
budget usage before a nonce/source/PID/preview-bound acknowledgement can permit
DB deletion. Only the existing typed phrase authorizes a changed preview.
"""
import json

import fleet_lifecycle as lifecycle
import fleet_protocol as protocol
import store
from texts import T


_ACTIVE = ("admission_prepared", "admission_queued", "preparing", "queued", "inspection_started", "executing", "ready", "approval", "approval_sending",
           "delivering", "analysis_started", "mcp_facts_queued", "mcp_facts_started") + tuple(name + "_" + phase for name in ("planning", "synthesis")
                                                     for phase in ("queued", "started", "completed", "consumed"))


class FleetLifecycleMixin:
    def _fleet_settle_content_calls(self):
        self._fleet_mcp_queued_nonces = {}
        receipt = lifecycle.settle_async_calls(self)
        slot = getattr(self, "_fleet_mcp_facts_slot", None)
        if slot is None:
            return receipt
        facts_receipt = slot.settle(purge_hold=True)
        if facts_receipt["pending_calls"] == 0:
            self._fleet_mcp_facts_pending = None
        count = receipt["pending_calls"] + facts_receipt["pending_calls"]
        return {"status": "waiting" if count else "settled", "pending_calls": count}
    def _fleet_content_enabled(self):
        explicit = getattr(self.cfg, "fleet_content_lifecycle", None)
        if explicit is not None and type(explicit) is not bool:
            raise lifecycle.FleetLifecycleError("invalid fleet lifecycle configuration")
        return bool(explicit or getattr(self.cfg, "fleet_enabled", False) or lifecycle.db_preview(self.conn))

    def fleet_purge_preview(self):
        # Empty, never-enabled legacy installations do not inspect another spool
        # and retain their exact old preview and typed confirmation behavior.
        return lifecycle.preview(self.conn, self.cfg) if self._fleet_content_enabled() else {}

    def _fleet_purge_save(self, value):
        lifecycle._save_authorization(self.conn, value)
        self.conn.commit()

    def prepare_fleet_purge(self, chat_id, lang, nonce, confirmed_info):
        if not self._fleet_content_enabled():
            return "ready"
        value = lifecycle._authorization(self.conn, nonce)
        if value is None or value["phase"] != "prepared":
            raise lifecycle.FleetLifecycleError("typed purge authorization unavailable")
        owner = value.get("fleet_owner")
        if owner and owner.get("chat_id") != chat_id:
            raise lifecycle.FleetLifecycleError("fleet purge owner mismatch")
        value.update(fleet_phase="quiescing", fleet_owner={"chat_id": int(chat_id), "lang": lang,
                     "confirmed": dict(confirmed_info), "stage": "quiescing",
                     "source_update": getattr(self, "_current_update_id", None),
                     "track_updates": getattr(self, "_current_update_id", None) is not None})
        # A renewed typed phrase binds the same nonce, never an old receipt.
        value.pop("fleet_quiescence", None)
        self._fleet_purge_save(value)
        return self._advance_fleet_content_purge(nonce, in_update=True)

    def _renew_fleet_purge_preview(self, value, actual, *, in_update):
        owner = value["fleet_owner"]
        anticipated = dict(actual)
        if owner.get("track_updates"):
            anticipated["conversation"] = anticipated.get("conversation", 0) + 1
            anticipated["updates_scrubbed"] = anticipated.get("updates_scrubbed", 0) + (2 if in_update else 1)
        phrase = T(owner["lang"], "purge_phrase_all")
        store.pending_set(self.conn, owner["chat_id"], "purge", {"scope": "all", "category": None,
                          "phrase": phrase, "anticipated": anticipated, "fleet_purge_nonce": value["nonce"]}, ttl_seconds=300)
        owner["stage"] = "awaiting_confirmation"
        self._fleet_purge_save(value)
        self.reply(owner["chat_id"], T(owner["lang"], "purge_preview",
                   impact=self._purge_impact_text(owner["lang"], anticipated), phrase=phrase), record=False)
        return "refresh"

    def _advance_fleet_content_purge(self, nonce, *, in_update=False):
        value = lifecycle._authorization(self.conn, nonce)
        owner = value.get("fleet_owner") if value else None
        if not isinstance(owner, dict) or value["phase"] != "prepared":
            raise lifecycle.FleetLifecycleError("fleet purge continuation unavailable")
        counts = {key: n for key, n in owner["confirmed"].items() if key in lifecycle.PREVIEW_KEYS}
        lifecycle.cancel_spooled_requests(self.cfg)
        # Preserve canonical payload/history. Maintenance already dispatched is
        # ambiguous; cancellation cannot claim that an effect was undone.
        marks = ",".join("?" for _ in _ACTIVE)
        self.conn.execute("UPDATE fleet_requests SET state=CASE WHEN state IN ('executing','delivering') THEN 'ambiguous' ELSE 'cancelled' END, updated_at=? WHERE state IN (" + marks + ")", (protocol.now(), *_ACTIVE))
        self.conn.commit()
        async_receipt = self._fleet_settle_content_calls()
        lifecycle.request_quiescence(self.cfg, nonce, counts)
        ack = lifecycle.executor_ack(self.cfg, nonce, counts)
        if ack is None or async_receipt != {"status": "settled", "pending_calls": 0}:
            return "pending"
        actual = self._content_purge_preview("all")
        if in_update and owner.get("track_updates"):
            actual["updates_scrubbed"] = actual.get("updates_scrubbed", 0) + 1
        if actual != owner["confirmed"]:
            # No DB content or transport evidence has been removed at this point.
            return self._renew_fleet_purge_preview(value, self._content_purge_preview("all"), in_update=in_update)
        lifecycle.record_quiescence(self.conn, nonce, ack, async_receipt,
                                    {key: n for key, n in actual.items() if key in lifecycle.PREVIEW_KEYS})
        return "ready"

    def check_fleet_content_purge(self):
        """One nonblocking tick; dispatcher must call before ordinary fleet pumps."""
        value = lifecycle._authorization(self.conn)
        if not value or not value.get("fleet_phase") or value["phase"] == "db_committed":
            return False
        owner = value.get("fleet_owner")
        if not isinstance(owner, dict):
            return False
        if owner.get("abort_requested"):
            return self.abort_fleet_purge(value["nonce"])
        if owner.get("stage") == "awaiting_confirmation":
            pending = store.pending_get(self.conn, owner["chat_id"])
            if (not pending or pending["kind"] != "purge"
                    or pending["payload"].get("fleet_purge_nonce") != value["nonce"]):
                return self.cancel_fleet_purge(value["nonce"])
            return False
        state = self._advance_fleet_content_purge(value["nonce"])
        if state == "ready":
            self._complete_content_purge(owner["chat_id"], owner["lang"], "all", None, value["nonce"], scrub_update=False)
            return True
        return False

    def cancel_fleet_purge(self, nonce):
        value = lifecycle._authorization(self.conn, nonce)
        if value is None or value["phase"] != "prepared":
            return False
        owner = value.get("fleet_owner")
        if not isinstance(owner, dict):
            return False
        owner["abort_requested"] = True
        self._fleet_purge_save(value)
        return self.abort_fleet_purge(nonce)

    def abort_fleet_purge(self, nonce):
        """Release only a cancelled/expired continuation after quiescent cleanup.

        Recovery calls this hook routinely. An active confirmed continuation or
        unexpired renewed card keeps its hold and must never be silently aborted.
        Canonical DB content/results remain; cancelled old requests cannot replay.
        """
        value = lifecycle._authorization(self.conn, nonce)
        if value is None:
            return True
        owner = value.get("fleet_owner")
        if not isinstance(owner, dict) or not owner.get("abort_requested"):
            return False
        counts = {key: n for key, n in owner["confirmed"].items() if key in lifecycle.PREVIEW_KEYS}
        settled = self._fleet_settle_content_calls()
        lifecycle.request_quiescence(self.cfg, nonce, counts)
        if settled != {"status": "settled", "pending_calls": 0} or lifecycle.executor_ack(self.cfg, nonce, counts) is None:
            return False
        lifecycle.clear_app_copies(self.cfg)
        lifecycle.release_hold(self.cfg, nonce)
        store.kv_set(self.conn, "task_purge_authorization", "")
        return True

    def purge_fleet_external_state(self, nonce, timeout_seconds=5):
        """Post-commit two-phase cleanup; timeout is intentionally nonblocking."""
        value = lifecycle._authorization(self.conn, nonce)
        if value is None or value["phase"] != "db_committed":
            return False
        receipt = lifecycle._receipt(value.get("fleet_quiescence"), nonce)
        # Recovery after an already-consumed hold is safe only with the persisted
        # completed phase. It never republishes/replays old requests.
        if value.get("fleet_phase") == "external_done":
            return lifecycle.release_hold(self.cfg, nonce)
        lifecycle.request_quiescence(self.cfg, nonce, receipt["preview"], phase="delete")
        if lifecycle.executor_ack(self.cfg, nonce, receipt["preview"], status="deleted") is None:
            return False
        lifecycle.clear_app_copies(self.cfg)
        # Commit this recoverable completion before releasing the executor hold.
        value["fleet_phase"] = "external_done"
        self._fleet_purge_save(value)
        lifecycle.release_hold(self.cfg, nonce)
        return True
