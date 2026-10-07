#!/usr/bin/env python3
"""Offline unit tests: router, LLM gateway, reminders, spend, texts, memory."""
import gc
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import action_truth
import boss_model
import common
import converse
import events
import fetch
import ingest
import gcal
import jobs
import journals
import knowledge
import llm
import media
import memory_curator
import pdftext
import persona
import relationship
import runtime
import self_model
import reminders
import review
import router
import skill_manifest
import spend
import storage
import store
import sysinfo
import texts
import tg_api
import trace as tracing


from testlib import make_config, setUpModule, tearDownModule

class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_pricing_table_and_cost(self):
        cfg = make_config(PRICING_JSON='{"my-model": [2.0, 4.0]}')
        table = llm.pricing_table(cfg)
        self.assertEqual(table["my-model"], (2.0, 4.0))
        self.assertAlmostEqual(llm.chat_cost("my-model", 1_000_000, 500_000, table), 4.0)
        # unknown model uses the conservative default
        self.assertAlmostEqual(
            llm.chat_cost("mystery", 1_000_000, 0, table), llm.DEFAULT_CHAT_PRICE[0]
        )

    def test_live_models_are_priced_not_defaulted(self):
        # Every model Cara actually runs MUST be in the price table — a missing
        # slug falls through to DEFAULT_CHAT_PRICE ($3/$15) and silently inflates
        # the meter (the 2026-06-19 budget spike). Lock the real DO rates in.
        table = llm.pricing_table(make_config())
        for slug, pair in {
            "deepseek-4-flash": (0.112, 0.224),
            "deepseek-v4-pro": (1.392, 2.784),
            "nemotron-3-nano-omni": (0.50, 0.90),
            "openai-gpt-oss-20b": (0.05, 0.45),
            "kimi-k3": (3.0, 15.0),
            "kimi-k2.6": (0.76, 3.20),
        }.items():
            self.assertEqual(table.get(slug), pair, slug)
            self.assertIn(slug, table)

    def test_budget_states(self):
        cfg = make_config(BUDGET_DAILY_USD="1.0", BUDGET_MONTHLY_USD="100")
        self.assertEqual(llm.budget_state(cfg, self.conn)[0], "ok")
        store.usage_add(self.conn, "ingest", "chat", "m", 1, 1, cost_usd=0.85)
        state, period, spent, limit = llm.budget_state(cfg, self.conn)
        self.assertEqual((state, period), ("warn", "day"))
        store.usage_add(self.conn, "ingest", "chat", "m", 1, 1, cost_usd=0.2)
        state, period, spent, limit = llm.budget_state(cfg, self.conn)
        self.assertEqual((state, period), ("stop", "day"))
        self.assertGreaterEqual(spent, 1.0)
        with self.assertRaises(llm.BudgetExceeded):
            llm.chat(cfg, self.conn, "ingest", [])

    def test_usage_total_and_breakdown(self):
        store.usage_add(self.conn, "router", "chat", "m1", 100, 50, cost_usd=0.01)
        store.usage_add(self.conn, "ingest", "chat", "m1", 200, 100, cost_usd=0.05)
        store.usage_add(self.conn, "stt", "stt", "whisper", seconds=30, cost_usd=0.003)
        self.assertAlmostEqual(store.usage_total(self.conn, "day"), 0.063)
        self.assertAlmostEqual(store.usage_total(self.conn, "month"), 0.063)
        by_skill = {r["k"]: r["cost"] for r in store.usage_breakdown(self.conn, "day", "skill")}
        self.assertAlmostEqual(by_skill["ingest"], 0.05)
        by_model = {r["k"]: r["calls"] for r in store.usage_breakdown(self.conn, "month", "model")}
        self.assertEqual(by_model["m1"], 2)

    def test_local_stt_config_and_dispatch(self):
        cfg = make_config(STT_MODE="local", WHISPER_BIN="/x/whisper-cli",
                          WHISPER_MODEL="/x/model.bin")
        self.assertEqual(cfg.stt_mode, "local")
        self.assertEqual(cfg.stt_local_timeout, 600)
        # local mode must not touch the remote endpoint nor the budget
        with mock.patch.object(llm, "_transcribe_local", return_value="привет") as local_mock:
            text = llm.transcribe(cfg, self.conn, "stt", "/tmp/v.oga", 5)
        self.assertEqual(text, "привет")
        local_mock.assert_called_once()
        remote_cfg = make_config()
        self.assertEqual(remote_cfg.stt_mode, "remote")

    def test_local_stt_missing_tool_raises_llmerror(self):
        cfg = make_config(STT_MODE="local", WHISPER_BIN="/nonexistent/whisper-cli")
        import subprocess
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError("ffmpeg")):
            with self.assertRaises(llm.LLMError):
                llm._transcribe_local(cfg, self.conn, "stt", "/tmp/v.oga", 5)
        self.assertEqual(store.usage_total(self.conn, "day"), 0)  # nothing logged on failure

    def test_profiles_and_override(self):
        cfg = make_config()
        profs = llm.profiles(cfg)
        self.assertEqual(profs["router_fast"]["primary"], cfg.router_model)
        self.assertTrue(profs["router_fast"]["json_required"])
        cfg2 = make_config(LLM_PROFILES_JSON='{"router_fast": {"max_tokens": 99}}')
        self.assertEqual(llm.profiles(cfg2)["router_fast"]["max_tokens"], 99)


    def test_chat_profile_failover_and_cooldown(self):
        cfg = make_config()
        calls = []

        fb = llm.default_profiles(cfg)["router_fast"]["fallbacks"][0]  # the default fallback slug

        def fake_chat(c, conn, skill, messages, max_tokens=300, model=None, temperature=0, **kw):
            calls.append(model)
            if model == cfg.router_model:
                raise llm.LLMError("primary down")
            return '{"action": "spend", "params": {}, "confidence": 0.9}'
        with mock.patch.object(llm, "chat", side_effect=fake_chat):
            out = llm.chat_profile(cfg, self.conn, "router", [], profile="router_fast")
        self.assertIn("spend", out)
        self.assertEqual(calls, [cfg.router_model, fb])  # primary then fallback
        self.assertTrue(store.cooldown_active(self.conn, "router_fast", cfg.router_model))
        # next call skips the cooled-down primary
        calls.clear()
        with mock.patch.object(llm, "chat", side_effect=fake_chat):
            llm.chat_profile(cfg, self.conn, "router", [], profile="router_fast")
        self.assertEqual(calls, [fb])

    def test_default_fallback_is_tier_accessible(self):
        # The default fallback must NOT be the tier-403 openai-gpt-4o (a dead fallback on
        # a fresh deploy) — it's an open-weight slug that's actually reachable AND priced.
        fb = llm.default_profiles(make_config())["router_fast"]["fallbacks"]
        self.assertNotIn("openai-gpt-4o", fb)
        for slug in fb:
            self.assertIn(slug, llm.DEFAULT_PRICING, slug)

    def test_profile_without_primary_is_repaired(self):
        # A brand-new LLM_PROFILES_JSON profile without a "primary" would KeyError in
        # chat_profile (not an LLMError) and crash the turn — profiles() backfills one.
        cfg = make_config(LLM_PROFILES_JSON='{"weird_new": {"max_tokens": 50}}')
        prof = llm.profiles(cfg)["weird_new"]
        self.assertEqual(prof["primary"], cfg.do_model)

    def test_chat_estimates_usage_when_provider_omits_it(self):
        # A response with no usage block must be metered from text length, not logged as
        # $0 — an unmetered model silently under-counts the budget.
        cfg = make_config()
        body = {"choices": [{"message": {"content": "a fairly long reply " * 10}}]}  # no "usage"

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(body).encode("utf-8")
        with mock.patch.object(llm, "urlopen", return_value=Resp()):
            llm.chat(cfg, self.conn, "converse", [{"role": "user", "content": "x" * 400}],
                     model="deepseek-4-flash")
        row = self.conn.execute("SELECT tokens_in, tokens_out, cost_usd FROM llm_usage").fetchone()
        self.assertGreater(row["tokens_in"], 0)   # estimated from the 400-char prompt
        self.assertGreater(row["tokens_out"], 0)  # estimated from the reply length
        self.assertGreater(row["cost_usd"], 0)

    def test_chat_meters_before_no_choices_error(self):
        # A billed-but-empty (no-choices) response still bills the provider — it must be
        # metered even though chat() then raises.
        cfg = make_config()
        body = {"choices": [], "usage": {"prompt_tokens": 123, "completion_tokens": 0}}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(body).encode("utf-8")
        with mock.patch.object(llm, "urlopen", return_value=Resp()):
            with self.assertRaises(llm.LLMError):
                llm.chat(cfg, self.conn, "converse", [], model="deepseek-4-flash")
        row = self.conn.execute("SELECT tokens_in FROM llm_usage").fetchone()
        self.assertEqual(row["tokens_in"], 123)  # metered despite the raise

    def test_chat_profile_budget_never_falls_back(self):
        cfg = make_config(BUDGET_DAILY_USD="0.01")
        store.usage_add(self.conn, "x", "chat", "m", 1, 1, cost_usd=0.02)  # over budget
        with mock.patch.object(llm, "chat",
                               side_effect=llm.BudgetExceeded("day", 0.02, 0.01)):
            with self.assertRaises(llm.BudgetExceeded):
                llm.chat_profile(cfg, self.conn, "router", [], profile="router_fast")

    def test_chat_profile_json_required_tries_fallback(self):
        cfg = make_config()
        outs = iter(["not json at all", '{"ok": true}'])

        def fake_chat(c, conn, skill, messages, max_tokens=300, model=None, temperature=0, **kw):
            return next(outs)
        with mock.patch.object(llm, "chat", side_effect=fake_chat):
            out = llm.chat_profile(cfg, self.conn, "router", [], profile="router_fast")
        self.assertEqual(out, '{"ok": true}')  # fell through to JSON-clean fallback

    def test_transcribe_local_server(self):
        cfg = make_config(STT_MODE="local_server", WHISPER_SERVER_URL="http://127.0.0.1:8089")
        self.assertEqual(cfg.stt_mode, "local_server")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "v.oga"
            p.write_bytes(b"OGGDATA")

            class Resp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return b'{"text": "  \xd0\xbd\xd0\xb0\xd0\xbf\xd0\xbe\xd0\xbc\xd0\xbd\xd0\xb8  "}'
            with mock.patch.object(llm, "urlopen", return_value=Resp()) as up:
                text = llm.transcribe(cfg, self.conn, "stt", str(p), 4)
            self.assertEqual(text, "напомни")  # JSON {"text"} parsed + trimmed
            self.assertIn("/inference", up.call_args[0][0].full_url)  # warm-server endpoint
            self.assertEqual(store.usage_total(self.conn, "day"), 0.0)  # on-box, free

    def test_build_multipart(self):
        body, boundary = llm.build_multipart(
            {"model": "whisper"}, "file", "voice.oga", b"AUDIO", "audio/ogg"
        )
        self.assertIn(boundary.encode(), body)
        self.assertIn(b'name="model"\r\n\r\nwhisper', body)
        self.assertIn(b'filename="voice.oga"', body)
        self.assertIn(b"AUDIO", body)
        self.assertTrue(body.endswith(f"--{boundary}--\r\n".encode()))


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "test.db")
        self.cfg = make_config()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_validate_route(self):
        self.assertIsNone(router.validate_route(None, False))
        self.assertIsNone(router.validate_route({"action": "chat"}, False))  # not in enum
        self.assertIsNone(router.validate_route({"action": "confirm"}, False))  # pending-only
        ok = router.validate_route({"action": "confirm", "params": {}}, True)
        self.assertEqual(ok["action"], "confirm")
        clamped = router.validate_route({"action": "spend", "confidence": 7}, False)
        self.assertEqual(clamped["confidence"], 1.0)
        defaulted = router.validate_route({"action": "spend", "params": "junk"}, False)
        self.assertEqual(defaulted["params"], {})
        self.assertEqual(defaulted["confidence"], 0.5)

    def test_short_review_export_bypasses_router_model(self):
        for phrase in ("Давай md", "пришли .md", "send the md"):
            with self.subTest(phrase=phrase), mock.patch.object(
                    llm, "chat_profile") as chat_profile:
                decision = router.route(self.cfg, self.conn, 1, phrase, None)
            chat_profile.assert_not_called()
            self.assertEqual(decision, {
                "action": "review",
                "params": {"period": "week", "export": True,
                           "resolved_issue_detail": phrase},
                "confidence": 1.0,
            })
        self.assertFalse(router.detect_review_export("давай обсудим markdown"))

    def test_system_prompt_mentions_pending_and_timezone(self):
        prompt = router.build_system_prompt(self.cfg, None)
        self.assertIn("NO pending action", prompt)
        self.assertIn("UTC+3", prompt)
        pending = {"kind": "reminder", "payload": {"title": "x"}}
        prompt2 = router.build_system_prompt(self.cfg, pending)
        self.assertIn("pending action awaiting", prompt2)
        self.assertIn("reminder", prompt2)

    def test_hermes_domain_is_business_only(self):
        import hermes, tg_ingest_agent
        # Business actions belong to Hermes; personal/companion ones never do.
        for a in ("reminder_create", "ask", "ingest", "spend", "review"):
            self.assertTrue(hermes.is_business(a))
        for a in ("converse", "smalltalk", "meeting_start", "cara_selfie", "persona"):
            self.assertFalse(hermes.is_business(a))
        # The dispatcher's business-register set IS the Hermes domain (single source).
        self.assertIs(tg_ingest_agent.Agent.BUSINESS_REGISTER_ACTIONS, hermes.ACTIONS)

    def test_business_handlers_relocated_to_domain_mixins(self):
        # The extraction (#2): business handlers physically live in their DOMAIN mixin — the
        # reminder subsystem in reminders_svc.ReminderMixin, the notes/inbox+journals+problem
        # log in notes_svc.NotesMixin, KB/fetch + spend/review/export + agreements in
        # hermes.HermesMixin — are NOT duplicated on Agent, yet resolve on Agent via inheritance.
        import hermes, reminders_svc, notes_svc, tg_ingest_agent
        self.assertTrue(issubclass(tg_ingest_agent.Agent, hermes.HermesMixin))
        self.assertTrue(issubclass(tg_ingest_agent.Agent, reminders_svc.ReminderMixin))
        self.assertTrue(issubclass(tg_ingest_agent.Agent, notes_svc.NotesMixin))
        reminder_methods = (
            "do_reschedule", "do_rename_reminder", "_resolve_reminder_target",
            "_resolve_reminder_op", "_parse_reminder_selector", "do_reminder_undo",
            "continue_partial_reminder", "start_partial_reminder", "_note_reminder_title",
            "_remember_reminder", "_reminder_list_body", "_parse_fired_followup",
            "resolve_fired_followup",
            "fire_due_reminders", "check_reminder_expiry", "reminder_no")
        notes_methods = (
            "do_report_problem", "do_set_journal", "_journal_since", "do_journal_show",
            "_journal_page",
            "stats_text", "overview_text", "_note_line", "_notes_page",
            "_notes_page_keyboard", "do_list_items", "do_show_media", "do_discard",
            "_purge_impact_text", "do_purge", "resolve_purge", "resolve_items", "resolve_item",
            "note_no", "item_detail_text", "do_item_detail", "do_recategorize", "do_note_edit",
            "do_merge_categories", "issues_text", "files_text", "categories_text", "do_item_delete")
        hermes_methods = (
            "do_ask", "do_fetch", "ingest_fetched", "_keyword_context",
            "do_budget_set", "do_review", "do_export")
        for name in reminder_methods:
            self.assertIn(name, reminders_svc.ReminderMixin.__dict__)  # in the reminder mixin
            self.assertNotIn(name, hermes.HermesMixin.__dict__)
        for name in notes_methods:
            self.assertIn(name, notes_svc.NotesMixin.__dict__)         # in the notes mixin
            self.assertNotIn(name, hermes.HermesMixin.__dict__)        # moved out of hermes
        for name in hermes_methods:
            self.assertIn(name, hermes.HermesMixin.__dict__)           # still in hermes
        for name in reminder_methods + notes_methods + hermes_methods:
            self.assertNotIn(name, tg_ingest_agent.Agent.__dict__)     # not duplicated on Agent
            self.assertTrue(hasattr(tg_ingest_agent.Agent, name))      # available via the mixin

    def test_ordinal_reschedule_routes_to_action_not_converse(self):
        import converse
        # "перенеси первое/его на TIME" must reschedule (the action), not fall to converse.
        self.assertIn("перенеси первое на 12:16", router.ROUTER_EXAMPLES)
        self.assertIn("перенеси его на 12:20", router.ROUTER_EXAMPLES)
        self.assertIn("move verb + a time is ALWAYS reminder_reschedule", router.ROUTER_EXAMPLES)
        # and the persona must not invent a fake "system won't allow" limitation
        self.assertIn("система не даст", converse.CHARACTER)

    def test_reminder_status_question_steers_to_converse(self):
        # "почему не закрыла #1?" must NOT route to ask (notes) — it's about her own
        # reminders, answered in converse from the real reminder list.
        prompt = router.build_system_prompt(self.cfg, None)
        self.assertIn("HER REMINDERS", prompt)
        self.assertIn("почему не закрыла #1", router.ROUTER_EXAMPLES)

    def test_detect_smalltalk(self):
        self.assertEqual(router.detect_smalltalk("кто ты?"), "who_are_you")
        self.assertEqual(router.detect_smalltalk("Are you human?"), "who_are_you")
        self.assertEqual(router.detect_smalltalk("Привет!"), "hello")
        self.assertEqual(router.detect_smalltalk("  hi"), "hello")
        self.assertEqual(router.detect_smalltalk("Спасибо"), "thanks")
        self.assertEqual(router.detect_smalltalk("как дела?"), "how_are_you")
        self.assertEqual(router.detect_smalltalk("ок"), "ack")
        self.assertIsNone(router.detect_smalltalk("привет, поставь напоминание"))
        self.assertIsNone(router.detect_smalltalk(""))
        ok = router.validate_route({"action": "smalltalk", "params": {"kind": "hello"}}, False)
        self.assertEqual(ok["action"], "smalltalk")
        # (detect_smalltalk still classifies to short-circuit to warm converse; the old
        # smalltalk_* reply templates were removed 2026-07-02 — the reply is free-form.)

    def test_route_happy_path_and_guards(self):
        with mock.patch.object(llm, "chat",
                               return_value='{"action": "spend", "params": {"period": "month"}, "confidence": 0.9}'):
            decision = router.route(self.cfg, self.conn, 1, "сколько потратили?", None)
        self.assertEqual(decision["action"], "spend")
        # garbage output degrades to clarify, never crashes
        with mock.patch.object(llm, "chat", return_value="I think you want..."):
            decision = router.route(self.cfg, self.conn, 1, "hmm", None)
        self.assertEqual(decision["action"], "clarify")
        # low-confidence guesses now drop to warm converse (not a cold clarify)
        with mock.patch.object(llm, "chat",
                               return_value='{"action": "reminder_create", "params": {}, "confidence": 0.3}'):
            decision = router.route(self.cfg, self.conn, 1, "что-то", None)
        self.assertEqual(decision["action"], "converse")
        # confirm without pending is invalid -> clarify
        with mock.patch.object(llm, "chat",
                               return_value='{"action": "confirm", "params": {}, "confidence": 0.9}'):
            decision = router.route(self.cfg, self.conn, 1, "да", None)
        self.assertEqual(decision["action"], "clarify")


class RemindersTests(unittest.TestCase):
    def test_parse_iso_utc(self):
        parsed = reminders.parse_iso_utc("2026-06-13T07:00:00Z")
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.hour, 7)
        self.assertIsNone(reminders.parse_iso_utc("tomorrow"))
        naive = reminders.parse_iso_utc("2026-06-13T07:00:00")
        self.assertEqual(naive.tzinfo, timezone.utc)

    def test_validate_draft(self):
        now = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
        draft = reminders.validate_draft(
            {"title": "  call bank ", "due_utc": "2026-06-13T07:00:00Z", "recurrence": "WEEKLY"},
            now,
        )
        self.assertEqual(draft["title"], "call bank")
        self.assertEqual(draft["recurrence"], "weekly")
        self.assertIsNone(reminders.validate_draft({"title": "x", "due_utc": "2020-01-01T00:00:00Z"}, now))
        self.assertIsNone(reminders.validate_draft({"title": "", "due_utc": "2026-06-13T07:00:00Z"}, now))
        bad_rec = reminders.validate_draft(
            {"title": "x", "due_utc": "2026-06-13T07:00:00Z", "recurrence": "hourly"}, now
        )
        self.assertEqual(bad_rec["recurrence"], "none")
        # A DAILY reminder whose time-of-day already passed today rolls to the next
        # occurrence instead of being rejected as past (the "ежедневно на 22:00" loop).
        rolled = reminders.validate_draft(
            {"title": "благодарности", "due_utc": "2026-06-12T07:00:00Z", "recurrence": "daily"}, now)
        self.assertIsNotNone(rolled)
        self.assertEqual(rolled["recurrence"], "daily")
        self.assertGreater(reminders.parse_iso_utc(rolled["due_utc"]), now)   # future, not rejected
        # a one-shot in the past is still unusable
        self.assertIsNone(reminders.validate_draft(
            {"title": "x", "due_utc": "2026-06-12T07:00:00Z", "recurrence": "none"}, now))

    def test_time_only_request_is_deterministic_and_rolls_forward(self):
        now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)  # 15:00 at UTC+3
        parsed = reminders.parse_time_only_request("Напомни в 21:15", 3, now)
        self.assertEqual(reminders.parse_iso_utc(parsed["due_utc"]),
                         datetime(2026, 7, 15, 18, 15, tzinfo=timezone.utc))
        later = datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc)  # 22:00 local
        rolled = reminders.parse_time_only_request("remind me at 21:15", 3, later)
        self.assertEqual(reminders.parse_iso_utc(rolled["due_utc"]),
                         datetime(2026, 7, 16, 18, 15, tzinfo=timezone.utc))
        # A request that already contains the subject belongs to the full router.
        self.assertIsNone(reminders.parse_time_only_request(
            "Напомни в 21:15 зарядить тройку", 3, now))

    def test_forwarded_reminder_wording_becomes_title_data(self):
        self.assertEqual(reminders.title_from_forward(
            "Напомни пожалуйста вечером у тебя зарядить тройку"), "зарядить тройку")
        self.assertEqual(reminders.title_from_forward(
            "Remind me please tonight to charge the card"), "charge the card")
        self.assertEqual(reminders.title_from_forward("Встреча с Наталией"),
                         "Встреча с Наталией")

    def test_next_due(self):
        now = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
        self.assertIsNone(reminders.next_due("2026-06-12T07:00:00Z", "none", now))
        daily = reminders.parse_iso_utc(reminders.next_due("2026-06-12T07:00:00Z", "daily", now))
        self.assertEqual(daily, datetime(2026, 6, 13, 7, 0, tzinfo=timezone.utc))
        weekly = reminders.parse_iso_utc(reminders.next_due("2026-06-12T07:00:00Z", "weekly", now))
        self.assertEqual(weekly, datetime(2026, 6, 19, 7, 0, tzinfo=timezone.utc))

    def test_roll_forward_past_to_future(self):
        now = datetime(2026, 6, 24, 1, 0, tzinfo=timezone.utc)
        # 'today 12:00' that the router misdated to yesterday -> rolls to the next noon (future)
        rolled = reminders.roll_forward(datetime(2026, 6, 23, 9, 0, tzinfo=timezone.utc), now)
        self.assertGreater(rolled, now)
        self.assertEqual(rolled.hour, 9)                       # local time-of-day preserved
        self.assertEqual(rolled.date(), now.date())            # next occurrence = today
        future = datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(reminders.roll_forward(future, now), future)  # already future -> unchanged

    def test_fmt_local_and_find(self):
        self.assertEqual(reminders.fmt_local("2026-06-13T07:00:00Z", 3), "2026-06-13 10:00")
        rows = [
            {"id": 1, "title": "позвонить в банк", "due_utc": "2026-06-13T07:00:00Z", "recurrence": "none"},
            {"id": 2, "title": "report", "due_utc": "2026-06-14T07:00:00Z", "recurrence": "weekly"},
        ]
        self.assertEqual(reminders.find_by_query(rows, {"id": 2})["id"], 2)
        self.assertEqual(reminders.find_by_query(rows, {"title_query": "БАНК"})["id"], 1)
        self.assertIsNone(reminders.find_by_query(rows, {"title_query": "nothing"}))
        self.assertIsNone(reminders.find_by_query(rows, {}))
        listing = reminders.format_list(rows, 3, "ru")
        self.assertIn("#1 2026-06-13 10:00", listing)
        self.assertIn("еженедельно", listing)

    def test_list_marks_fired_and_overdue(self):
        now = datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)
        rows = [
            # a one-shot that already fired but wasn't confirmed -> still open
            {"id": 1, "title": "пиво", "due_utc": "2026-06-22T18:31:00Z",
             "recurrence": "none", "last_fired_at": "2026-06-22T18:31:05Z"},
            # a future one-shot -> no marker
            {"id": 2, "title": "Азербайджан", "due_utc": "2026-06-24T15:00:00Z",
             "recurrence": "none", "last_fired_at": None},
        ]
        # a reschedule-moved one-shot (re-armed, future) -> 'перенесено', NOT a warning
        rows.append({"id": 3, "title": "Рим", "due_utc": "2026-06-25T15:00:00Z",
                     "recurrence": "none", "last_fired_at": None,
                     "prev_due_utc": "2026-06-22T10:00:00Z"})
        out = reminders.format_list(rows, 3, "ru", now=now)
        self.assertIn("ждёт «готово»", out)          # fired one-shot is marked
        self.assertNotIn("просрочено", out)           # the fired one isn't double-marked
        self.assertIn("🔄 перенесено", out)           # the rescheduled one shows re-scheduled
        self.assertNotIn("Азербайджан — ⚠️", out)     # the fresh future reminder has no marker
        self.assertNotIn("Рим — ⚠️", out)             # rescheduled is NOT a ⚠️ warning
        # status helper directly: fired / rescheduled / clean (each with its own icon)
        self.assertEqual(reminders.reminder_status_mark(rows[0], "en", now), '⚠️ fired, awaiting "done"')
        self.assertEqual(reminders.reminder_status_mark(rows[1], "en", now), "")
        self.assertEqual(reminders.reminder_status_mark(rows[2], "en", now), "🔄 rescheduled")
        # a RECURRING reminder re-arms (prev_due_utc set) every fire — that is NOT 'перенесено'
        recurring = {"id": 4, "title": "благодарности", "due_utc": "2026-06-25T19:00:00Z",
                     "recurrence": "daily", "last_fired_at": None,
                     "prev_due_utc": "2026-06-24T19:00:00Z"}
        self.assertEqual(reminders.reminder_status_mark(recurring, "en", now), "")


class SpendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "test.db")
        self.cfg = make_config()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_normalize_period(self):
        self.assertEqual(spend.normalize_period("today"), "day")
        self.assertEqual(spend.normalize_period("сегодня"), "day")
        self.assertEqual(spend.normalize_period("неделя"), "week")
        self.assertEqual(spend.normalize_period(None), "month")
        self.assertEqual(spend.normalize_period("garbage"), "month")

    def test_format_spend(self):
        self.assertIn("No AI spend", spend.format_spend(self.conn, "day", self.cfg, "en"))
        store.usage_add(self.conn, "router", "chat", "m1", 100, 50, cost_usd=0.012)
        store.usage_add(self.conn, "ingest", "chat", "m2", 500, 200, cost_usd=0.03)
        report_ru = spend.format_spend(self.conn, "month", self.cfg, "ru")
        self.assertIn("$0.042", report_ru)
        self.assertIn("ingest", report_ru)
        self.assertIn("Бюджет", report_ru)
        report_en = spend.format_spend(self.conn, "day", self.cfg, "en")
        self.assertIn("By model:", report_en)
        self.assertIn("m2", report_en)


class CalendarTests(unittest.TestCase):
    EVENT = {"uid": "reminder-5", "title": "Call; bank, now\nplease",
             "start_utc": "2026-06-13T07:00:00+00:00", "duration_minutes": 45,
             "recurrence": "weekly"}

    def test_make_ics(self):
        now = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
        ics = gcal.make_ics([self.EVENT], now)
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertIn("UID:reminder-5@tg-ingest-agent", ics)
        self.assertIn("DTSTART:20260613T070000Z", ics)
        self.assertIn("DTEND:20260613T074500Z", ics)
        self.assertIn("DTSTAMP:20260612T100000Z", ics)
        self.assertIn(r"SUMMARY:Call\; bank\, now\nplease", ics)
        self.assertIn("RRULE:FREQ=WEEKLY", ics)
        self.assertTrue(ics.endswith("END:VCALENDAR\r\n"))
        one_shot = gcal.make_ics([dict(self.EVENT, recurrence="none")], now)
        self.assertNotIn("RRULE", one_shot)

    def test_event_payload_and_from_reminder(self):
        payload = gcal.build_event_payload(self.EVENT)
        self.assertEqual(payload["start"]["dateTime"], "2026-06-13T07:00:00+00:00")
        self.assertEqual(payload["end"]["dateTime"], "2026-06-13T07:45:00+00:00")
        self.assertEqual(payload["recurrence"], ["RRULE:FREQ=WEEKLY"])
        row = {"id": 7, "title": "x", "due_utc": "2026-06-13T07:00:00+00:00",
               "recurrence": "daily"}
        event = gcal.event_from_reminder(row, 30)
        self.assertEqual(event["uid"], "reminder-7")
        self.assertEqual(event["recurrence"], "daily")

    def test_jwt_unsigned(self):
        import base64, json as jsonlib
        unsigned = gcal.build_jwt_unsigned("sa@project.iam.gserviceaccount.com", 1_000_000)
        header_b64, claims_b64 = unsigned.split(b".")
        pad = b"=" * (-len(claims_b64) % 4)
        claims = jsonlib.loads(base64.urlsafe_b64decode(claims_b64 + pad))
        self.assertEqual(claims["iss"], "sa@project.iam.gserviceaccount.com")
        self.assertEqual(claims["exp"] - claims["iat"], 3600)
        self.assertEqual(claims["aud"], gcal.GOOGLE_TOKEN_URI)
        self.assertIn("calendar.events", claims["scope"])

    def test_configured(self):
        cfg = make_config()
        self.assertFalse(gcal.configured(cfg))  # no calendar id, no key file
        cfg.gcal_calendar_id = "me@gmail.com"
        self.assertFalse(gcal.configured(cfg))  # key file still missing

    def test_auth_failures_raise_calendar_error_not_raw(self):
        # Regression: an unreadable/malformed key or a bare socket timeout used
        # to escape as OSError/ValueError/TimeoutError — NOT CalendarError — so
        # send_to_calendar's `except CalendarError` was skipped, the promised
        # .ics fallback never engaged, and the boss got total silence.
        with tempfile.TemporaryDirectory() as tmp:
            conn = store.open_db(Path(tmp) / "t.db")
            cfg = make_config()
            cfg.gcal_calendar_id = "me@gmail.com"
            try:
                # 1) key file missing entirely
                cfg.gcal_key_file = str(Path(tmp) / "absent.json")
                with self.assertRaises(gcal.CalendarError):
                    gcal.get_access_token(cfg, conn)
                # 2) key file present but not JSON (also covers PermissionError
                #    -> OSError: same except clause)
                bad = Path(tmp) / "bad.json"
                bad.write_text("not json", encoding="utf-8")
                cfg.gcal_key_file = str(bad)
                with self.assertRaises(gcal.CalendarError):
                    gcal.get_access_token(cfg, conn)
                # 3) valid JSON but missing the SA fields
                bad.write_text('{"type": "service_account"}', encoding="utf-8")
                with self.assertRaises(gcal.CalendarError):
                    gcal.get_access_token(cfg, conn)
                # 4) bare socket read-timeout during the token call (raw
                #    TimeoutError is NOT a URLError)
                good = Path(tmp) / "sa.json"
                good.write_text(json.dumps({"client_email": "sa@p.iam.gserviceaccount.com",
                                            "private_key": "PEM"}), encoding="utf-8")
                cfg.gcal_key_file = str(good)
                import http.client
                for fault in (TimeoutError("read"), http.client.IncompleteRead(b"x")):
                    with mock.patch.object(gcal, "_sign_rs256", return_value=b"sig"), \
                            mock.patch.object(gcal, "urlopen", side_effect=fault):
                        with self.assertRaises(gcal.CalendarError, msg=repr(fault)):
                            gcal.get_access_token(cfg, conn)
            finally:
                conn.close()

    def test_router_accepts_calendar_add(self):
        ok = router.validate_route({"action": "calendar_add", "params": {"title_query": "банк"}}, False)
        self.assertEqual(ok["action"], "calendar_add")


