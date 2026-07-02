#!/usr/bin/env python3
"""Hermes — Cara's business subsystem (in-process).

Cara is one person with two registers. HERMES is her *businesslike* side: the
sharp, efficient assistant mode that runs the work — reminders, notes, files, the
knowledge base, spend, reviews. It is NOT a separate agent, bot, process or memory;
it's a bounded domain (ACTIONS), a distinct VOICE (PERSONA), and the business
handler logic (HermesMixin) — all governed by the one Cara. The companion
(warm/personal/intimate) side stays in `converse.py` / the Agent.

HermesMixin is mixed into the Agent (`class Agent(hermes.HermesMixin)`), so its
methods run on the SAME object: `self` is the Agent, `self.reply` / `self.conn` /
`self.reminder_no` resolve exactly as before. This physically separates the
business code into its own labelled module with ZERO behaviour change — pure
relocation; the router is still the single delegation hop (business -> these
handlers, personal -> the companion).
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fetch
import ingest
import llm
import proactive
import reminders
import relationship
import review
import store
from common import log
from texts import T
from tg_api import TelegramError, tg_send_document
# NOTE: `knowledge` and `persona` are imported LOCALLY inside do_ask/_keyword_context —
# `knowledge` imports `hermes` (for the Hermes register), so a top-level import here would
# be a cycle. Call-time local imports keep it clean.

# The Hermes domain — the business/assistant actions Cara delegates to her work
# register. Routing one of these means "he's working": it sets the businesslike
# resting tone (see Agent._register_state) and is answered in the Hermes voice.
ACTIONS = frozenset({
    "ingest", "reminder_create", "reminder_list", "reminder_cancel",
    "reminder_reschedule", "reminder_rename", "reminder_undo", "list_files",
    "calendar_add", "spend", "budget_set", "stats", "categories", "overview",
    "list_items", "item_detail", "item_delete", "note_edit", "recategorize", "merge_categories",
    "show_media", "vps_stats", "purge", "fetch", "ask", "issues_report",
    "report_problem", "multi_action", "set_journal", "journal_show", "export",
    "working_history", "review",
})

# The Hermes register — the system-prompt persona for any LLM-generated BUSINESS
# reply (the KB `ask`, a fetched-page summary, a review/brief, working history).
# Businesslike Cara: crisp and structured, never flirty/intimate/chatty here — but
# still HER human voice on «ты», never a cold "I'm an AI assistant" disclaimer.
PERSONA = (
    "You are operating as HERMES — Cara's businesslike side: her sharp, efficient work "
    "mode for getting things done. It's the SAME person (you are still Cara, a real woman "
    "on «ты»), just her work register. In this mode:\n"
    "- Be CRISP, structured and factual — lead with the answer, keep it brief and concrete "
    "(the specific fact, date, time, number, name); use a short clean list when it helps.\n"
    "- Stay professional and warm, but NOT flirty, intimate, playful or chatty here — no "
    "endearments, no roleplay, no sharing your own day/feelings; that's for off the clock.\n"
    "- Never call yourself an AI, a bot, a program or 'твой помощник'/'assistant', and never "
    "add disclaimers about how you work — just do the task and report the result plainly.\n"
    "- Answer in the SAME language he used."
)


def is_business(action):
    """True if `action` belongs to the Hermes (business) domain."""
    return action in ACTIONS


class HermesMixin:
    """Cara's remaining business handlers: the knowledge base (ask/fetch), spend/review/export,
    and agreements. The reminder subsystem lives in `reminders_svc.ReminderMixin` and the
    notes/inbox + journals + problem log in `notes_svc.NotesMixin`. Mixed into the Agent, so
    `self` is the Agent — every `self.reply`/`self.conn`/`self.reminder_no`/`self.tz_offset()`
    resolves on it exactly as before. Pure relocation, no behaviour change."""

    # -- knowledge base / remote fetch (stage 3) ------------------------------

    def do_fetch(self, chat_id, lang, params):
        if not self.cfg.fetch_enabled:
            self.reply(chat_id, T(lang, "fetch_disabled"))
            return
        url = str(params.get("url") or "").strip()
        if not url:
            self.reply(chat_id, T(lang, "fetch_no_url"))
            return
        self.reply(chat_id, T(lang, "fetch_reading"), record=False)
        try:
            final_url, title, text = fetch.fetch(
                url, timeout=self.cfg.fetch_timeout, max_bytes=self.cfg.fetch_max_bytes)
        except fetch.FetchError as exc:
            log(f"fetch failed for {url}: {exc}")
            store.issue_add(self.conn, chat_id, "fetch_failed", f"{url}: {exc}")
            key = exc.reason if exc.reason in ("fetch_blocked", "fetch_private") else "fetch_failed"
            self.reply(chat_id, T(lang, key, error=str(exc)) if key == "fetch_failed"
                       else T(lang, key))
            return
        self.ingest_fetched(chat_id, lang, final_url, title, text)

    def ingest_fetched(self, chat_id, lang, url, title, text):
        """Store fetched remote content as an inbox item and suggest a
        category — same suggest-and-confirm flow as a forwarded post."""
        from urllib.parse import urlparse
        source = title or urlparse(url).hostname or "web"
        row_id = store.insert_message(self.conn, {
            "chat_id": chat_id,
            "tg_message_id": -int(datetime.now(timezone.utc).timestamp()),  # synthetic, unique
            "forward_origin_type": "web",
            "forward_origin_title": source[:200],
            "received_at": datetime.now(timezone.utc).isoformat(),
            "raw_text": text,
        })
        if row_id is None:
            return
        store.insert_url(self.conn, row_id, url)
        suggestion = self.suggest_row(store.get_message(self.conn, row_id))
        if not suggestion:
            self.reply(chat_id, T(lang, "stored_retry", row_id=self.note_no(row_id)))
            return
        category, alternatives, summary = suggestion
        counts = T(lang, "counts", row_id=self.note_no(row_id), images=0, files=0, urls=1)
        self.present_suggestion(row_id, chat_id, None, category, alternatives, summary, counts)

    def do_ask(self, chat_id, lang, params, text):
        import knowledge
        import persona
        question = str(params.get("question") or "").strip() or text.strip()
        if not question:
            self.reply(chat_id, T(lang, "clarify"))
            return
        try:
            qvec = llm.embed(self.cfg, self.conn, "ask", [question])[0]
            rows = store.all_embedded_chunks(self.conn)
            context = knowledge.rank_chunks(qvec, rows, self.cfg.ask_top_k,
                                            self.cfg.ask_context_chars)
            if not context:  # nothing indexed/matched -> keyword fallback
                context = self._keyword_context(question)
            hint = persona.boss_preference_hint(self.conn)
            answer = llm.chat_profile(self.cfg, self.conn, "ask",
                                      knowledge.build_ask_messages(question, context, hint),
                                      profile="ask_grounded")
        except llm.BudgetExceeded as exc:
            store.issue_add(self.conn, chat_id, "budget_stop", question[:200])
            self.reply(chat_id, T(lang, "budget_stop", spent=exc.spent, limit=exc.limit,
                                  period=T(lang, f"period_{exc.period}")))
            return
        except llm.LLMError as exc:
            log(f"ask failed: {exc}")
            store.issue_add(self.conn, chat_id, "llm_error", f"ask: {exc}")
            self.reply(chat_id, T(lang, "llm_error"))
            return
        if not context:
            store.issue_add(self.conn, chat_id, "ask_no_context", question[:200])
        self.reply(chat_id, answer.strip()[:4000])

    def _keyword_context(self, question):
        import knowledge
        items = []
        for term in knowledge.salient_terms(question):
            for row in store.list_messages(self.conn, query=term, limit=3):
                if not any(c["message_id"] == row["id"] for c in items):
                    items.append({"message_id": row["id"],
                                  "text": (row["raw_text"] or row["summary"] or "")[:1500],
                                  "category": row["category"] or row["suggested_category"] or "?",
                                  "title": row["forward_origin_title"]})
            if len(items) >= self.cfg.ask_top_k:
                break
        return items[:self.cfg.ask_top_k]

    # -- spend / review / export ----------------------------------------------

    # -- agreements (passive memory of commitments we made) -------------------

    def do_agreement_add(self, chat_id, lang, params, text):
        import agreements
        now = datetime.now(timezone.utc)
        draft = agreements.validate_add(params, now)
        if not draft:  # router gave no clean text -> fall back to the raw message
            draft = agreements.validate_add(
                {"text": params.get("text") or text, "party": params.get("party"),
                 "due_utc": params.get("due_utc")}, now)
        if not draft:
            self.reply(chat_id, T(lang, "agreement_unclear"))
            return
        store.agreement_add(self.conn, chat_id, draft["text"], party=draft["party"],
                            due_utc=draft["due_utc"], source="explicit")
        relationship.log_event(self.conn, "agreement", f"agreed: {draft['text']}", importance=2)
        self.reply(chat_id, T(lang, "agreement_saved", text=draft["text"]))

    def do_agreements_list(self, chat_id, lang):
        import agreements
        rows = store.agreements_open(self.conn, chat_id)
        self.reply(chat_id, agreements.format_list(rows, self.tz_offset(), lang))

    def do_agreement_close(self, chat_id, lang, params, text):
        import agreements
        # "не договаривались" right after Cara surfaced newly auto-captured agreements:
        # cancel exactly the ones she just showed (a mis-heard commitment, corrected on
        # the spot). params.surfaced is set by the router hint keyed on agreements_surfaced_at.
        if params.get("surfaced"):
            # Recency guard (belt-and-suspenders): only act on a JUST-surfaced batch, so a
            # stray/hallucinated {surfaced:true} outside the router's 600s hint window can't
            # cancel a stale batch of ids that lingered after the boss simply accepted them.
            at = store.kv_get(self.conn, "agreements_surfaced_at")
            recent = False
            if at:
                try:
                    from datetime import datetime, timezone
                    recent = (datetime.now(timezone.utc)
                              - datetime.fromisoformat(at)).total_seconds() < 600
                except (TypeError, ValueError):
                    recent = False
            raw = store.kv_get(self.conn, "agreements_surfaced_ids") or ""
            ids = [int(x) for x in raw.split(",") if x.strip().isdigit()] if recent else []
            cancelled = [store.agreement_get(self.conn, i) for i in ids]
            cancelled = [r for r in cancelled if r and r["status"] == "open"]
            for r in cancelled:
                store.agreement_set_status(self.conn, r["id"], "cancelled")
            store.kv_set(self.conn, "agreements_surfaced_ids", "")
            if cancelled:
                self.reply(chat_id, T(lang, "agreement_surfaced_removed", n=len(cancelled)))
            else:
                self.reply(chat_id, T(lang, "agreement_not_found"))
            return
        rows = store.agreements_open(self.conn, chat_id)
        row = agreements.find(rows, params)
        if row is None:
            self.reply(chat_id, T(lang, "agreement_not_found"))
            return
        outcome = str(params.get("outcome") or "kept").strip().lower()
        cancelled = outcome in ("cancel", "cancelled", "отмена", "отменить", "сними", "снять")
        store.agreement_set_status(self.conn, row["id"], "cancelled" if cancelled else "kept")
        self.reply(chat_id, T(lang, "agreement_cancelled" if cancelled else "agreement_kept",
                              text=row["text"]))

    def do_budget_set(self, chat_id, lang, params):
        """Change the AI spend cap on the boss's explicit request — a runtime
        override stored in preferences and enforced by the budget gateway."""
        raw = str(params.get("amount") or "").replace("$", "").replace(",", ".").strip()
        try:
            amount = round(float(raw), 2)
        except (TypeError, ValueError):
            self.reply(chat_id, T(lang, "budget_set_unclear"))
            return
        if not 0 <= amount <= 1000:  # sane bounds; 0 disables the cap
            self.reply(chat_id, T(lang, "budget_set_unclear"))
            return
        period = str(params.get("period") or "day").strip().lower()
        if period in ("month", "monthly", "месяц", "месячный"):
            store.pref_set(self.conn, "budget_monthly_usd", amount)
            plabel = "месяц" if lang == "ru" else "month"
        else:
            store.pref_set(self.conn, "budget_daily_usd", amount)
            plabel = "день" if lang == "ru" else "day"
        log(f"budget override set: {period}=${amount}")
        self.reply(chat_id, T(lang, "budget_set_done", period=plabel, amount=f"{amount:.2f}"))

    def do_review(self, chat_id, lang, params):
        if str(params.get("focus") or "").strip().lower() == "corrections":
            self.reply(chat_id, review.corrections_report(self.conn, lang))
            return
        if params.get("schedule") or str(params.get("when") or "").strip().lower() in (
            "when", "schedule", "next"
        ):
            self.reply(chat_id, self.review_schedule_text(lang))
            return
        period = review.normalize_period(params.get("period"))
        self.reply(chat_id, review.chat_text(self.conn, self.cfg, lang, period))
        if not params.get("export"):
            return
        md = review.markdown(self.conn, self.cfg, period)
        reviews_dir = self.cfg.db_path.parent / "reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        filename = f"cara-review-{period}-{stamp}.md"
        (reviews_dir / filename).write_text(md, encoding="utf-8")
        try:
            tg_send_document(self.cfg.token, chat_id, filename, md.encode("utf-8"),
                             caption=T(lang, "review_file_caption"),
                             content_type="text/markdown")
            store.convo_add(self.conn, chat_id, "bot", f"[review file: {filename}]")
        except TelegramError as exc:
            log(f"review export send failed: {exc}")
            self.reply(chat_id, T(lang, "llm_error"))
        log(f"review exported: {reviews_dir / filename}")

    def do_export(self, chat_id, lang, params):
        what = str(params.get("what") or "review").strip().lower()
        if what in ("last_trace", "trace_timeline", "trace_steps"):
            filename, md = self._last_trace_markdown(chat_id)
            if not md:
                self.reply(chat_id, T(lang, "trace_none"))
                return
        elif what not in review.EXPORT_KINDS:
            filename, md = review.export_document(self.conn, self.cfg, "review", lang,
                                                  params.get("period") or "week")
        else:
            filename, md = review.export_document(self.conn, self.cfg, what, lang,
                                                  params.get("period") or "week")
        exports_dir = self.cfg.db_path.parent / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        (exports_dir / filename).write_text(md, encoding="utf-8")
        try:
            tg_send_document(self.cfg.token, chat_id, filename, md.encode("utf-8"),
                             caption=T(lang, "review_file_caption"), content_type="text/markdown")
            relationship.log_event(self.conn, "export_created",
                                   f"exported {what} to {filename}", importance=1,
                                   title=filename)
        except TelegramError as exc:
            log(f"export send failed: {exc}")
            self.reply(chat_id, T(lang, "llm_error"))
