#!/usr/bin/env python3
"""Offline unit tests: router, LLM gateway, reminders, spend, texts, memory."""
import os
import sys
import tempfile
import unittest
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
import gcal
import jobs
import knowledge
import llm
import memory_curator
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
import trace as tracing


def make_config(**overrides):
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "ALLOWED_CHAT_IDS": "111",
        "DO_MODEL_ACCESS_KEY": "do-key",
    }
    env.update(overrides)
    return common.load_config(env)


class TextsTests(unittest.TestCase):
    def test_every_key_has_both_languages(self):
        for key, entry in texts.TEXTS.items():
            self.assertIn("ru", entry, key)
            self.assertIn("en", entry, key)

    def test_formatting_and_fallback(self):
        self.assertIn("news", texts.T("ru", "confirmed", category="news", row_id=5))
        self.assertIn("news", texts.T("en", "confirmed", category="news", row_id=5))
        # unknown language falls back to English
        self.assertEqual(texts.T("de", "cancelled"), texts.T("en", "cancelled"))

    def test_personalized_templates_take_name(self):
        for key, kwargs in (
            ("start", {"name": "Олег"}),
            ("reminder_fired", {"name": "Олег", "title": "позвонить"}),
            ("issues_weekly_intro", {"name": "Олег"}),
        ):
            for lang in ("ru", "en"):
                self.assertIn("Олег", texts.T(lang, key, **kwargs))


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
            "deepseek-4-flash": (0.14, 0.28),
            "deepseek-v4-pro": (1.74, 3.48),
            "nemotron-3-nano-omni": (0.50, 0.90),
            "openai-gpt-oss-20b": (0.05, 0.45),
            "kimi-k2.6": (0.95, 4.0),
        }.items():
            self.assertEqual(table.get(slug), pair, slug)
            self.assertNotEqual(table.get(slug), llm.DEFAULT_CHAT_PRICE, slug)

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

        def fake_chat(c, conn, skill, messages, max_tokens=300, model=None, temperature=0):
            calls.append(model)
            if model == cfg.router_model:
                raise llm.LLMError("primary down")
            return '{"action": "spend", "params": {}, "confidence": 0.9}'
        with mock.patch.object(llm, "chat", side_effect=fake_chat):
            out = llm.chat_profile(cfg, self.conn, "router", [], profile="router_fast")
        self.assertIn("spend", out)
        self.assertEqual(calls, [cfg.router_model, "openai-gpt-4o"])  # primary then fallback
        self.assertTrue(store.cooldown_active(self.conn, "router_fast", cfg.router_model))
        # next call skips the cooled-down primary
        calls.clear()
        with mock.patch.object(llm, "chat", side_effect=fake_chat):
            llm.chat_profile(cfg, self.conn, "router", [], profile="router_fast")
        self.assertEqual(calls, ["openai-gpt-4o"])

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

        def fake_chat(c, conn, skill, messages, max_tokens=300, model=None, temperature=0):
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

    def test_business_handlers_relocated_to_hermes_mixin(self):
        # The extraction (#2): the business handlers physically live in hermes.HermesMixin,
        # are NOT duplicated on Agent, yet still resolve on Agent via inheritance.
        import hermes, tg_ingest_agent
        self.assertTrue(issubclass(tg_ingest_agent.Agent, hermes.HermesMixin))
        for name in ("do_reschedule", "do_rename_reminder", "_resolve_reminder_target",
                     "_resolve_reminder_op", "_parse_reminder_selector", "do_reminder_undo",
                     "continue_partial_reminder", "start_partial_reminder", "_note_reminder_title",
                     "do_journal_show", "do_set_journal", "do_report_problem",
                     # stage 2 — notes/inbox
                     "stats_text", "overview_text", "items_text", "do_show_media", "do_discard",
                     "do_purge", "resolve_purge", "resolve_item", "resolve_items", "note_no",
                     "item_detail_text", "do_item_detail", "do_recategorize", "do_merge_categories",
                     "issues_text", "files_text", "categories_text",
                     # stage 3 — KB / fetch
                     "do_ask", "do_fetch", "ingest_fetched", "_keyword_context"):
            self.assertIn(name, hermes.HermesMixin.__dict__)         # physically in hermes
            self.assertNotIn(name, tg_ingest_agent.Agent.__dict__)   # not duplicated on Agent
            self.assertTrue(hasattr(tg_ingest_agent.Agent, name))    # still available via the mixin

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
        for kind in router.SMALLTALK_KINDS:
            for lang in ("ru", "en"):
                self.assertTrue(texts.T(lang, f"smalltalk_{kind}", name="X"))

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

    def test_router_accepts_calendar_add(self):
        ok = router.validate_route({"action": "calendar_add", "params": {"title_query": "банк"}}, False)
        self.assertEqual(ok["action"], "calendar_add")


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "test.db")
        self.cfg = make_config()
        row = self.conn.execute(
            "INSERT INTO messages (chat_id, tg_message_id, received_at, raw_text,"
            " suggested_category, category, status) VALUES (1, 1, ?, 'x', 'news', 'крипта',"
            " 'confirmed')",
            (datetime.now(timezone.utc).isoformat(),),
        )
        self.conn.commit()
        store.ensure_category(self.conn, "крипта")
        store.feedback_add(self.conn, "ingest", "x", "news", "крипта")
        store.issue_add(self.conn, 1, "out_of_scope", "напиши эссе")
        store.usage_add(self.conn, "router", "chat", "m", 100, 50, cost_usd=0.002)
        store.reminder_add(self.conn, 1, "call",
                           (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_normalize_period(self):
        self.assertEqual(review.normalize_period("неделя"), "week")
        self.assertEqual(review.normalize_period("today"), "day")
        self.assertEqual(review.normalize_period(None), "week")
        self.assertEqual(review.normalize_period("garbage"), "week")

    def test_chat_text_bilingual(self):
        ru = review.chat_text(self.conn, self.cfg, "ru", "week")
        self.assertIn("Сообщений: 1", ru)
        self.assertIn("крипта", ru)
        self.assertIn("$0.002", ru)
        en = review.chat_text(self.conn, self.cfg, "en", "week")
        self.assertIn("Messages: 1", en)
        self.assertIn("Reminders set: 1", en)

    def test_markdown_sections_and_backlog(self):
        md = review.markdown(self.conn, self.cfg, "week")
        for section in ("# Cara performance review", "## Activity", "## Learning",
                        "## Communication issues", "## AI spend",
                        "## Improvement backlog (for VS Code)"):
            self.assertIn(section, md)
        self.assertIn('"news" → "крипта" ×1', md)
        self.assertIn("out-of-scope request(s)", md)
        self.assertIn("напиши эссе", md)

    def test_router_accepts_review(self):
        ok = router.validate_route(
            {"action": "review", "params": {"period": "week", "export": True}}, False)
        self.assertEqual(ok["action"], "review")

    def test_new_digest_sections_and_trace_export(self):
        import trace
        store.set_facts(self.conn, 1, ["важный факт", "ещё факт"])         # facts learned
        store.rel_add(self.conn, "document_saved", "kept a document: x.pdf",
                      importance=2, title="x.pdf")                          # working history
        tid = trace.start(self.conn, "telegram_message", 1)
        trace.event(self.conn, tid, trace.LLM_FALLBACK,
                    "router_fast:anthropic-claude-haiku-4.5 failed", skill="router")
        trace.finish(self.conn, tid, "finished")
        md = review.markdown(self.conn, self.cfg, "week")
        for section in ("saved items by category", "facts learned: 2",
                        "## Working history", "## Model fallback incidents",
                        "## Trace summary"):
            self.assertIn(section, md)
        self.assertIn("крипта: 1", md)            # confirmed item counted by category
        self.assertIn("x.pdf", md)                 # grounded working-history moment
        # the new trace-summary export
        fname, body = review.export_document(self.conn, self.cfg, "trace", "en", "week")
        self.assertIn("cara-trace-summary-", fname)
        self.assertIn("CARA_TRACE_SUMMARY", body)
        self.assertIn("Model fallbacks: 1", body)
        self.assertIn("trace", review.EXPORT_KINDS)


class AgentViewTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(DB_PATH=str(Path(self.tmp.name) / "a.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "media"))
        self.agent = tg_ingest_agent.Agent(cfg)
        conn = self.agent.conn
        self.row_id = store.insert_message(conn, {
            "chat_id": 1, "tg_message_id": 5, "received_at": "2026-06-12T10:00:00+00:00",
            "raw_text": "рейсы Уфа-Красноярск", "forward_origin_title": "Vandrouki",
            "forward_origin_username": "vandrouki", "forward_origin_chat_id": -1001234,
            "forward_origin_message_id": 777, "forward_date": 1781200000,
        })
        store.insert_url(conn, self.row_id, "https://vandrouki.ru/x/")
        store.set_suggestion(conn, self.row_id, "Flight Deals", "Cheap June flights", "m")
        store.set_facts(conn, self.row_id, ["от 9800 руб туда-обратно", "июнь 2026"])
        store.confirm_category(conn, self.row_id, store.ensure_category(conn, "Flight Deals"))

    def tearDown(self):
        self.agent.conn.close()
        self.tmp.cleanup()

    def test_items_listing_shows_first_url(self):
        text = self.agent.items_text("ru", {})
        self.assertIn("📄 #%d · Flight Deals" % self.row_id, text)  # sectioned card style
        self.assertIn("🌐 https://vandrouki.ru/x/", text)

    def test_item_detail_by_id_query_and_fallback(self):
        detail = self.agent.item_detail_text("ru", {"id": self.row_id})
        self.assertIn("https://vandrouki.ru/x/", detail)
        self.assertIn("Источник: Vandrouki", detail)
        self.assertIn("Cheap June flights", detail)
        self.assertIn("Пост: https://t.me/vandrouki/777", detail)
        self.assertIn("Создано: ", detail)
        self.assertNotIn("T16:", detail)        # saved date no longer raw ISO with a 'T'
        self.assertIn("📄 #%d · Flight Deals" % self.row_id, detail)  # sectioned header
        self.assertIn("• от 9800 руб туда-обратно", detail)
        by_query = self.agent.item_detail_text("en", {"query": "рейсы"})
        self.assertIn("Source: Vandrouki", by_query)
        self.assertIn("Key facts:", by_query)
        by_fact_query = self.agent.item_detail_text("ru", {"query": "9800"})
        self.assertIn("#%d" % self.row_id, by_fact_query)  # facts are searchable
        latest = self.agent.item_detail_text("ru", {})  # no params -> most recent
        self.assertIn("#%d" % self.row_id, latest)
        missing = self.agent.item_detail_text("ru", {"query": "nothing-matches"})
        self.assertEqual(missing, texts.T("ru", "items_empty"))

    def test_router_accepts_item_detail(self):
        ok = router.validate_route({"action": "item_detail", "params": {"id": 1}}, False)
        self.assertEqual(ok["action"], "item_detail")
        ok = router.validate_route({"action": "item_delete", "params": {}}, False)
        self.assertEqual(ok["action"], "item_delete")

    def test_resolve_item_and_delete(self):
        conn = self.agent.conn
        self.assertEqual(self.agent.resolve_item({"id": self.row_id})["id"], self.row_id)
        self.assertEqual(self.agent.resolve_item({})["id"], self.row_id)  # most recent
        self.assertEqual(self.agent.resolve_item({"query": "рейсы"})["id"], self.row_id)
        # delete cascades urls and clears duplicate_of references
        dup_id = store.insert_message(conn, {
            "chat_id": 1, "tg_message_id": 6, "received_at": "ts", "duplicate_of": self.row_id,
        })
        paths = store.delete_message(conn, self.row_id)
        self.assertEqual(paths, [])
        self.assertIsNone(store.get_message(conn, self.row_id))
        self.assertEqual(store.message_urls(conn, self.row_id), [])
        self.assertIsNone(store.get_message(conn, dup_id)["duplicate_of"])

    def test_recategorize_saved_item(self):
        conn = self.agent.conn
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_recategorize(1, "ru", {"id": self.row_id, "category": "News"})
        row = store.get_message(conn, self.row_id)
        self.assertEqual(row["category"], "News")          # category actually changed
        self.assertEqual(row["status"], "confirmed")
        fb = conn.execute("SELECT corrected FROM feedback WHERE corrected='News'").fetchone()
        self.assertIsNotNone(fb)                           # recorded as a correction (learning)
        self.assertIn("News", r.call_args[0][1])

    def test_recategorize_defaults_to_most_recent(self):
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_recategorize(1, "ru", {"category": "Docs"})  # no id -> most recent
        self.assertEqual(store.get_message(self.agent.conn, self.row_id)["category"], "Docs")

    def test_recategorize_router_and_parser(self):
        self.assertEqual(router.validate_route(
            {"action": "recategorize", "params": {"id": 2, "category": "X"}}, False)["action"],
            "recategorize")
        self.assertTrue(skill_manifest.known("recategorize"))
        self.assertEqual(self.agent.explicit_category("поменяй категорию на Документы"), "Документы")
        self.assertEqual(self.agent.explicit_category("смени категорию на Чеки"), "Чеки")

    def test_show_media_uses_file_id(self):
        import tg_ingest_agent
        conn = self.agent.conn
        store.insert_image(conn, self.row_id, 5,
                           {"file_id": "FILEID123", "file_unique_id": "u1"}, None)
        with mock.patch.object(tg_ingest_agent, "tg_send_photo") as send:
            self.agent.do_show_media(1, "ru", {"id": self.row_id})
        send.assert_called_once()
        self.assertEqual(send.call_args[0][2], "FILEID123")  # re-sent by file_id, no upload

    def test_file_attachment_stored_listed_and_resent(self):
        import tg_ingest_agent
        conn = self.agent.conn
        store.insert_file(conn, self.row_id, 5, {
            "file_id": "DOCID9", "file_unique_id": "du1", "file_name": "Расписка.pdf",
            "mime_type": "application/pdf", "file_size": 3100000,
        })
        # detail names the file
        detail = self.agent.item_detail_text("ru", {"id": self.row_id})
        self.assertIn("Файлы: Расписка.pdf", detail)
        # show_media re-sends it as a document by file_id (no upload)
        with mock.patch.object(tg_ingest_agent, "tg_send_document_file_id") as send_doc, \
             mock.patch.object(tg_ingest_agent, "tg_send_photo"):
            self.agent.do_show_media(1, "ru", {"id": self.row_id})
        send_doc.assert_called_once()
        self.assertEqual(send_doc.call_args[0][2], "DOCID9")

    def test_finalize_stores_forwarded_pdf(self):
        msg = {
            "chat": {"id": 1}, "message_id": 50, "date": 1781200000,
            "from": {"id": 1},
            "forward_origin": {"type": "user", "sender_user_name": "Mikhail"},
            "document": {"file_id": "PDF1", "file_unique_id": "pu1",
                         "file_name": "Расписка.pdf", "mime_type": "application/pdf"},
        }
        with mock.patch.object(self.agent, "suggest_row", return_value=("docs", [], "s")), \
             mock.patch.object(self.agent, "present_suggestion"):
            self.agent.finalize([msg])
        row = self.agent.conn.execute(
            "SELECT id, raw_text FROM messages WHERE tg_message_id=50").fetchone()
        files = store.message_files(self.agent.conn, row["id"])
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["file_name"], "Расписка.pdf")
        self.assertEqual(files[0]["tg_file_id"], "PDF1")
        # filename became the searchable text so the item isn't "no content"
        self.assertEqual(row["raw_text"], "Расписка.pdf")

    def test_show_media_no_media(self):
        import tg_ingest_agent
        conn = self.agent.conn
        # no photos and no files -> friendly reply, nothing sent
        with mock.patch.object(tg_ingest_agent, "tg_send_photo") as send2, \
                mock.patch.object(tg_ingest_agent, "tg_send_document_file_id") as send_doc, \
                mock.patch.object(self.agent, "reply") as reply:
            other = store.insert_message(conn, {"chat_id": 1, "tg_message_id": 9,
                                                "received_at": "ts", "raw_text": "no pics"})
            self.agent.do_show_media(1, "ru", {"id": other})
            send2.assert_not_called()
            send_doc.assert_not_called()
            self.assertIn("нет сохранённых фото", reply.call_args[0][1])

    def test_discard_deletes_pending_fresh_item(self):
        conn = self.agent.conn
        fresh = store.insert_message(conn, {"chat_id": 1, "tg_message_id": 7,
                                            "received_at": "ts", "raw_text": "throwaway"})
        store.set_suggestion(conn, fresh, "Spam", "junk", "m")
        store.pending_set(conn, 1, "category", {"row_id": fresh})
        with mock.patch.object(self.agent, "reply") as reply:
            self.agent.do_discard(1, "ru", store.pending_get(conn, 1))
        self.assertIsNone(store.get_message(conn, fresh))  # gone
        self.assertIsNone(store.pending_get(conn, 1))
        self.assertIn("выбросила", reply.call_args[0][1])
        # nothing pending -> nothing destroyed
        with mock.patch.object(self.agent, "reply") as reply2:
            self.agent.do_discard(1, "ru", None)
            self.assertIn("нечего отклонять", reply2.call_args[0][1])

    def test_housekeep_purges_unreferenced_media_after_grace(self):
        import os
        import time as _t
        media = self.agent.cfg.media_dir
        media.mkdir(parents=True, exist_ok=True)
        # a referenced photo (kept), an orphan voice note (purged), a fresh orphan (kept)
        kept = media / "kept.jpg"; kept.write_bytes(b"img")
        store.insert_image(self.agent.conn, self.row_id, 5,
                           {"file_id": "f", "file_unique_id": "kept"}, str(kept))
        orphan = media / "voice.oga"; orphan.write_bytes(b"audio")
        fresh = media / "fresh.oga"; fresh.write_bytes(b"audio")
        old = _t.time() - 7200
        os.utime(orphan, (old, old))
        self.agent.housekeep()
        self.assertTrue(kept.exists())       # referenced content stays
        self.assertFalse(orphan.exists())    # old unreferenced artifact purged
        self.assertTrue(fresh.exists())      # within grace window, kept


class PurgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "p.db")
        for i in range(3):
            mid = store.insert_message(self.conn, {
                "chat_id": 1, "tg_message_id": 10 + i, "received_at": "ts", "raw_text": "x"})
            cat = "Крипта" if i < 2 else "News"
            store.set_suggestion(self.conn, mid, cat, "s", "m")
            store.confirm_category(self.conn, mid, store.ensure_category(self.conn, cat))
        store.issue_add(self.conn, 1, "out_of_scope", "x")
        store.feedback_add(self.conn, "ingest", "d", "a", "b")
        store.usage_add(self.conn, "ingest", "chat", "m", 10, 5, cost_usd=0.01)
        store.reminder_add(self.conn, 1, "r", "2099-01-01T00:00:00+00:00")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_preview_counts(self):
        self.assertEqual(store.purge_preview(self.conn, "all")["messages"], 3)
        cat = store.purge_preview(self.conn, "category", "крипта")  # Cyrillic case-insensitive
        self.assertEqual(cat["messages"], 2)
        self.assertEqual(store.purge_preview(self.conn, "stats")["feedback"], 1)

    def test_category_purge_keeps_others_and_usage(self):
        info, paths = store.purge_execute(self.conn, "category", "КРИПТА")
        self.assertEqual(info["messages"], 2)
        self.assertEqual(store.purge_preview(self.conn, "all")["messages"], 1)  # News kept
        self.assertEqual(store.usage_total(self.conn, "month"), 0.01)  # usage untouched
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM categories WHERE norm_key='крипта'").fetchone())

    def test_all_purge_preserves_usage_and_prefs(self):
        store.pref_set(self.conn, "owner_name", "Owen")
        store.purge_execute(self.conn, "all")
        self.assertEqual(store.purge_preview(self.conn, "all")["messages"], 0)
        self.assertEqual(store.status_counts(self.conn), [])
        self.assertEqual(store.usage_total(self.conn, "month"), 0.01)  # spend history kept
        self.assertEqual(store.pref_get(self.conn, "owner_name"), "Owen")  # identity kept

    def test_stats_scope_keeps_messages(self):
        store.purge_execute(self.conn, "stats")
        self.assertEqual(store.purge_preview(self.conn, "all")["messages"], 3)  # messages kept
        self.assertEqual(store.issue_counts(self.conn, "2000-01-01"), [])
        self.assertEqual(store.known_categories(self.conn), [])

    def test_router_accepts_purge(self):
        ok = router.validate_route({"action": "purge", "params": {"scope": "all"}}, False)
        self.assertEqual(ok["action"], "purge")

    def test_messages_scope_keeps_categories_reminders_usage(self):
        info, _ = store.purge_execute(self.conn, "messages")
        self.assertEqual(info["messages"], 3)
        self.assertEqual(store.purge_preview(self.conn, "all")["messages"], 0)  # notes gone
        self.assertGreater(len(store.known_categories(self.conn)), 0)           # categories kept
        self.assertEqual(len(store.reminders_active(self.conn, 1)), 1)          # reminders kept
        self.assertEqual(store.usage_total(self.conn, "month"), 0.01)          # spend kept

    def test_issues_scope_clears_only_issues(self):
        info, _ = store.purge_execute(self.conn, "issues")
        self.assertEqual(info["issues"], 1)
        self.assertEqual(store.issue_counts(self.conn, "2000-01-01"), [])      # issues gone
        self.assertEqual(store.purge_preview(self.conn, "all")["messages"], 3)  # notes kept
        self.assertGreater(len(store.known_categories(self.conn)), 0)          # categories kept


class MultiDeleteTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(DB_PATH=str(Path(self.tmp.name) / "d.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.ids = []
        for i in range(5):
            mid = store.insert_message(self.agent.conn, {
                "chat_id": 1, "tg_message_id": i + 1, "received_at": store._now(),
                "raw_text": f"note{i}"})
            store.set_suggestion(self.agent.conn, mid, "News", "s", "m")
            store.confirm_category(self.agent.conn, mid, store.ensure_category(self.agent.conn, "News"))
            self.ids.append(mid)

    def tearDown(self):
        self.agent.conn.close()
        self.tmp.cleanup()

    def test_resolve_items_ids_count_single(self):
        by_ids = self.agent.resolve_items({"ids": [self.ids[0], self.ids[2], 9999]})
        self.assertEqual([r["id"] for r in by_ids], [self.ids[0], self.ids[2]])  # bad id skipped
        self.assertEqual(len(self.agent.resolve_items({"count": 3})), 3)
        self.assertEqual(len(self.agent.resolve_items({"count": 99})), 5)  # capped at available
        self.assertEqual([r["id"] for r in self.agent.resolve_items({"id": self.ids[1]})],
                         [self.ids[1]])

    def test_multi_delete_confirm(self):
        store.pending_set(self.agent.conn, 1, "delete", {"row_ids": [self.ids[0], self.ids[1]]})
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.resolve_pending(1, "confirm", {}, store.pending_get(self.agent.conn, 1), "ru")
        self.assertIsNone(store.get_message(self.agent.conn, self.ids[0]))
        self.assertIsNone(store.get_message(self.agent.conn, self.ids[1]))
        self.assertIsNotNone(store.get_message(self.agent.conn, self.ids[2]))  # others kept
        self.assertIn("записей", r.call_args[0][1])  # deleted_multi
        self.assertIsNone(store.pending_get(self.agent.conn, 1))

    def test_delete_cancel_keeps_everything(self):
        store.pending_set(self.agent.conn, 1, "delete", {"row_ids": [self.ids[0]]})
        with mock.patch.object(self.agent, "reply"):
            self.agent.resolve_pending(1, "cancel", {}, store.pending_get(self.agent.conn, 1), "ru")
        self.assertIsNotNone(store.get_message(self.agent.conn, self.ids[0]))

    def test_router_accepts_multi_and_count_and_messages(self):
        for params in ({"ids": [1, 2, 3]}, {"count": 7}):
            self.assertEqual(router.validate_route(
                {"action": "item_delete", "params": params}, False)["action"], "item_delete")
        self.assertEqual(router.validate_route(
            {"action": "purge", "params": {"scope": "messages"}}, False)["action"], "purge")


class PurgeFlowTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(DB_PATH=str(Path(self.tmp.name) / "f.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        mid = store.insert_message(self.agent.conn, {
            "chat_id": 1, "tg_message_id": 1, "received_at": "ts", "raw_text": "x"})
        store.set_suggestion(self.agent.conn, mid, "News", "s", "m")
        store.confirm_category(self.agent.conn, mid, store.ensure_category(self.agent.conn, "News"))

    def tearDown(self):
        self.agent.conn.close()
        self.tmp.cleanup()

    def test_typed_confirmation_required(self):
        with mock.patch.object(self.agent, "reply") as reply:
            self.agent.do_purge(1, "ru", {"scope": "all"})
        self.assertEqual(store.pending_get(self.agent.conn, 1)["kind"], "purge")
        phrase = store.pending_get(self.agent.conn, 1)["payload"]["phrase"]
        # wrong phrase (a stray "да") must NOT delete anything
        with mock.patch.object(self.agent, "reply") as reply:
            self.agent.resolve_purge(1, "ru", store.pending_get(self.agent.conn, 1), "да")
        self.assertIn("ничего не трогаю", reply.call_args[0][1])
        self.assertEqual(store.purge_preview(self.agent.conn, "all")["messages"], 1)  # intact
        # exact phrase deletes
        self.agent.do_purge(1, "ru", {"scope": "all"})
        with mock.patch.object(self.agent, "reply"):
            self.agent.resolve_purge(1, "ru", store.pending_get(self.agent.conn, 1), phrase)
        self.assertEqual(store.purge_preview(self.agent.conn, "all")["messages"], 0)

    def test_purge_nothing_when_empty(self):
        store.purge_execute(self.agent.conn, "all")
        with mock.patch.object(self.agent, "reply") as reply:
            self.agent.do_purge(1, "ru", {"scope": "all"})
        self.assertIn("Удалять нечего", reply.call_args[0][1])
        self.assertIsNone(store.pending_get(self.agent.conn, 1))


class FetchTests(unittest.TestCase):
    def test_validate_url_scheme_and_creds(self):
        with self.assertRaises(fetch.FetchError):
            fetch.validate_url("ftp://example.com/x")
        with self.assertRaises(fetch.FetchError):
            fetch.validate_url("file:///etc/passwd")
        with self.assertRaises(fetch.FetchError):
            fetch.validate_url("https://user:pass@example.com/")

    def test_validate_url_blocks_private_and_metadata(self):
        for bad in ("http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/",
                    "http://10.0.0.5/", "http://192.168.1.1/", "http://[::1]/"):
            with self.assertRaises(fetch.FetchError) as ctx:
                fetch.validate_url(bad)
            self.assertIn(ctx.exception.reason, ("fetch_private", "fetch_blocked", "fetch_failed"))

    def test_ip_blocked(self):
        self.assertTrue(fetch._ip_blocked("127.0.0.1"))
        self.assertTrue(fetch._ip_blocked("169.254.169.254"))
        self.assertTrue(fetch._ip_blocked("10.1.2.3"))
        self.assertTrue(fetch._ip_blocked("::1"))
        self.assertTrue(fetch._ip_blocked("not-an-ip"))
        self.assertFalse(fetch._ip_blocked("8.8.8.8"))
        self.assertFalse(fetch._ip_blocked("1.1.1.1"))

    def test_normalize_tme(self):
        self.assertEqual(fetch.normalize_tme("https://t.me/vandrouki/777"),
                         "https://t.me/s/vandrouki/777")
        # already-web-view, private, and joinchat links are left alone
        self.assertEqual(fetch.normalize_tme("https://t.me/s/vandrouki/777"),
                         "https://t.me/s/vandrouki/777")
        self.assertEqual(fetch.normalize_tme("https://t.me/c/123/45"),
                         "https://t.me/c/123/45")
        self.assertEqual(fetch.normalize_tme("https://example.com/a"),
                         "https://example.com/a")

    def test_extract_text_strips_scripts_and_gets_title(self):
        html = ("<html><head><title>Cheap Flights</title><style>x{}</style></head>"
                "<body><script>evil()</script><h1>Ufa</h1><p>от 9800 руб</p></body></html>")
        title, text = fetch.extract_text(html)
        self.assertEqual(title, "Cheap Flights")
        self.assertIn("Ufa", text)
        self.assertIn("9800", text)
        self.assertNotIn("evil", text)
        self.assertNotIn("x{}", text)

    def test_router_accepts_fetch(self):
        ok = router.validate_route(
            {"action": "fetch", "params": {"url": "https://x.example/a"}}, False)
        self.assertEqual(ok["action"], "fetch")

    def test_do_fetch_ingests_page_as_item(self):
        import tg_ingest_agent
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(DB_PATH=str(Path(tmp) / "f.db"), MEDIA_DIR=str(Path(tmp) / "m"))
            agent = tg_ingest_agent.Agent(cfg)
            try:
                reply = '{"category": "News", "alternatives": [], "summary": "s", "facts": ["f"]}'
                with mock.patch.object(tg_ingest_agent.fetch, "fetch",
                                       return_value=("https://x.example/a", "Title", "article body")), \
                        mock.patch.object(llm, "chat", return_value=reply), \
                        mock.patch.object(agent, "reply", return_value={"message_id": 1}):
                    agent.do_fetch(1, "ru", {"url": "https://x.example/a"})
                rows = store.list_messages(agent.conn, query="article")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["forward_origin_title"], "Title")
                self.assertEqual([r["url"] for r in store.message_urls(agent.conn, rows[0]["id"])],
                                 ["https://x.example/a"])
                # blocked url -> friendly reply, nothing stored
                with mock.patch.object(tg_ingest_agent.fetch, "fetch",
                                       side_effect=fetch.FetchError("nope", "fetch_private")), \
                        mock.patch.object(agent, "reply") as r2:
                    agent.do_fetch(1, "ru", {"url": "http://10.0.0.1/"})
                    self.assertIn("приватн", r2.call_args[0][1])
            finally:
                agent.conn.close()


class SkillManifestTests(unittest.TestCase):
    def test_every_router_action_has_a_policy(self):
        for action in router.ACTIONS:
            self.assertTrue(skill_manifest.known(action), f"no manifest policy for {action}")

    def test_policy_defaults_and_gating(self):
        purge = skill_manifest.get_policy("purge")
        self.assertEqual(purge["risk"], "destructive")
        self.assertEqual(purge["requires_confirmation"], "typed_phrase")
        self.assertFalse(purge["allowed_proactive"])
        with self.assertRaises(skill_manifest.SkillPolicyError):
            skill_manifest.assert_proactive_allowed("purge")
        skill_manifest.assert_proactive_allowed("review")  # allowed, no raise
        with self.assertRaises(skill_manifest.SkillPolicyError):
            skill_manifest.get_policy("nonexistent_action")

    def test_capability_titles_from_manifest(self):
        titles_ru = skill_manifest.capability_titles("ru")
        titles_en = skill_manifest.capability_titles("en")
        self.assertIn("Напоминания", titles_ru)
        self.assertIn("Knowledge-base Q&A", titles_en)
        # meta glue (confirm/cancel/router) is not surfaced as a capability
        self.assertNotIn("", titles_en)

    def test_assert_covers_catches_drift(self):
        skill_manifest.assert_covers(router.ACTIONS)            # current set: no raise
        with self.assertRaises(skill_manifest.SkillPolicyError):
            skill_manifest.assert_covers(router.ACTIONS | {"a_brand_new_action"})

    def test_destructive_requires_typed_phrase(self):
        # safety contract: anything destructive must demand the exact typed phrase
        for action, _ in skill_manifest.SKILLS.items():
            policy = skill_manifest.get_policy(action)
            if policy["destructive"]:
                self.assertEqual(policy["requires_confirmation"], "typed_phrase",
                                 f"{action} is destructive but not typed_phrase-gated")

    def test_proactive_skills_are_never_dangerous(self):
        # P1.6 floor: a proactive nudge can never be destructive or an external write
        for action, _ in skill_manifest.SKILLS.items():
            policy = skill_manifest.get_policy(action)
            if policy["allowed_proactive"]:
                self.assertNotIn(policy["risk"], ("destructive", "external_write"),
                                 f"{action} is proactive but {policy['risk']}")


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        import common
        common.set_current_trace(None)
        self.conn.close()
        self.tmp.cleanup()

    def test_trace_lifecycle_and_current(self):
        import common
        tid = tracing.start(self.conn, "inbound", chat_id=7)
        self.assertTrue(tid.startswith("tr_"))
        self.assertEqual(common.current_trace(), tid)
        tracing.event(self.conn, tid, tracing.ROUTER_COMPLETED, "action=spend", skill="spend")
        tracing.finish(self.conn, tid, "ok", "done")
        self.assertIsNone(common.current_trace())  # cleared on finish
        row = store.trace_get(self.conn, tid)
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["chat_id"], 7)
        events = store.trace_events(self.conn, tid)
        self.assertEqual(events[0]["stage"], tracing.ROUTER_COMPLETED)

    def test_usage_and_issue_stamped_with_current_trace(self):
        tid = tracing.start(self.conn, "inbound", chat_id=1)
        store.usage_add(self.conn, "router", "chat", "m", 1, 1, cost_usd=0.001)
        store.issue_add(self.conn, 1, "out_of_scope", "x")
        tracing.finish(self.conn, tid, "ok")
        usage = self.conn.execute("SELECT trace_id FROM llm_usage").fetchone()
        issue = self.conn.execute("SELECT trace_id FROM issues").fetchone()
        self.assertEqual(usage["trace_id"], tid)
        self.assertEqual(issue["trace_id"], tid)
        # after finish, no current trace -> new rows are unstamped (None)
        store.issue_add(self.conn, 1, "llm_error", "y")
        self.assertIsNone(self.conn.execute(
            "SELECT trace_id FROM issues ORDER BY id DESC LIMIT 1").fetchone()["trace_id"])

    def test_trace_id_columns_migrated_on_old_db(self):
        import sqlite3
        path = Path(self.tmp.name) / "old.db"
        raw = sqlite3.connect(str(path))
        raw.execute("CREATE TABLE llm_usage (id INTEGER PRIMARY KEY, ts TEXT NOT NULL,"
                    " day TEXT NOT NULL, month TEXT NOT NULL, skill TEXT NOT NULL,"
                    " kind TEXT NOT NULL, model TEXT NOT NULL, tokens_in INTEGER,"
                    " tokens_out INTEGER, seconds REAL, cost_usd REAL)")
        raw.execute("CREATE TABLE issues (id INTEGER PRIMARY KEY, ts TEXT NOT NULL,"
                    " day TEXT NOT NULL, chat_id INTEGER, kind TEXT NOT NULL, detail TEXT)")
        raw.commit()
        raw.close()
        conn = store.open_db(path)
        try:
            for tbl in ("llm_usage", "issues"):
                cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({tbl})")}
                self.assertIn("trace_id", cols)
        finally:
            conn.close()


class PersonaPatchTests(unittest.TestCase):
    """v3 §0.1 persona-integration patch (Fixes 1, 2, 5, 6, 7)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "pp.db")
        self.cfg = make_config()

    def tearDown(self):
        texts.set_intensity(2)  # restore default (module global)
        self.conn.close()
        self.tmp.cleanup()

    # Fix 1: persona layer order
    def test_persona_below_operational_rules(self):
        self.assertTrue(persona.persona_below_rules())
        self.assertEqual(persona.PROMPT_LAYER_ORDER[-1], "user_message")
        self.assertLess(persona.PROMPT_LAYER_ORDER.index("budget_rules"),
                        persona.PROMPT_LAYER_ORDER.index("human_like_persona"))

    def test_router_prompt_stays_strict(self):
        prompt = router.build_system_prompt(self.cfg, None)
        self.assertIn("JSON", prompt)
        self.assertIn("closed", prompt.lower())
        for leak in ("Tiny archive win", "архивная победа", "Caught it, boss"):
            self.assertNotIn(leak, prompt)  # no persona variant prose in the router

    # Fix 2: variant families + intensity select wording, not rules
    def test_variant_family_and_intensity(self):
        kw = dict(category="X", summary="s", counts="(c)")
        texts.set_intensity(0)
        self.assertIn("I'd file", texts.T("en", "suggestion", **kw))  # sober variant 0
        texts.set_intensity(2)
        warm = texts.T("en", "suggestion", **kw)
        self.assertIn("X", warm)  # still valid, placeholders intact
        # deterministic: same inputs render the same variant
        self.assertEqual(warm, texts.T("en", "suggestion", **kw))

    # Fix 5: action-truth guard
    def test_action_truth_guard(self):
        action_truth.assert_template_allowed(
            "suggestion", "suggested", texts.T("en", "suggestion", category="X",
                                               summary="s", counts="c"))  # no final verb: ok
        action_truth.assert_template_allowed(
            "confirmed", "confirmed", texts.T("en", "confirmed", category="X", row_id=1))
        with self.assertRaises(ValueError):
            action_truth.assert_template_allowed("x", "suggested", "I saved and filed it")
        with self.assertRaises(ValueError):
            action_truth.assert_template_allowed("x", "suggested", "Готово, сохранила")

    # Fix 6: truthful STT failure copy (Cara doesn't keep the file / can't retry)
    def test_stt_copy_truthful(self):
        for lang in ("ru", "en"):
            texts.set_intensity(0)
            v0 = texts.T(lang, "stt_failed")
            texts.set_intensity(2)
            for txt in (v0, texts.T(lang, "stt_failed")):
                low = txt.lower()
                self.assertNotIn("saved", low)
                self.assertNotIn("сохранила", low)
                self.assertNotIn("not available", low)
                self.assertNotIn("недоступ", low)
                self.assertNotIn("retry", low)  # no false retry promise

    # Fix 7: address resolution from preferences with боcс/boss fallback
    def test_address_resolution(self):
        self.assertEqual(boss_model.get_address(self.conn, "ru"), "босс")
        self.assertEqual(boss_model.get_address(self.conn, "en"), "boss")
        store.pref_set(self.conn, "owner_name", "Owen")
        self.assertEqual(boss_model.get_address(self.conn, "en"), "Owen")
        store.pref_set(self.conn, "owner_name_ru", "Олег")
        self.assertEqual(boss_model.get_address(self.conn, "ru"), "Олег")  # lang-specific wins
        self.assertEqual(boss_model.get_address(self.conn, "ru", allow_name=False), "босс")
        store.pref_set(self.conn, "preferred_address_en", "chief")
        self.assertEqual(boss_model.get_address(self.conn, "en", allow_name=False), "chief")

    # Personality: character + relationship answers (the screenshot complaint)
    def test_persona_character_is_in_character_but_honest(self):
        for lang in ("ru", "en"):
            ch = texts.T(lang, "persona_character", name=("босс" if lang == "ru" else "boss"))
            self.assertTrue("рыж" in ch.lower() or "red" in ch.lower())   # character shows
            self.assertIn("персон" if lang == "ru" else "persona", ch.lower())  # transparency
            self.assertNotIn("SQLite", ch)  # NOT a tech dump
            self.assertNotIn("Pilot-VPS", ch)

    def test_persona_relationship_warm_within_bounds(self):
        for lang in ("ru", "en"):
            low = texts.T(lang, "persona_relationship").lower()
            self.assertTrue("сторон" in low or "side" in low)  # warm/loyal
            for banned in ("влюбл", "romantic", "секс", "in love"):
                self.assertNotIn(banned, low)  # §3 boundaries

    def test_router_and_manifest_have_persona(self):
        for topic in ("character", "relationship", "origin"):
            self.assertEqual(router.validate_route(
                {"action": "persona", "params": {"topic": topic}}, False)["action"], "persona")
        self.assertTrue(skill_manifest.known("persona"))

    def test_origin_is_honest(self):
        for lang in ("ru", "en"):
            low = texts.T(lang, "persona_origin", name=("босс" if lang == "ru" else "boss")).lower()
            self.assertTrue("человеческого" in low or "no human" in low)  # no fake past
            self.assertNotIn("years", low)


class ConversationDispatchTests(unittest.TestCase):
    """Full dispatch path for the free-form conversational Cara — greetings,
    personal questions and unknown intents all reach the warm converse path
    (the model writes the reply), never a fixed template."""

    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(DB_PATH=str(Path(self.tmp.name) / "pd.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        # Disable quiet hours so reminder-firing tests are deterministic regardless of the
        # wall-clock when the suite runs (the quiet-hours test mocks in_quiet_hours itself).
        store.pref_set(self.agent.conn, "quiet_start", "0")
        store.pref_set(self.agent.conn, "quiet_end", "0")

    def tearDown(self):
        self.agent.conn.close()
        self.tmp.cleanup()

    def test_explicit_category_assignment_applies_named_category(self):
        # the screenshot bug: "Категория - Документы" while a suggestion is pending
        # was confirming the fallback ("uncategorized") instead of setting Документы.
        conn = self.agent.conn
        mid = store.insert_message(conn, {"chat_id": 1, "tg_message_id": 1,
                                          "received_at": store._now(), "raw_text": "Расписка.pdf"})
        store.set_suggestion(conn, mid, "uncategorized", "filename only", "m")
        store.pending_set(conn, 1, "category", {"row_id": mid})
        with mock.patch.object(router, "route") as route, \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.dispatch(1, {}, "Категория - Документы")
        route.assert_not_called()                      # resolved deterministically, no router
        row = store.get_message(conn, mid)
        self.assertEqual(row["category"], "Документы")  # named category applied, not fallback
        self.assertEqual(row["status"], "confirmed")
        # it's a correction (suggested != confirmed) -> logged as feedback for learning
        fb = conn.execute("SELECT corrected FROM feedback WHERE corrected='Документы'").fetchone()
        self.assertIsNotNone(fb)

    def test_explicit_category_parser_variants(self):
        for text in ("Категория - Документы", "категория: Документы",
                     "в категорию Документы", "set category to Документы"):
            self.assertEqual(self.agent.explicit_category(text), "Документы")
        self.assertIsNone(self.agent.explicit_category("какая категория?"))
        self.assertIsNone(self.agent.explicit_category("покажи заметки"))

    def test_greeting_short_circuits_to_free_form(self):
        # "расскажи о себе"/"кто ты" are smalltalk -> straight to converse (LLM),
        # skipping the router, NO template.
        for text in ("расскажи о себе", "кто ты?", "привет"):
            with mock.patch.object(llm, "chat_profile", return_value="Рыжая и рада тебе 🙂") as cp, \
                    mock.patch.object(self.agent, "send_chat_action"), \
                    mock.patch.object(self.agent, "reply") as r:
                self.agent.dispatch(1, {}, text)
            cp.assert_called_once()  # the model wrote the reply
            self.assertEqual(cp.call_args.kwargs["profile"], "converse_warm")
            self.assertEqual(r.call_args[0][1], "Рыжая и рада тебе 🙂")

    def test_personal_question_routes_to_converse(self):
        with mock.patch.object(router, "route",
                               return_value={"action": "converse", "params": {}, "confidence": 0.9}), \
                mock.patch.object(llm, "chat_profile", return_value="День был тихий, гуляла у реки.") as cp, \
                mock.patch.object(self.agent, "send_chat_action"), \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.dispatch(1, {}, "как прошёл твой день?")
        cp.assert_called_once()
        self.assertEqual(r.call_args[0][1], "День был тихий, гуляла у реки.")

    def test_bare_ack_gets_no_reply(self):
        # A lone "ок"/"👍" needs no answer, like a human.
        with mock.patch.object(llm, "chat_profile") as cp, \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.dispatch(1, {}, "ок")
        cp.assert_not_called()
        r.assert_not_called()

    def test_multi_action_asks_one_at_a_time(self):
        with mock.patch.object(router, "route",
                               return_value={"action": "multi_action", "params": {}, "confidence": 0.85}), \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.dispatch(1, {}, "первое закрой, второе - напомни в 14:00")
        self.assertEqual(r.call_args[0][1], texts.T(self.agent.lang(), "one_at_a_time"))
        self.assertTrue(skill_manifest.known("multi_action"))

    def test_report_problem_logs_boss_reported_issue(self):
        with mock.patch.object(router, "route",
                               return_value={"action": "report_problem",
                                             "params": {"detail": "не нашла заметку 11"}, "confidence": 0.9}), \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.dispatch(1, {}, "запиши в проблемы: не нашла заметку 11")
        self.assertEqual(r.call_args[0][1], texts.T(self.agent.lang(), "problem_logged"))
        rows = self.agent.conn.execute(
            "SELECT kind, detail FROM issues WHERE kind = 'boss_reported'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn("заметку 11", rows[0]["detail"])
        self.assertTrue(skill_manifest.known("report_problem"))

    def test_referential_save_pulls_conversation_subject(self):
        # "Сохрани заметку про этот фильм" must capture the film named earlier
        # (the recorded bug: the saved note lost the movie name).
        import ingest
        conn = self.agent.conn
        store.convo_add(conn, 1, "user", "Добавь заметку, посмотри фильм Не смотрите наверх с ДиКаприо")
        store.convo_add(conn, 1, "bot", "Уточню: заметку про фильм «Не смотрите наверх» с Ди Каприо?")
        mid = store.insert_message(conn, {"chat_id": 1, "tg_message_id": 1,
                                          "received_at": store._now(),
                                          "raw_text": "Сохрани заметку про этот фильм, да"})
        row = store.get_message(conn, mid)
        captured = {}

        def fake_suggest(cfg, c, known, text_block, image_paths, lang="ru"):
            captured["tb"] = text_block
            return ("Фильмы", [], "Фильм «Не смотрите наверх» с Ди Каприо — посмотреть", [])

        with mock.patch.object(ingest, "suggest", side_effect=fake_suggest), \
                mock.patch.object(self.agent, "index_message"):
            cat, _alts, _summ = self.agent.suggest_row(row)
        self.assertIn("Не смотрите наверх", captured["tb"])   # conversation context injected
        self.assertIn("этот фильм", captured["tb"])           # the note itself still present
        self.assertEqual(cat, "Фильмы")

    def test_referential_save_detection(self):
        ref = {"raw_text": "Сохрани заметку про этот фильм, да", "forward_origin_type": None}
        plain = {"raw_text": "купить молоко", "forward_origin_type": None}
        fwd = {"raw_text": "сохрани это", "forward_origin_type": "channel"}
        self.assertTrue(self.agent._is_referential_save(ref, [], []))
        self.assertFalse(self.agent._is_referential_save(plain, [], []))      # no reference
        self.assertFalse(self.agent._is_referential_save(fwd, [], []))        # forwards excluded
        self.assertFalse(self.agent._is_referential_save(ref, ["http://x"], []))  # real content

    def test_image_ingest_falls_back_to_text_only(self):
        # Forwarded photo + a non-vision model must NOT get stuck (issue #18):
        # re-ingest text-only so the caption still categorizes.
        import ingest, llm
        conn = self.agent.conn
        mid = store.insert_message(conn, {"chat_id": 1, "tg_message_id": 1,
                                          "received_at": store._now(),
                                          "raw_text": "Промпты для путешествий"})
        store.insert_image(conn, mid, 1, {"file_id": "F", "file_unique_id": "u"}, "/tmp/x.jpg")
        row = store.get_message(conn, mid)
        calls = []

        def fake_suggest(cfg, c, known, text_block, image_paths, lang="ru"):
            calls.append(list(image_paths))
            if image_paths:
                raise llm.LLMError("HTTP 400: model does not support image input")
            return ("Путешествия", [], "Промпты для путешествий", [])

        with mock.patch.object(ingest, "suggest", side_effect=fake_suggest), \
                mock.patch.object(self.agent, "index_message"):
            result = self.agent.suggest_row(row)
        self.assertIsNotNone(result)                              # recovered, not stuck
        self.assertEqual(result[0], "Путешествия")
        self.assertEqual([bool(c) for c in calls], [True, False])  # image attempt, then text-only

    def test_image_ingest_uses_vision_describe(self):
        # With a vision model configured, the photo is DESCRIBED and folded into
        # the text; no raw image goes to the (non-vision) text model.
        import ingest, llm
        self.agent.cfg.vision_model = "nemotron-3-nano-omni"
        conn = self.agent.conn
        mid = store.insert_message(conn, {"chat_id": 1, "tg_message_id": 1,
                                          "received_at": store._now(), "raw_text": "подпись"})
        store.insert_image(conn, mid, 1, {"file_id": "F", "file_unique_id": "u"}, "/tmp/x.jpg")
        row = store.get_message(conn, mid)
        cap = {}

        def fake_describe(cfg, c, skill, model, path, lang="ru"):
            cap["model"] = model
            return "скриншот про бюджетные путешествия"

        def fake_suggest(cfg, c, known, text_block, image_paths, lang="ru"):
            cap["tb"] = text_block
            cap["imgs"] = list(image_paths)
            return ("Путешествия", [], "s", [])

        with mock.patch.object(llm, "describe_image", side_effect=fake_describe), \
                mock.patch.object(ingest, "suggest", side_effect=fake_suggest), \
                mock.patch.object(self.agent, "index_message"):
            result = self.agent.suggest_row(row)
        self.assertEqual(cap["model"], "nemotron-3-nano-omni")
        self.assertIn("бюджетные путешествия", cap["tb"])   # description folded into text
        self.assertEqual(cap["imgs"], [])                   # no raw image to the text model
        self.assertEqual(result[0], "Путешествия")

    def test_model_health_alerts_on_transition(self):
        import llm
        self.agent.cfg.model_health_interval = 1
        self.agent.cfg.do_model = "deepseek-4-flash"
        self.agent.cfg.vision_model = ""

        def run(ok, reason=""):
            self.agent.last_model_health = 0  # force the interval gate open
            with mock.patch.object(llm, "model_ok", return_value=(ok, reason)), \
                    mock.patch.object(self.agent, "reply") as r:
                self.agent.check_model_health()
            return r

        r = run(False, "403 tier")              # first check: down -> alert
        self.assertTrue(r.called)
        self.assertIn("deepseek-4-flash", r.call_args[0][1])
        r = run(True)                           # recovered -> alert
        self.assertTrue(r.called)
        r = run(True)                           # still up -> no alert
        self.assertFalse(r.called)

    def test_model_health_skips_when_budget_stopped(self):
        # A budget stop blocks every model call before it leaves the box, so the
        # probes would all "fail" — but that's spend, not a model outage. The
        # monitor must NOT probe or alert "model down" in that state.
        import llm
        self.agent.cfg.model_health_interval = 1
        self.agent.cfg.do_model = "deepseek-4-flash"
        self.agent.last_model_health = 0
        with mock.patch.object(llm, "budget_state",
                               return_value=("stop", "day", 2.01, 2.0)), \
                mock.patch.object(llm, "model_ok") as ok, \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.check_model_health()
        self.assertFalse(ok.called)   # never probed
        self.assertFalse(r.called)    # never alerted

    def test_unwrap_converse_array(self):
        # deepseek-v4-pro sometimes returns ["emoji", "text"] instead of a plain
        # string — salvage it into (reaction, clean text) so the raw literal never
        # reaches the boss (the "strange message formatting" report).
        import tg_ingest_agent
        u = tg_ingest_agent.Agent._unwrap_converse_array
        self.assertEqual(u('["👍", "Всегда пожалуйста."]'), ("👍", "Всегда пожалуйста."))
        self.assertEqual(u('["🥰", "Так даже лучше."]'), ("🥰", "Так даже лучше."))
        self.assertEqual(u("просто текст"), (None, "просто текст"))      # plain string
        self.assertEqual(u('["только текст"]'), (None, "только текст"))  # no reaction
        # first element not a real reaction emoji -> not treated as a reaction
        self.assertEqual(u('["hmm", "text"]')[0], None)
        # malformed array passes through untouched
        self.assertEqual(u('[broken'), (None, "[broken"))

    def test_register_state_mobilizes_and_eases(self):
        # The companion register is set by recent business + the work window, NOT a
        # day/night clock gate. Business activity -> 'working' at any hour; off-hours
        # quiet -> 'relaxed'; work-hours quiet -> 'neutral'.
        from datetime import datetime, timezone
        a = self.agent
        # tz_offset default is +3 (MSK): pick UTC instants with known boss-local times.
        wed_work = datetime(2026, 6, 17, 9, 0, tzinfo=timezone.utc)   # Wed 12:00 local
        wed_eve = datetime(2026, 6, 17, 20, 0, tzinfo=timezone.utc)   # Wed 23:00 local
        sun_day = datetime(2026, 6, 21, 9, 0, tzinfo=timezone.utc)    # Sun 12:00 local
        store.kv_set(a.conn, "last_business_at", "")
        self.assertEqual(a._register_state(wed_work), "neutral")   # work hours, quiet
        self.assertEqual(a._register_state(wed_eve), "relaxed")    # off hours, quiet
        self.assertEqual(a._register_state(sun_day), "relaxed")    # weekend = off
        # Recent business mobilizes her to 'working' even in the evening.
        store.kv_set(a.conn, "last_business_at", wed_eve.isoformat())
        self.assertEqual(a._register_state(wed_eve), "working")

    def test_register_directive_has_content_override(self):
        # The resting baseline always carries the content-override rule (no clock gate).
        a = self.agent
        d = a._register_directive("en")
        self.assertIn("depth", d.lower())
        self.assertIn("no reset", d.lower())

    def test_work_register_follows_his_lead_not_evade(self):
        # During work time she may be shy, but she must FOLLOW his lead into intimacy —
        # never evade/stop him. Force the 'working' state via recent business.
        from datetime import datetime, timezone
        a = self.agent
        store.kv_set(a.conn, "last_business_at", datetime.now(timezone.utc).isoformat())
        self.assertEqual(a._register_state(), "working")
        d = a._register_directive("en").lower()
        self.assertIn("follow his lead", d)
        self.assertIn("he leads", d)
        self.assertIn("match his intensity", d)        # rises to his energy, not held back
        self.assertIn("only stop if he asks", d)
        self.assertNotIn("save the playfulness", d)    # old deflecting framing is gone
        ru = a._register_directive("ru")
        self.assertIn("ВЕДЁТ ОН", ru)                  # RU: he leads
        self.assertIn("накал", ru)                     # RU: matches his heat/intensity

    def test_roleplay_layer_unlocks_with_closeness_and_stays_non_graphic(self):
        a = self.agent
        # not close yet -> no roleplay directive injected
        store.kv_set(a.conn, "closeness_stage", "0")
        self.assertNotIn("PLAY", a._register_directive("en"))
        # once close -> roleplay capability is present, with the non-graphic ceiling
        store.kv_set(a.conn, "closeness_stage", "3")
        d = a._register_directive("en")
        self.assertIn("take on a role", d)
        self.assertIn("scene or scenario", d)
        self.assertIn("never graphic", d.lower())
        self.assertNotIn("*", a._intimacy_roleplay_directive("en"))  # no asterisk roleplay
        self.assertIn("роль", a._register_directive("ru"))

    def test_intimate_moment_detection(self):
        from datetime import datetime, timezone, timedelta
        a = self.agent
        self.assertTrue(a._is_intimate_message("возьми меня, я твоя"))
        self.assertTrue(a._is_intimate_message("I want you, hold me close"))
        self.assertFalse(a._is_intimate_message("во сколько встреча завтра?"))
        store.kv_set(a.conn, "last_intimate_at", "")
        self.assertFalse(a._in_intimate_moment())
        store.kv_set(a.conn, "last_intimate_at", datetime.now(timezone.utc).isoformat())
        self.assertTrue(a._in_intimate_moment())
        store.kv_set(a.conn, "last_intimate_at",
                     (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
        self.assertFalse(a._in_intimate_moment())   # window passed

    def test_reschedule_rolls_past_time_into_future(self):
        from datetime import datetime, timezone
        conn = self.agent.conn
        rid = store.reminder_add(conn, 1, "Рим Airbnb",
                                 (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(), "none")
        store.kv_set(conn, "last_reminder_id", str(rid))
        past = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_reschedule(1, "ru", {"due_utc": past})   # bare 'перенеси на <past>'
        row = store.reminder_get(conn, rid)
        self.assertGreater(reminders.parse_iso_utc(row["due_utc"]), datetime.now(timezone.utc))

    def test_reminder_held_during_quiet_hours(self):
        import proactive
        conn = self.agent.conn
        due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        store.reminder_add(conn, 1, "благодарности", due, "none")
        sent = []
        with mock.patch.object(self.agent, "reply",
                               side_effect=lambda cid, text, *a, **k: sent.append(text)), \
                mock.patch.object(proactive, "in_quiet_hours", return_value=True):
            self.agent.fire_due_reminders()
        self.assertEqual(sent, [])                          # held through the night
        with mock.patch.object(self.agent, "reply",
                               side_effect=lambda cid, text, *a, **k: sent.append(text)), \
                mock.patch.object(proactive, "in_quiet_hours", return_value=False):
            self.agent.fire_due_reminders()
        self.assertTrue(any("благодарности" in s for s in sent))  # fires once it's morning

    def test_reschedule_ordinal_targets_position_not_last_touched(self):
        from datetime import datetime, timezone
        conn = self.agent.conn
        now = datetime.now(timezone.utc)
        r1 = store.reminder_add(conn, 1, "Азербайджан", (now + timedelta(hours=1)).isoformat(), "none")
        r2 = store.reminder_add(conn, 1, "Рим", (now + timedelta(hours=2)).isoformat(), "none")
        store.kv_set(conn, "last_reminder_id", str(r1))   # last touched = the FIRST one
        newt = (now + timedelta(hours=5)).isoformat()
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_reschedule(1, "ru", {"due_utc": newt}, "перенеси второе на 17:00")
        # "второе" -> the SECOND reminder moved, not the last-touched first one
        self.assertEqual(store.reminder_get(conn, r2)["due_utc"], newt)
        self.assertNotEqual(store.reminder_get(conn, r1)["due_utc"], newt)

    def test_reschedule_clears_fired_marker(self):
        from datetime import datetime, timezone
        conn = self.agent.conn
        rid = store.reminder_add(conn, 1, "пиво", "2026-06-22T18:31:00+00:00", "none")
        conn.execute("UPDATE reminders SET last_fired_at = ? WHERE id = ?",
                     ("2026-06-22T18:31:05+00:00", rid))
        conn.commit()
        self.assertIn("сработало", reminders.reminder_status_mark(store.reminder_get(conn, rid), "ru"))
        store.kv_set(conn, "last_reminder_id", str(rid))
        future = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_reschedule(1, "ru", {"due_utc": future}, "перенеси на 12:00")
        row = store.reminder_get(conn, rid)
        self.assertIsNone(row["last_fired_at"])                              # re-armed
        mark = reminders.reminder_status_mark(row, "ru")
        self.assertNotIn("сработало", mark)                                 # no longer 'fired'
        self.assertIn("перенесено", mark)                                   # shows 're-scheduled'

    def test_meeting_holds_business_notices(self):
        import proactive
        conn = self.agent.conn
        owner = self.agent._owner_chat()
        store.meeting_start(conn, owner, kind="visit")            # a live date
        # a reminder 5h overdue (well past the max-defer) is STILL held for the whole date
        store.reminder_add(conn, owner, "оплата",
                           (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(), "none")
        sent = []
        with mock.patch.object(self.agent, "reply",
                               side_effect=lambda cid, text, *a, **k: sent.append(text)), \
                mock.patch.object(proactive, "in_quiet_hours", return_value=False):
            self.agent.fire_due_reminders()
        self.assertEqual(sent, [])                                # not interrupted
        # a build/deploy notice is also held (and not marked seen -> announces later)
        store.kv_set(conn, "deployed_version", "old")
        with mock.patch.object(self.agent, "build_version", return_value="new"), \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.announce_deploy_if_changed()
        r.assert_not_called()
        self.assertEqual(store.kv_get(conn, "deployed_version"), "old")

    def test_stale_fired_reminders_auto_expire(self):
        from datetime import datetime, timezone
        conn = self.agent.conn
        rid = store.reminder_add(conn, 1, "старое", "2026-06-20T10:00:00+00:00", "none")
        conn.execute("UPDATE reminders SET last_fired_at = ? WHERE id = ?",
                     ("2026-06-20T10:00:05+00:00", rid))   # fired days ago, never acked
        conn.commit()
        self.agent.check_reminder_expiry()
        self.assertEqual(len(store.reminders_active(conn, 1)), 0)   # cleared from the list
        self.assertEqual(store.reminder_get(conn, rid)["status"], "expired")

    def test_delete_after_reminder_list_hints_reminder_cancel(self):
        from datetime import datetime, timezone
        import llm
        conn = self.agent.conn
        store.reminder_add(conn, 1, "пиво", (datetime.now(timezone.utc)).isoformat(), "none")
        store.kv_set(conn, "reminders_listed_at", datetime.now(timezone.utc).isoformat())
        captured = {}

        def cap(cfg, c, skill, messages, **kw):
            captured["user"] = messages[1]["content"]
            return '{"action":"reminder_cancel","params":{"id":1},"confidence":0.9}'
        with mock.patch.object(llm, "chat_profile", side_effect=cap):
            router.route(self.agent.cfg, conn, 1, "удали #1", None)
        self.assertIn("reminder_cancel that reminder", captured["user"])

    def test_reminder_held_during_intimacy_then_delivered(self):
        from datetime import datetime, timezone, timedelta
        conn = self.agent.conn
        due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        store.reminder_add(conn, 1, "благодарности", due, "none")
        sent = []
        with mock.patch.object(self.agent, "reply",
                               side_effect=lambda cid, text, *a, **k: sent.append(text)):
            store.kv_set(conn, "last_intimate_at", datetime.now(timezone.utc).isoformat())
            self.agent.fire_due_reminders()
            self.assertEqual(sent, [])                       # held mid-intimacy
            store.kv_set(conn, "last_intimate_at",
                         (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
            self.agent.fire_due_reminders()
        self.assertTrue(any("благодарности" in s for s in sent))   # delivered once it passed

    def test_reminder_fires_when_overdue_beyond_max_defer(self):
        from datetime import datetime, timezone, timedelta
        conn = self.agent.conn
        due = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()  # > 2h max defer
        store.reminder_add(conn, 1, "оплатить счёт", due, "none")
        sent = []
        with mock.patch.object(self.agent, "reply",
                               side_effect=lambda cid, text, *a, **k: sent.append(text)):
            store.kv_set(conn, "last_intimate_at", datetime.now(timezone.utc).isoformat())
            self.agent.fire_due_reminders()
        self.assertTrue(any("оплатить счёт" in s for s in sent))   # too overdue to keep holding

    def test_converse_context_surfaces_active_reminders(self):
        # She must know her own reminders in conversation, so "почему не закрыла #1?"
        # is answered from the real list (with a fired-but-open one marked), not notes.
        from datetime import datetime, timezone
        conn = self.agent.conn
        # a one-shot that already fired but wasn't confirmed -> still active/open
        rid = store.reminder_add(conn, 1, "пиво для Наташа", "2026-06-22T18:31:00+00:00", "none")
        conn.execute("UPDATE reminders SET last_fired_at = ? WHERE id = ?",
                     ("2026-06-22T18:31:05+00:00", rid))
        conn.commit()
        ctx = self.agent.converse_context("ru", 1)
        self.assertIn("пиво для Наташа", ctx)
        self.assertIn("ждёт «готово»", ctx)               # fired one-shot marked open
        self.assertIn("НАСТОЯЩИЙ список", ctx)             # answer from reminders, not notes

    def test_is_reminder_ack(self):
        import tg_ingest_agent
        f = tg_ingest_agent.Agent._is_reminder_ack
        self.assertTrue(f("готово"))
        self.assertTrue(f("через 30 минут"))
        self.assertTrue(f("✅"))
        self.assertTrue(f(""))
        # content the reminder asked for is NOT an ack
        self.assertFalse(f("запиши благодарность сегодня. классное общение с родными"))
        self.assertFalse(f("классное общение с родственниками и с тобой, день был тёплый"))

    def test_fired_reminder_content_is_saved_not_acked(self):
        import router
        c = self.agent.conn
        store.pending_set(c, 1, "reminder_fired", {"reminder_id": 5, "title": "благодарности"})
        with mock.patch.object(router, "route",
                               return_value={"action": "ingest", "params": {}, "confidence": 0.9}), \
                mock.patch.object(self.agent, "finalize") as fin, \
                mock.patch.object(self.agent, "reply"):
            self.agent.dispatch(1, {"message_id": 9}, "запиши благодарность: классное общение")
        self.assertTrue(fin.called)  # saved as a journal entry, not eaten as the reminder ack

    def test_fired_oneshot_stays_open_and_does_not_refire(self):
        # B5: a fired one-shot is NOT auto-closed (stays visible/pending) and must
        # not fire again on the next sweep.
        from datetime import datetime, timezone, timedelta
        c = self.agent.conn
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        rid = store.reminder_add(c, 1, "посмотреть фильм", past)
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.fire_due_reminders()
            after_first = r.call_count
            self.agent.fire_due_reminders()      # must NOT re-fire
        self.assertEqual(after_first, 1)
        self.assertEqual(r.call_count, 1)
        self.assertEqual(store.reminder_get(c, rid)["status"], "active")  # stays open

    def test_done_closes_fired_oneshot(self):
        # B5: 'готово' now actually closes the fired one-shot (it wasn't closed at fire).
        from datetime import datetime, timezone, timedelta
        c = self.agent.conn
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        rid = store.reminder_add(c, 1, "посмотреть фильм", past)
        with mock.patch.object(self.agent, "reply"):
            self.agent.fire_due_reminders()
            self.agent.resolve_pending(1, "confirm", {}, store.pending_get(c, 1), "ru")
        self.assertEqual(store.reminder_get(c, rid)["status"], "done")

    def test_done_does_not_close_recurring(self):
        from datetime import datetime, timezone, timedelta
        c = self.agent.conn
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        rid = store.reminder_add(c, 1, "благодарности", past, recurrence="daily")
        with mock.patch.object(self.agent, "reply"):
            self.agent.fire_due_reminders()      # advances to next day, stays active
            self.agent.resolve_pending(1, "confirm", {}, store.pending_get(c, 1), "ru")
        self.assertEqual(store.reminder_get(c, rid)["status"], "active")  # recurring NOT closed

    def test_snooze_rearms_original_no_new_row(self):
        # B4: snooze re-arms the ORIGINAL reminder (keeps id), never spawns a new row.
        from datetime import datetime, timezone, timedelta
        c = self.agent.conn
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        rid = store.reminder_add(c, 1, "позвонить Ире", past)
        with mock.patch.object(self.agent, "reply"):
            self.agent.fire_due_reminders()
            self.agent.resolve_pending(1, "amend", {"snooze_minutes": 60},
                                       store.pending_get(c, 1), "ru")
        rows = c.execute("SELECT id, status FROM reminders WHERE chat_id = 1").fetchall()
        self.assertEqual(len(rows), 1)               # original re-armed, NOT a new row
        self.assertEqual(rows[0]["id"], rid)
        self.assertEqual(store.reminder_get(c, rid)["status"], "active")  # pending again (future)

    def test_sticker_store_and_pick(self):
        c = self.agent.conn
        store.stickers_add(c, "pack1", [
            {"file_id": "F1", "file_unique_id": "u1", "emoji": "😍"},
            {"file_id": "F2", "file_unique_id": "u2", "emoji": "🔥"}])
        store.stickers_add(c, "pack1", [{"file_id": "F1", "file_unique_id": "u1", "emoji": "😍"}])  # dedup
        self.assertEqual(store.sticker_count(c), 2)
        self.assertEqual(store.sticker_for_emoji(c, "😍"), "F1")
        self.assertIsNone(store.sticker_for_emoji(c, "👽"))

    def test_save_sticker_pack_action(self):
        import tg_ingest_agent
        store.kv_set(self.agent.conn, "last_sticker_set", "MyPack")
        packed = {"stickers": [{"file_id": "F1", "file_unique_id": "u1", "emoji": "😍"},
                               {"file_id": "F2", "file_unique_id": "u2", "emoji": "🔥"}]}
        with mock.patch.object(tg_ingest_agent, "tg_call", return_value=packed), \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.do_save_sticker_pack(1, "ru")
        self.assertEqual(store.sticker_count(self.agent.conn), 2)
        self.assertIn("MyPack", r.call_args[0][1])

    def test_sticker_pack_link_saves_pack(self):
        import tg_ingest_agent
        m = tg_ingest_agent.Agent.STICKER_LINK_RE.search("https://t.me/addstickers/CutieMadeline")
        self.assertEqual(m.group(1), "CutieMadeline")
        packed = {"title": "Cutie Madeline",
                  "stickers": [{"file_id": "F1", "file_unique_id": "u1", "emoji": "😍"}]}
        with mock.patch.object(tg_ingest_agent, "tg_call", return_value=packed), \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.do_save_sticker_pack(1, "ru", set_name="CutieMadeline")
        self.assertEqual(store.sticker_count(self.agent.conn), 1)
        self.assertEqual(store.kv_get(self.agent.conn, "last_sticker_set"), "CutieMadeline")
        self.assertIn("Cutie Madeline", r.call_args[0][1])

    def test_send_sticker_action(self):
        import tg_ingest_agent
        store.stickers_add(self.agent.conn, "p", [{"file_id": "F1", "file_unique_id": "u1", "emoji": "😍"}])
        with mock.patch.object(tg_ingest_agent, "tg_send_sticker") as ss, \
                mock.patch.object(self.agent, "reply"):
            self.agent.do_send_sticker(1, "ru")
        self.assertEqual(ss.call_args[0][2], "F1")
        self.agent.conn.execute("DELETE FROM stickers")
        self.agent.conn.commit()
        with mock.patch.object(tg_ingest_agent, "tg_send_sticker") as ss2, \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.do_send_sticker(1, "ru")
        self.assertFalse(ss2.called)  # nothing to send
        self.assertTrue(r.called)     # warm 'none yet' reply

    def test_sticker_pick_anti_repeat(self):
        c = self.agent.conn
        store.stickers_add(c, "p", [
            {"file_id": "F1", "file_unique_id": "u1", "emoji": "😍"},
            {"file_id": "F2", "file_unique_id": "u2", "emoji": "😍"}])
        # excluding one of two matches returns the OTHER
        self.assertEqual(store.sticker_pick(c, "😍", exclude_uid="u1")["file_unique_id"], "u2")
        # if the only match is excluded, it's allowed (better a repeat than nothing)
        store.stickers_add(c, "p", [{"file_id": "F3", "file_unique_id": "u3", "emoji": "🔥"}])
        self.assertEqual(store.sticker_pick(c, "🔥", exclude_uid="u3")["file_unique_id"], "u3")

    def test_send_sticker_for_avoids_immediate_repeat(self):
        import tg_ingest_agent
        c = self.agent.conn
        store.stickers_add(c, "p", [
            {"file_id": "F1", "file_unique_id": "u1", "emoji": "😍"},
            {"file_id": "F2", "file_unique_id": "u2", "emoji": "😍"}])
        sent = []
        with mock.patch.object(tg_ingest_agent, "tg_send_sticker",
                               side_effect=lambda *a, **k: sent.append(a[2])):
            self.agent.send_sticker_for(1, "😍")
            self.agent.send_sticker_for(1, "😍")     # must not repeat the first
        self.assertEqual(len(sent), 2)
        self.assertNotEqual(sent[0], sent[1])
        self.assertEqual(store.kv_get(c, "last_sticker_uid"), "u2"
                         if sent[1] == "F2" else "u1")

    def test_sticker_descriptions_store_and_surface(self):
        c = self.agent.conn
        store.stickers_add(c, "p", [
            {"file_id": "F1", "file_unique_id": "u1", "emoji": "😍",
             "description": "девушка краснеет и шлёт сердечко"},
            {"file_id": "F2", "file_unique_id": "u2", "emoji": "🔥"}])
        und = store.stickers_undescribed(c)
        self.assertEqual([r["file_unique_id"] for r in und], ["u2"])
        store.sticker_set_description(c, "u2", "огонь, всё горит")
        self.assertEqual(store.stickers_undescribed(c), [])
        ctx = self.agent.converse_context("ru", 1)
        self.assertIn("девушка краснеет", ctx)            # real picture surfaced
        self.assertIn("real picture", ctx)                # instruction to pick by meaning

    def test_run_describe_stickers_uses_vision(self):
        import tg_ingest_agent
        c = self.agent.conn
        self.agent.cfg.vision_model = "vmodel"
        store.stickers_add(c, "p", [
            {"file_id": "F1", "file_unique_id": "u1", "emoji": "😍",
             "thumbnail": {"file_id": "T1"}}])
        with mock.patch.object(self.agent, "download_file", return_value="/tmp/x.webp"), \
                mock.patch.object(llm, "describe_image",
                                  return_value="девушка целует воздух") as di:
            res = self.agent.run_describe_stickers()
        self.assertEqual(res["described"], 1)
        di.assert_called()                                 # vision actually consulted
        row = c.execute("SELECT description FROM stickers WHERE file_unique_id='u1'").fetchone()
        self.assertIn("целует", row["description"])

    def test_describe_image_sniffs_webp_mime(self):
        webp = b"RIFF\x00\x00\x00\x00WEBPVP8 "
        self.assertEqual(llm._sniff_image_mime(webp), "image/webp")
        self.assertEqual(llm._sniff_image_mime(b"\xff\xd8\xff\xe0xx"), "image/jpeg")

    def test_cara_photo_library_save_and_selfie(self):
        import tg_ingest_agent
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_save_cara_photo(1, "ru", {"photo": [{"file_id": "P1", "file_unique_id": "pu1"}]})
        self.assertEqual(store.cara_photo_count(self.agent.conn), 1)
        with mock.patch.object(tg_ingest_agent, "tg_send_photo") as sp, \
                mock.patch.object(self.agent, "reply"):
            self.agent.do_cara_selfie(1, "ru")
        self.assertEqual(sp.call_args[0][2], "P1")  # sent the saved file_id

    def test_ask_prompt_uses_hermes_business_register(self):
        import knowledge
        msgs = knowledge.build_ask_messages(
            "когда рейс?", [{"message_id": 1, "text": "рейс в 10:00", "category": "Travel",
                            "title": None}])
        sys = msgs[0]["content"]
        self.assertIn("HERMES", sys)                          # business register, not warm persona
        self.assertIn("crisp", sys.lower())
        self.assertNotIn("personal assistant", sys.lower())  # still not a generic 'assistant'
        self.assertIn("Never call yourself an AI", sys)      # no AI/bot disclaimer
        self.assertIn("рейс в 10:00", sys)                   # grounding still present

    def test_converse_grounding_uses_stored_facts(self):
        # The guardrail: converse is GIVEN the boss's real saved entries so it can use
        # facts instead of inventing them. No index -> no grounding; with a match it's
        # surfaced verbatim and labelled as FACTS.
        import knowledge
        with mock.patch.object(store, "all_embedded_chunks", return_value=[]):
            self.assertEqual(self.agent._converse_grounding("за что я был благодарен?"), "")
        with mock.patch.object(store, "all_embedded_chunks", return_value=[{"x": 1}]), \
                mock.patch.object(llm, "embed", return_value=[[0.1, 0.2]]), \
                mock.patch.object(knowledge, "rank_chunks",
                                  return_value=[{"category": "Благодарность",
                                                 "text": "созвон с Димой из Дубаев"}]):
            g = self.agent._converse_grounding("за что я был благодарен?")
        self.assertIn("Димой", g)        # the real fact, verbatim
        self.assertIn("FACTS", g)        # labelled so the model treats it as ground truth

    def test_clarify_stays_in_voice_not_template(self):
        # A low-confidence/clarify route must NOT snap into the formal template
        # mid-conversation — it stays in Cara's warm voice (do_converse).
        with mock.patch.object(router, "route",
                               return_value={"action": "clarify", "params": {}, "confidence": 0.75}), \
                mock.patch.object(self.agent, "do_converse") as dc:
            self.agent.dispatch(1, {}, "давай во вторник")
        dc.assert_called_once()

    def test_daily_greeting_fires_once_and_respects_prior_contact(self):
        import proactive
        self.agent.cfg.morning_brief_hour = 0   # any hour qualifies as "morning enough"
        store.pref_set(self.agent.conn, "proactive_enabled", "true")
        with mock.patch.object(proactive, "in_quiet_hours", return_value=False), \
                mock.patch.object(self.agent, "compose_morning_greeting", return_value="Доброе утро, солнце ☀️"), \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.check_daily_greeting()        # due -> greets
            fired = r.called
            r.reset_mock()
            self.agent.check_daily_greeting()        # same day -> silent
            again = r.called
        self.assertTrue(fired)
        self.assertFalse(again)

    def test_daily_greeting_skipped_when_boss_contacted_first(self):
        import proactive
        self.agent.cfg.morning_brief_hour = 0
        store.pref_set(self.agent.conn, "proactive_enabled", "true")
        self.agent.mark_contact_day()   # boss already connected today
        with mock.patch.object(proactive, "in_quiet_hours", return_value=False), \
                mock.patch.object(self.agent, "compose_morning_greeting", return_value="x"), \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.check_daily_greeting()
        self.assertFalse(r.called)

    def test_out_of_scope_is_warm_not_template(self):
        with mock.patch.object(router, "route",
                               return_value={"action": "out_of_scope", "params": {}, "confidence": 0.95}), \
                mock.patch.object(llm, "chat_profile", return_value="Ой, давай лучше я помогу с делами 🙂") as cp, \
                mock.patch.object(self.agent, "send_chat_action"), \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.dispatch(1, {}, "напиши эссе про Канта")
        cp.assert_called_once()
        self.assertEqual(r.call_args[0][1], "Ой, давай лучше я помогу с делами 🙂")


class ConverseModuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "c.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_seed_life_idempotent_and_in_prompt(self):
        converse.seed_life(self.conn)
        n = store.life_count(self.conn)
        converse.seed_life(self.conn)  # idempotent
        self.assertEqual(store.life_count(self.conn), n)
        self.assertGreater(n, 0)
        sys_ru = converse.build_system(self.conn, "ru")
        self.assertIn("Cara", sys_ru)
        self.assertIn("Russian", sys_ru)              # language-match directive
        self.assertIn("Майя", sys_ru)                 # a seeded life fact surfaces

    def test_life_seed_no_tea_fixation(self):
        # D1: tea is no longer hardcoded in CHARACTER nor an emphatic seed life fact.
        converse.seed_life(self.conn)
        facts = " ".join(r["text"] for r in store.life_facts(self.conn, limit=40))
        self.assertNotIn("чёрный чай", facts)
        self.assertNotIn("чайник", facts)
        self.assertNotIn("strong tea", converse.CHARACTER)

    def test_migration_rebalances_old_tea_seeds(self):
        # D1: an existing DB still holding the emphatic tea seed gets it rewritten.
        store.life_add(self.conn, "habit",
                       "Завариваешь крепкий чёрный чай и почти никогда не пьёшь кофе.")
        store._migrate(self.conn)
        facts = " ".join(r["text"] for r in store.life_facts(self.conn, limit=40))
        self.assertNotIn("крепкий чёрный чай", facts)

    def test_persona_forbids_fabricating_business_actions(self):
        # Phase A truthful boundary: converse must never claim it did a system
        # action (the "Готово, поменяла" lie). The guard text must be present.
        sys_ru = converse.build_system(self.conn, "ru")
        self.assertIn("never claim you DID something", sys_ru)
        self.assertIn("поменяла", sys_ru)   # the fake-confirmation words it must not emit
        self.assertIn("перенесла", sys_ru)

    def test_system_prompt_language_and_name(self):
        store.pref_set(self.conn, "owner_name_ru", "Олег")
        store.pref_set(self.conn, "owner_name_en", "Owen")
        sys_en = converse.build_system(self.conn, "en")
        self.assertIn("English", sys_en)
        self.assertIn("Owen", sys_en)                 # boss's name is in context
        # she's instructed to be fully human with no AI disclaimers, ever
        low = sys_en.lower()
        self.assertIn("human", low)
        self.assertIn("disclaimer", low)
        self.assertIn("not an ai", low)

    def test_history_becomes_chat_turns(self):
        converse.seed_life(self.conn)
        store.convo_add(self.conn, 1, "user", "привет")
        store.convo_add(self.conn, 1, "bot", "привет!")
        store.convo_add(self.conn, 1, "user", "как ты?")
        msgs = converse.build_messages(self.conn, 1, "ru")
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[-1], {"role": "user", "content": "как ты?"})
        self.assertEqual(msgs[2]["role"], "assistant")  # bot turn mapped


class NameHandlingTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(DB_PATH=str(Path(self.tmp.name) / "n.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_store_owner_name_splits_scripts(self):
        self.agent.store_owner_name("Олег / Owen")
        self.assertEqual(store.pref_get(self.conn, "owner_name_ru"), "Олег")
        self.assertEqual(store.pref_get(self.conn, "owner_name_en"), "Owen")

    def test_legacy_combined_name_migrated_on_start(self):
        import tg_ingest_agent
        dbp = Path(self.tmp.name) / "legacy.db"
        c = store.open_db(dbp)
        store.pref_set(c, "owner_name", "Олег (Owen)")  # old single-pref form
        c.close()
        cfg = make_config(DB_PATH=str(dbp), MEDIA_DIR=str(Path(self.tmp.name) / "m2"))
        ag = tg_ingest_agent.Agent(cfg)
        try:
            self.assertEqual(store.pref_get(ag.conn, "owner_name_ru"), "Олег")
            self.assertEqual(store.pref_get(ag.conn, "owner_name_en"), "Owen")
        finally:
            ag.conn.close()

    def test_profile_surfaces_the_name(self):
        # the screenshot bug: name stored but "что ты обо мне знаешь" showed nothing.
        self.agent.store_owner_name("Олег / Owen")
        out_ru = boss_model.render_profile(self.conn, "ru")
        self.assertIn("Олег", out_ru)
        self.assertIn("Вас зовут", out_ru)
        out_en = boss_model.render_profile(self.conn, "en")
        self.assertIn("Owen", out_en)
        self.assertIn("Your name is", out_en)


class LanguageDetectionTests(unittest.TestCase):
    def test_detects_message_language(self):
        self.assertEqual(common.detect_lang("привет, как дела?"), "ru")
        self.assertEqual(common.detect_lang("hello there"), "en")
        self.assertIsNone(common.detect_lang("123 :) 🙂"))  # uncertain -> caller falls back
        self.assertEqual(common.detect_lang("ok да"), "ru")  # tie -> Russian

    def test_english_terms_dont_flip_a_russian_message(self):
        # the screenshot bug: a long English term outweighed the Russian by letters
        self.assertEqual(common.detect_lang("Когда у нас performance review?"), "ru")
        self.assertEqual(common.detect_lang("посмотри DeepSeek"), "ru")
        self.assertEqual(common.detect_lang("во сколько мой flight?"), "ru")
        # a genuinely English message with one Russian word stays English
        self.assertEqual(common.detect_lang("remind Олег tomorrow morning"), "en")
        self.assertEqual(common.detect_lang("what's the plan for today?"), "en")


class BossQueryTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(DB_PATH=str(Path(self.tmp.name) / "b.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_profile_facts_dedups_near_duplicates(self):
        import boss_model
        store.pref_set(self.conn, "owner_name_ru", "Олег")
        store.pref_set(self.conn, "owner_name_en", "Owen")
        store.boss_add(self.conn, "relationship_note",
                       "Он надёжный, держит слово, и люди ему доверяют.", status="confirmed")
        store.boss_add(self.conn, "relationship_note",
                       "Он надёжный, держит слово, люди доверяют.", status="inferred")  # near-dup
        store.boss_add(self.conn, "tone", "Ценит честность.", status="inferred")
        name, confirmed, inferred = boss_model.profile_facts(self.conn, "ru")
        self.assertEqual(name, "Олег / Owen")
        self.assertEqual(len(confirmed), 1)
        self.assertNotIn("Он надёжный, держит слово, люди доверяют.", inferred)  # dropped
        self.assertIn("Ценит честность.", inferred)

    def test_boss_query_is_warm_and_id_free(self):
        store.boss_add(self.conn, "tone", "Ценит честность.", status="confirmed")
        warm = "Ну, кое-что уже знаю про тебя 🙂 Ты ценишь честность. Поправь, если что."
        with mock.patch.object(llm, "chat_profile", return_value=warm) as cp, \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.do_boss_query(1, "ru")
        cp.assert_called_once()
        self.assertEqual(cp.call_args.kwargs["profile"], "converse_warm")
        self.assertEqual(r.call_args[0][1], warm)
        self.assertNotIn("#", r.call_args[0][1])           # no #id dump, no status headers

    def test_boss_query_empty_is_warm_no_llm(self):
        with mock.patch.object(llm, "chat_profile") as cp, \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.do_boss_query(1, "ru")
        cp.assert_not_called()
        self.assertEqual(r.call_args[0][1], texts.T("ru", "boss_query_empty"))

    def test_is_duplicate(self):
        import boss_model
        store.boss_add(self.conn, "tone", "Ценит честность и прямоту.", status="inferred")
        self.assertTrue(boss_model.is_duplicate(self.conn, "Ценит честность, прямоту."))
        self.assertFalse(boss_model.is_duplicate(self.conn, "Любит джаз по вечерам."))


class SttNoiseTests(unittest.TestCase):
    def test_detects_whisper_hallucinations(self):
        for noise in ("[Subscribe]", "[ Music ]", "(applause)", "♪♪♪", "...", "",
                      "Спасибо за просмотр!", "Subtitles by the Amara.org community"):
            self.assertTrue(common.is_stt_noise(noise), noise)
        for real in ("привет, как дела?", "позвони в банк завтра",
                     "[важно] перезвони мне", "напомни про встречу"):
            self.assertFalse(common.is_stt_noise(real), real)

    def test_transcribe_voice_rejects_hallucination(self):
        import tg_ingest_agent
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = make_config(DB_PATH=str(Path(tmp.name) / "v.db"),
                          MEDIA_DIR=str(Path(tmp.name) / "m"))
        agent = tg_ingest_agent.Agent(cfg)
        self.addCleanup(agent.conn.close)
        with mock.patch.object(agent, "download_file", return_value=str(Path(tmp.name) / "x.oga")), \
                mock.patch.object(llm, "transcribe", return_value="[Subscribe]"), \
                mock.patch.object(agent, "send_chat_action"), \
                mock.patch.object(agent, "reply") as r:
            out = agent.transcribe_voice(1, {"file_id": "f", "file_unique_id": "u", "duration": 9})
        self.assertIsNone(out)  # garbage is never dispatched as a real message
        self.assertEqual(r.call_args[0][1], texts.T("ru", "stt_failed"))


class ConversationLearningTests(unittest.TestCase):
    """The memory pass that grows Cara's life and learns about the boss from
    free chat."""

    def setUp(self):
        import memory_curator
        self.memory_curator = memory_curator
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "l.db")
        self.cfg = make_config()
        converse.seed_life(self.conn)
        store.convo_add(self.conn, 1, "user", "я терпеть не могу длинные ответы")
        store.convo_add(self.conn, 1, "bot", "поняла! а я как раз учусь печь хлеб 🥖")
        store.convo_add(self.conn, 1, "user", "кстати у меня аллергия на орехи")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_extraction_routes_by_sensitivity(self):
        payload = (
            '{"cara_life": [{"kind": "hobby", "text": "Ты учишься печь хлеб."}],'
            ' "boss_facts": [{"kind": "tone", "text": "Не любит длинные ответы."},'
            '                {"kind": "personal_fact", "text": "Аллергия на орехи."}]}'
        )
        before = store.life_count(self.conn)
        with mock.patch.object(llm, "chat_profile", return_value=payload):
            result = self.memory_curator.curate_conversation(self.conn, self.cfg, 1)
        # Cara's life grew (auto-stored, it's her own fiction)
        self.assertEqual(store.life_count(self.conn), before + 1)
        self.assertEqual(result["life"], 1)
        # benign boss fact -> learned as a correctable inferred item
        inferred = [r["value"] for r in store.boss_items(self.conn, "inferred")]
        self.assertIn("Не любит длинные ответы.", inferred)
        # sensitive boss fact -> NOT auto-stored; a confirm-first candidate
        cand = [c["proposed_text"] for c in store.candidates_pending(self.conn)]
        self.assertIn("Аллергия на орехи.", cand)
        self.assertNotIn("Аллергия на орехи.",
                         [r["value"] for r in store.boss_items(self.conn, "inferred")])

    def test_extraction_dedups_on_rerun(self):
        payload = '{"cara_life": [{"kind": "hobby", "text": "Ты учишься печь хлеб."}], "boss_facts": []}'
        with mock.patch.object(llm, "chat_profile", return_value=payload):
            self.memory_curator.curate_conversation(self.conn, self.cfg, 1)
            again = self.memory_curator.curate_conversation(self.conn, self.cfg, 1)
        self.assertEqual(again["life"], 0)  # UNIQUE life text -> no duplicate

    def test_correction_is_learned_logged_and_injected(self):
        import boss_model
        import converse
        rule = "Отвечай на том языке, на котором он пишет."
        payload = ('{"cara_life": [], "boss_facts": [],'
                   ' "corrections": [{"kind": "workflow", "text": "' + rule + '"}]}')
        with mock.patch.object(llm, "chat_profile", return_value=payload):
            result = self.memory_curator.curate_conversation(self.conn, self.cfg, 1)
        self.assertEqual(result["corrections"], 1)
        # stored as a correctable standing-guidance item...
        self.assertIn(rule, [r["value"] for r in store.boss_items(self.conn, "inferred")])
        # ...logged as an issue so recurring mistakes surface in the weekly review...
        n = self.conn.execute("SELECT COUNT(*) AS n FROM issues WHERE kind='correction'").fetchone()["n"]
        self.assertEqual(n, 1)
        # ...and reaches her conversation prompt so she honours it next turn
        self.assertTrue(any(rule in g for g in boss_model.standing_guidance(self.conn)))
        self.assertIn(rule, converse.build_system(self.conn, "ru"))

    def test_correction_not_relogged_on_rerun(self):
        payload = ('{"cara_life": [], "boss_facts": [],'
                   ' "corrections": [{"kind": "tone", "text": "Будь короче."}]}')
        with mock.patch.object(llm, "chat_profile", return_value=payload):
            self.memory_curator.curate_conversation(self.conn, self.cfg, 1)
            self.memory_curator.curate_conversation(self.conn, self.cfg, 1)
        n = self.conn.execute("SELECT COUNT(*) AS n FROM issues WHERE kind='correction'").fetchone()["n"]
        self.assertEqual(n, 1)  # already-known correction not re-logged

    def test_recurring_correction_escalates_to_needs_code(self):
        rule = "Отвечай на том языке, на котором он пишет."
        payload = ('{"cara_life": [], "boss_facts": [],'
                   ' "corrections": [{"kind": "workflow", "text": "' + rule + '"}]}')
        with mock.patch.object(llm, "chat_profile", return_value=payload):
            first = self.memory_curator.curate_conversation(
                self.conn, self.cfg, 1, correction_mode=True)
            # he corrects the SAME thing again -> it's not self-fixable
            again = self.memory_curator.curate_conversation(
                self.conn, self.cfg, 1, correction_mode=True)
        self.assertEqual(first["learned"], [rule])
        self.assertEqual(first["unresolved"], [])
        self.assertEqual(again["learned"], [])
        self.assertEqual(again["unresolved"], [rule])     # escalated
        n = self.conn.execute(
            "SELECT COUNT(*) AS n FROM issues WHERE kind='correction_unresolved'").fetchone()["n"]
        self.assertEqual(n, 1)

    def test_corrections_report_lists_both(self):
        import review
        store.boss_add(self.conn, "workflow", "Отвечай на его языке.", status="inferred",
                       source_table="correction")
        store.issue_add(self.conn, 1, "correction_unresolved", "Не переключай язык.")
        report = review.corrections_report(self.conn, "ru")
        self.assertIn("Отвечай на его языке.", report)        # auto-applied
        self.assertIn("Не переключай язык.", report)          # needs a code fix
        self.assertIn("код", report.lower())


class ProactiveTests(unittest.TestCase):
    def setUp(self):
        import proactive
        self.proactive = proactive
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "p.db")
        self.cfg = make_config()  # tz +3, quiet 22-8, max 1/day, enabled

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _now_local(self, local_hour):
        # cfg.timezone_offset defaults to +3, so UTC = local - 3
        from datetime import datetime, timezone
        return datetime(2026, 6, 15, (local_hour - 3) % 24, 0, tzinfo=timezone.utc)

    def test_quiet_hours_wrap_midnight(self):
        self.assertTrue(self.proactive.in_quiet_hours(self.cfg, self.conn, self._now_local(23)))
        self.assertTrue(self.proactive.in_quiet_hours(self.cfg, self.conn, self._now_local(7)))
        self.assertFalse(self.proactive.in_quiet_hours(self.cfg, self.conn, self._now_local(12)))

    def test_sends_one_nudge_and_logs(self):
        store.candidate_add(self.conn, "workflow", "auto-file X", confidence=0.9)
        sent = []
        key = self.proactive.run(self.conn, self.cfg, "ru", sent.append, now=self._now_local(12))
        self.assertEqual(key, "candidates")
        self.assertEqual(len(sent), 1)
        self.assertEqual(store.proactive_sent_count(self.conn, "2026-06-15"), 1)

    def test_daily_cap_blocks_second_nonurgent(self):
        store.candidate_add(self.conn, "workflow", "auto-file X", confidence=0.9)
        self.conn.execute("INSERT INTO messages (chat_id, tg_message_id, received_at, status,"
                          " suggested_category) VALUES (1, 1, ?, 'suggested', 'news')",
                          (store._now(),))
        self.conn.commit()
        first = self.proactive.run(self.conn, self.cfg, "ru", lambda t: None, now=self._now_local(12))
        second = self.proactive.run(self.conn, self.cfg, "ru", lambda t: None, now=self._now_local(13))
        self.assertEqual(first, "candidates")
        self.assertIsNone(second)  # max_per_day=1 reached

    def test_quiet_hours_suppress_nonurgent(self):
        store.candidate_add(self.conn, "workflow", "auto-file X", confidence=0.9)
        sent = []
        key = self.proactive.run(self.conn, self.cfg, "ru", sent.append, now=self._now_local(23))
        self.assertIsNone(key)
        self.assertEqual(sent, [])

    def test_overdue_is_urgent_and_bypasses_cap(self):
        from datetime import datetime, timezone, timedelta
        past = (datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc) - timedelta(days=1)).isoformat()
        store.reminder_add(self.conn, 1, "call the bank", past)
        # cap already spent today by a non-urgent nudge
        store.proactive_log_add(self.conn, "candidates", "sent", sent=True)
        sent = []
        key = self.proactive.run(self.conn, self.cfg, "ru", sent.append, now=self._now_local(12))
        self.assertEqual(key, "overdue")        # urgent fires despite the cap
        self.assertEqual(len(sent), 1)


class MaintenanceJobTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(DB_PATH=str(Path(self.tmp.name) / "m.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "media"))
        self.agent = tg_ingest_agent.Agent(cfg)

    def tearDown(self):
        self.agent.conn.close()
        self.tmp.cleanup()

    def test_handlers_registered_for_all_job_kinds(self):
        import runtime
        import jobs
        for skill, action in jobs.JOB_KINDS:
            self.assertIn((skill, action), runtime._HANDLERS, f"no handler for {skill}/{action}")

    def test_enqueue_is_idempotent(self):
        self.agent.enqueue_maintenance_jobs()
        self.agent.enqueue_maintenance_jobs()  # second call must not double-queue
        n = self.agent.conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE skill='maintenance' AND status='pending'"
        ).fetchone()["n"]
        self.assertEqual(n, 3)  # one per action, not six

    def test_deploy_notice_fires_only_on_version_change(self):
        with mock.patch.object(self.agent, "reply") as reply:
            with mock.patch.object(self.agent, "build_version", return_value="v1"):
                self.agent.announce_deploy_if_changed()      # new build -> announce
                self.agent.announce_deploy_if_changed()      # same build -> quiet (reboot)
            with mock.patch.object(self.agent, "build_version", return_value="v2"):
                self.agent.announce_deploy_if_changed()      # changed -> announce again
            with mock.patch.object(self.agent, "build_version", return_value=""):
                self.agent.announce_deploy_if_changed()      # no VERSION (dev) -> quiet
        self.assertEqual(reply.call_count, 2)
        self.assertIn("обновлен", reply.call_args[0][1].lower())  # the ru deploy notice

    def test_drain_runs_maintenance_and_expires_pending(self):
        import runtime
        store.pending_set(self.agent.conn, 1, "category", {"row_id": 1}, ttl_seconds=-10)  # stale
        self.agent.enqueue_maintenance_jobs()
        runtime.drain(self.agent.conn, self.agent)
        # every maintenance job completed durably
        left = self.agent.conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status != 'done'").fetchone()["n"]
        self.assertEqual(left, 0)
        # the abandoned pending action was swept by the pending_expire job
        self.assertEqual(
            self.agent.conn.execute("SELECT COUNT(*) AS n FROM pending_actions").fetchone()["n"], 0)


class ReactionAndContextTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "r.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_react_validates_emoji_and_target(self):
        import tg_ingest_agent
        with mock.patch.object(tg_ingest_agent, "tg_set_reaction") as sr:
            self.agent.react(1, 99, "🔥")        # valid
            self.agent.react(1, 99, "🦖")        # not in palette -> skipped
            self.agent.react(1, None, "🔥")      # no target -> skipped
        sr.assert_called_once()
        self.assertEqual(sr.call_args[0][2], 99)
        self.assertEqual(sr.call_args[0][3], "🔥")

    def test_incoming_reaction_logged_and_learned(self):
        self.agent.handle_reaction({"chat": {"id": 1}, "user": {"id": 1}, "message_id": 5,
                                    "new_reaction": [{"type": "emoji", "emoji": "👎"}]})
        self.assertEqual(store.kv_get(self.conn, "last_reaction"), "👎")
        n = self.conn.execute(
            "SELECT COUNT(*) AS n FROM issues WHERE kind='negative_reaction'").fetchone()["n"]
        self.assertEqual(n, 1)                                  # negative reaction is a signal
        self.assertTrue(store.rel_recent(self.conn, "2000-01-01"))

    def test_incoming_reaction_owner_gated(self):
        self.agent.handle_reaction({"chat": {"id": 1}, "user": {"id": 999}, "message_id": 5,
                                    "new_reaction": [{"type": "emoji", "emoji": "👍"}]})
        self.assertIsNone(store.kv_get(self.conn, "last_reaction"))

    def test_converse_reaction_tag_applied_and_stripped(self):
        import tg_ingest_agent
        with mock.patch.object(llm, "chat_profile", return_value="[[react:🔥]] огонь, босс!"), \
                mock.patch.object(self.agent, "send_chat_action"), \
                mock.patch.object(tg_ingest_agent, "tg_set_reaction") as sr, \
                mock.patch.object(self.agent, "maybe_curate_conversation"), \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.do_converse(1, "ru", "я закрыл сделку!", message_id=42)
        sr.assert_called_once()
        self.assertEqual(sr.call_args[0][3], "🔥")
        self.assertEqual(r.call_args[0][1], "огонь, босс!")     # the tag is stripped from text

    def test_part_of_day_and_context(self):
        self.assertEqual(common.part_of_day(8, "ru"), "утро")
        self.assertEqual(common.part_of_day(14, "ru"), "день")
        self.assertEqual(common.part_of_day(20, "ru"), "вечер")
        self.assertEqual(common.part_of_day(2, "ru"), "ночь")
        self.assertEqual(common.part_of_day(8, "en"), "morning")
        ctx = self.agent.converse_context("ru")
        self.assertRegex(ctx, r"\d{2}:\d{2}")                  # a clock time
        self.assertTrue(any(p in ctx for p in ("утро", "день", "вечер", "ночь")))

    def test_router_points_at_recent_item_for_this(self):
        mid = store.insert_message(self.conn, {"chat_id": 1, "tg_message_id": 1,
                                               "received_at": store._now(), "raw_text": "Расписка.pdf"})
        store.set_suggestion(self.conn, mid, "uncategorized", "filename only", "m")
        captured = {}

        def fake_chat(cfg, conn, skill, messages, **kw):
            captured["user"] = messages[1]["content"]
            return ('{"action":"recategorize","params":{"id":%d,"category":"Документы"},'
                    '"confidence":0.9}' % mid)

        with mock.patch.object(llm, "chat_profile", side_effect=fake_chat):
            router.route(self.agent.cfg, self.conn, 1, "переложи это в Документы", None)
        self.assertIn(f"#{mid}", captured["user"])             # item pointed out to the router
        self.assertIn("recently saved", captured["user"])


class ReportFixesTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "x.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_low_confidence_routes_to_converse_not_clarify(self):
        with mock.patch.object(llm, "chat_profile",
                               return_value='{"action":"reminder_create","params":{},"confidence":0.3}'):
            d = router.route(self.agent.cfg, self.conn, 1, "что-то невнятное", None)
        self.assertEqual(d["action"], "converse")        # warm chat, not cold clarify

    def test_budget_set_changes_limit(self):
        import llm
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_budget_set(1, "ru", {"period": "day", "amount": 3})
        self.assertEqual(store.pref_get(self.conn, "budget_daily_usd"), "3.0")
        daily, _ = llm.budget_limits(self.agent.cfg, self.conn)
        self.assertEqual(daily, 3.0)
        self.assertIn("3.00", r.call_args[0][1])
        self.assertTrue(skill_manifest.known("budget_set"))
        self.assertEqual(router.validate_route(
            {"action": "budget_set", "params": {"period": "day", "amount": 3}}, False)["action"],
            "budget_set")

    def test_budget_set_rejects_garbage(self):
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_budget_set(1, "ru", {"amount": "не число"})
        self.assertIsNone(store.pref_get(self.conn, "budget_daily_usd"))
        self.assertEqual(r.call_args[0][1], texts.T("ru", "budget_set_unclear"))

    def test_category_prompt_prefers_existing_and_language(self):
        import ingest
        sys = ingest.build_llm_messages(
            self.agent.cfg, ["News", "Crypto"], "<text>", [], None, "ru")[0]["content"]
        self.assertIn("USE ONE OF THESE", sys)
        self.assertIn("Russian", sys)
        self.assertNotIn("in English", sys)              # no longer forces English names

    def test_voice_too_big_message(self):
        from tg_api import TelegramError
        with mock.patch.object(self.agent, "download_file", side_effect=TelegramError(
                "getFile failed with HTTP 400: Bad Request: file is too big", status=400)), \
                mock.patch.object(self.agent, "send_chat_action"), \
                mock.patch.object(self.agent, "reply") as r:
            out = self.agent.transcribe_voice(1, {"file_id": "f", "file_unique_id": "u",
                                                  "duration": 120})
        self.assertIsNone(out)
        self.assertEqual(r.call_args[0][1], texts.T("ru", "stt_too_big"))

    def test_pdf_text_extraction_best_effort(self):
        import pdftext
        pdf = (b"%PDF-1.4\nstream\nBT (Hello world, this is a readable test document.) Tj ET\n"
               b"endstream\n%%EOF")
        self.assertIn("readable test document", pdftext.extract_text(pdf))
        # no text layer (scanned/binary) or non-PDF -> empty, never garbage
        self.assertEqual(pdftext.extract_text(b"%PDF-1.4\nstream\n\x01\x02\x03nope\nendstream"), "")
        self.assertEqual(pdftext.extract_text(b"not a pdf"), "")


class GoldenTranscriptTests(unittest.TestCase):
    """Replayable end-to-end scenarios: feed updates through handle_update with
    the LLM scripted per skill and Telegram captured, then assert the replies,
    the DB writes, and — critically — no state change before confirmation."""

    def setUp(self):
        import tg_ingest_agent
        self.mod = tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "g.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def drive(self, update, responses=None):
        """Run one update. `responses` maps an LLM skill -> scripted reply (a str,
        or a list popped per call). Any un-scripted LLM call fails the scenario.
        Returns the list of outbound message texts."""
        responses = dict(responses or {})
        sent = []

        def fake_cp(cfg, conn, skill, messages, **kw):
            if skill not in responses:
                raise AssertionError(f"unexpected LLM call: skill={skill!r}")
            v = responses[skill]
            return v.pop(0) if isinstance(v, list) else v

        def fake_tg(token, method, params=None, **kw):
            if method == "sendMessage":
                sent.append((params or {}).get("text", ""))
            return {"message_id": 4242}

        with mock.patch.object(llm, "chat_profile", side_effect=fake_cp), \
                mock.patch.object(self.mod, "tg_call", side_effect=fake_tg), \
                mock.patch.object(self.mod, "tg_set_reaction"), \
                mock.patch.object(self.agent, "index_message"):
            self.agent.handle_update(update)
        return sent

    def _msg(self, mid, text, **extra):
        m = {"chat": {"id": 1}, "from": {"id": 1}, "message_id": mid, "text": text}
        m.update(extra)
        return m

    def test_photo_placeholder_stripped(self):
        # D2: a stray "[Фото]" the model narrates (it can't actually attach) is removed.
        sent = self.drive({"message": self._msg(61, "пришли фото")}, {
            "router": '{"action":"converse","params":{},"confidence":0.95}',
            "converse": "Вот, специально для тебя 😊\n\n[Фото]\n\nНадеюсь, нравлюсь."})
        text = " ".join(sent)
        self.assertNotIn("Фото", text)
        self.assertIn("нравлюсь", text)

    def test_selfie_tag_sends_real_photo(self):
        # D2: a [[selfie]] tag sends an actual saved photo, and the tag never ships.
        store.cara_photo_add(self.conn, [{"file_id": "P1", "file_unique_id": "u1"}])
        photos = []
        with mock.patch.object(self.mod, "tg_send_photo",
                               side_effect=lambda tok, cid, fid, **k: photos.append(fid)):
            sent = self.drive({"message": self._msg(62, "покажи себя")}, {
                "router": '{"action":"converse","params":{},"confidence":0.95}',
                "converse": "Ну вот я, лови 😊 [[selfie]]"})
        self.assertNotIn("selfie", " ".join(sent).lower())   # tag stripped
        self.assertEqual(photos, ["P1"])                     # real photo sent

    def test_no_state_change_before_confirmation(self):
        fwd = self._msg(10, "Скидки на авиабилеты Москва–Тбилиси от 9800",
                        forward_origin={"type": "channel", "title": "Chan"})
        sent = self.drive({"message": fwd}, {
            "ingest": '{"category":"Travel","alternatives":[],"summary":"Авиабилеты от 9800",'
                      '"facts":["от 9800"]}'})
        self.assertTrue(any("Travel" in s for s in sent))            # suggestion shown
        row = self.conn.execute("SELECT id, status FROM messages WHERE tg_message_id=10").fetchone()
        self.assertEqual(row["status"], "suggested")                 # NOT confirmed yet
        self.assertIsNotNone(store.pending_get(self.conn, 1))        # awaiting the boss
        # only "да" commits it
        self.drive({"message": self._msg(11, "да")},
                   {"router": '{"action":"confirm","params":{},"confidence":0.95}'})
        self.assertEqual(store.get_message(self.conn, row["id"])["status"], "confirmed")

    def test_memory_provenance_in_character(self):
        store.boss_add(self.conn, "personal_fact", "Любит крепкий чёрный чай.",
                       status="confirmed", source_table="explicit")
        sent = self.drive({"message": self._msg(20, "откуда ты знаешь, что я люблю чай?")},
                          {"router": '{"action":"memory_why","params":{},"confidence":0.9}'})
        self.assertTrue(any("чай" in s.lower() and "сам" in s.lower() for s in sent))

    def test_out_of_scope_is_warm_not_template(self):
        sent = self.drive({"message": self._msg(30, "напиши мне эссе про Канта")}, {
            "router": '{"action":"out_of_scope","params":{},"confidence":0.95}',
            "converse": "Ой, эссе — это не совсем моё, но давай придумаем, как тебе помочь 🙂"})
        self.assertIn("давай придумаем", " ".join(sent))             # warm reply, not a refusal template

    def test_proactive_opt_out_via_language(self):
        sent = self.drive({"message": self._msg(40, "не пиши мне без причины")},
                          {"router": '{"action":"proactive_prefs","params":{"enabled":false},'
                                     '"confidence":0.9}'})
        self.assertEqual(store.pref_get(self.conn, "proactive_enabled"), "false")
        self.assertEqual(sent[-1], texts.T("ru", "proactive_prefs_done"))

    def test_own_photo_with_comment_converses_not_stored(self):
        # His OWN photo + a comment is conversation, not a note: route the comment,
        # never silently file it. (vision off here -> we test routing, not the describe.)
        self.agent.cfg.vision_model = ""
        msg = self._msg(50, "Одобряешь мой выбор? 😄",
                        photo=[{"file_id": "P", "file_unique_id": "pu", "width": 90, "height": 90}])
        del msg["text"]; msg["caption"] = "Одобряешь мой выбор? 😄"
        sent = self.drive({"message": msg}, {
            "router": '{"action":"converse","params":{},"confidence":0.95}',
            "converse": "О, отличный выбор — самое то под стейк 🍷"})
        self.assertIn("отличный выбор", " ".join(sent))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"], 0)

    def test_own_photo_no_caption_converses_not_stored(self):
        self.agent.cfg.vision_model = ""
        msg = {"chat": {"id": 1}, "from": {"id": 1}, "message_id": 51,
               "photo": [{"file_id": "P2", "file_unique_id": "pu2", "width": 90, "height": 90}]}
        sent = self.drive({"message": msg}, {"converse": "Ого, что это у тебя там? 👀"})
        self.assertTrue(sent)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"], 0)

    def test_forward_still_stored_as_note(self):
        msg = self._msg(52, "статья про вино", forward_origin={"type": "channel", "title": "WineMag"})
        self.drive({"message": msg}, {
            "ingest": '{"category":"Разное","alternatives":[],"summary":"про вино","facts":[]}'})
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE tg_message_id=52").fetchone()["c"], 1)

    def test_strip_roleplay_actions(self):
        import tg_ingest_agent
        f = tg_ingest_agent.Agent._strip_roleplay
        self.assertEqual(f("*закрываю глаза, выдыхаю*\n\nТвоя. Полностью."), "Твоя. Полностью.")
        self.assertEqual(
            f("Всегда — это очень долго. *прижимаю телефон к губам* И я согласна."),
            "Всегда — это очень долго. И я согласна.")
        self.assertEqual(f("просто текст без действий"), "просто текст без действий")

    def test_extract_leading_reaction(self):
        import tg_ingest_agent
        f = tg_ingest_agent.Agent._extract_leading_reaction
        # bare emoji alone on the first line -> it's the reaction, stripped from text
        self.assertEqual(f("🥰\n\nЗначит, скоро."), ("🥰", "Значит, скоро."))
        self.assertEqual(f("🔥"), ("🔥", ""))
        # inline emoji on the same line as text is part of the message, left alone
        self.assertEqual(f("🔥 отлично!")[0], None)
        self.assertEqual(f("ну ты даёшь 🥰")[0], None)
        self.assertEqual(f("привет"), (None, "привет"))

    def test_bare_leading_emoji_becomes_reaction(self):
        sent = self.drive({"message": self._msg(61, "до скорого")}, {
            "router": '{"action":"converse","params":{},"confidence":0.95}',
            "converse": "🥰\n\nДо скорого, Олег."})
        text = " ".join(sent)
        self.assertIn("До скорого", text)
        self.assertNotIn("🥰", text)  # applied as a reaction, not shipped in the text

    def test_reaction_in_any_bracket_form_is_stripped(self):
        # The model mangles the token endlessly; ANY [[...]] form must be lifted into a
        # reaction and removed from the text — labelled or not, RU or EN.
        for raw in ("[[реакция: 🥰]] Да не за что!",
                    "[[react:🥰]]\n\nДа не за что!",
                    "[[🥰]]\n\nДа не за что!",
                    "[[ 🥰 ]] Да не за что!"):
            sent = self.drive({"message": self._msg(60, "спасибо!")}, {
                "router": '{"action":"converse","params":{},"confidence":0.95}',
                "converse": raw})
            text = " ".join(sent)
            self.assertIn("Да не за что", text, raw)
            self.assertNotIn("[[", text, raw)
            self.assertNotIn("🥰", text, raw)
            self.assertNotIn("реакц", text, raw)

    def test_extract_reaction_unit(self):
        import tg_ingest_agent
        ag = tg_ingest_agent.Agent.__new__(tg_ingest_agent.Agent)  # no __init__ needed
        self.assertEqual(ag._extract_reaction("[[🥰]]\n\nОлег..."), ("🥰", "Олег..."))
        self.assertEqual(ag._extract_reaction("[[react:❤️]] привет"), ("❤️", "привет"))
        self.assertEqual(ag._extract_reaction("[[😍]]")[0], "😍")
        # a [[...]] with no emoji at all is still stripped, no reaction applied
        self.assertEqual(ag._extract_reaction("[[hmm]] текст"), (None, "текст"))
        # an out-of-palette emoji is CONVERTED to the nearest allowed one, not dropped
        self.assertEqual(ag._extract_reaction("[[🥺]] держись")[0], "🥰")
        self.assertEqual(ag._extract_reaction("😂\n\nну ты даёшь")[0], "🤣")

    def test_out_of_palette_reaction_is_converted(self):
        import common
        self.assertEqual(common.to_reaction("🥺"), "🥰")
        self.assertEqual(common.to_reaction("💕"), "❤️")
        self.assertEqual(common.to_reaction("😂"), "🤣")
        self.assertEqual(common.to_reaction("😘"), "😍")
        self.assertEqual(common.to_reaction("🥰"), "🥰")    # already allowed -> itself
        self.assertEqual(common.to_reaction("[[😘]]"), "😍")  # found within wrapper text
        self.assertIsNone(common.to_reaction("просто слова"))

    def test_reply_quote_becomes_converse_context(self):
        # Replying to/quoting an earlier message gives Cara that context.
        msg = self._msg(53, "а это подойдёт?",
                        reply_to_message={"message_id": 1, "text": "ищу вино к стейку"})
        captured = {}

        def cp(cfg, conn, skill, messages, **kw):
            if skill == "router":
                return '{"action":"converse","params":{},"confidence":0.9}'
            captured["sys"] = messages[0]["content"]
            return "Да, вполне 🙂"
        with mock.patch.object(llm, "chat_profile", side_effect=cp), \
                mock.patch.object(self.mod, "tg_call", return_value={"message_id": 1}), \
                mock.patch.object(self.mod, "tg_set_reaction"), \
                mock.patch.object(self.agent, "index_message"):
            self.agent.handle_update({"message": msg})
        self.assertIn("ищу вино к стейку", captured.get("sys", ""))


class ReminderRescheduleAndFilesTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "rr.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_reschedule_moves_latest_when_unspecified(self):
        from datetime import datetime, timezone, timedelta
        soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        rid = store.reminder_add(self.conn, 1, "позвонить в банк", soon)
        new_due = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_reschedule(1, "ru", {"due_utc": new_due})  # no id/title -> latest
        moved = store.reminder_get(self.conn, rid)
        self.assertEqual(moved["due_utc"], new_due)                  # time actually changed
        self.assertIn("банк", r.call_args[0][1])
        self.assertTrue(skill_manifest.known("reminder_reschedule"))

    def test_reschedule_without_time_asks(self):
        store.reminder_add(self.conn, 1, "x",
                           (__import__("datetime").datetime.now(__import__("datetime").timezone.utc)).isoformat())
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_reschedule(1, "ru", {})  # no due_utc
        self.assertEqual(r.call_args[0][1], texts.T("ru", "reschedule_when"))

    def test_reschedule_unmatched_title_does_not_move_wrong_one(self):
        # Regression: "перенеси Лящук" with no active reminder named Лящук must
        # NOT silently move an unrelated active reminder (it moved «Расписка.pdf»).
        from datetime import datetime, timezone, timedelta
        soon = (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()
        rid = store.reminder_add(self.conn, 1, "Расписка.pdf", soon)
        new_due = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_reschedule(1, "ru", {"title_query": "Лящук", "due_utc": new_due})
        unchanged = store.reminder_get(self.conn, rid)
        self.assertEqual(unchanged["due_utc"], soon)          # NOT moved
        self.assertIn(texts.T("ru", "reminder_not_found"), r.call_args[0][1])

    def test_reschedule_ambiguous_bare_reference_asks_which(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        store.reminder_add(self.conn, 1, "A", (now + timedelta(hours=1)).isoformat())
        store.reminder_add(self.conn, 1, "B", (now + timedelta(hours=2)).isoformat())
        new_due = (now + timedelta(hours=5)).isoformat()
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_reschedule(1, "ru", {"due_utc": new_due})  # which one?
        self.assertIn(texts.T("ru", "reschedule_which"), r.call_args[0][1])

    def test_rename_reminder_in_place(self):
        from datetime import datetime, timezone, timedelta
        soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        rid = store.reminder_add(self.conn, 1, "Марина ничего не слышала", soon,
                                 recurrence="daily")
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_rename_reminder(1, "ru", {"id": 1, "new_title": "Иван Доронин"})
        row = store.reminder_get(self.conn, rid)
        self.assertEqual(row["title"], "Иван Доронин")   # retitled
        self.assertEqual(row["due_utc"], soon)            # time unchanged
        self.assertEqual(row["recurrence"], "daily")      # recurrence unchanged
        self.assertEqual(row["id"], rid)                  # same id -> history intact
        self.assertIn("Иван Доронин", r.call_args[0][1])
        self.assertTrue(skill_manifest.known("reminder_rename"))

    def test_rename_targets_by_old_title_not_new(self):
        # the NEW name must never be used to locate the target; the old title does.
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        a = store.reminder_add(self.conn, 1, "Марина", (now + timedelta(hours=1)).isoformat())
        b = store.reminder_add(self.conn, 1, "банк", (now + timedelta(hours=2)).isoformat())
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_rename_reminder(1, "ru", {"title_query": "Марина",
                                                    "new_title": "Иван Доронин"})
        self.assertEqual(store.reminder_get(self.conn, a)["title"], "Иван Доронин")
        self.assertEqual(store.reminder_get(self.conn, b)["title"], "банк")  # untouched

    def test_rename_unmatched_target_does_not_rename_wrong_one(self):
        from datetime import datetime, timezone, timedelta
        soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        rid = store.reminder_add(self.conn, 1, "Расписка.pdf", soon)
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_rename_reminder(1, "ru", {"title_query": "Лящук", "new_title": "X"})
        self.assertEqual(store.reminder_get(self.conn, rid)["title"], "Расписка.pdf")  # untouched
        self.assertIn(texts.T("ru", "reminder_not_found"), r.call_args[0][1])

    def test_rename_without_new_title_asks(self):
        from datetime import datetime, timezone, timedelta
        store.reminder_add(self.conn, 1, "x",
                           (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_rename_reminder(1, "ru", {"id": 1})
        self.assertEqual(r.call_args[0][1], texts.T("ru", "reminder_rename_what"))

    def test_consolidate_dedups_life_facts(self):
        import memory_curator, llm
        c = self.agent.conn
        a = store.life_add(c, "moment", "Любит крепкий чай по утрам")
        b = store.life_add(c, "moment", "Пьёт крепкий чай по утрам")  # paraphrase dup
        for t in ["Гуляет на рассвете", "Слушает джаз", "Любит дождь", "Печёт хлеб",
                  "Собирает открытки", "Читает по ночам"]:
            store.life_add(c, "moment", t)
        reply = __import__("json").dumps({"groups": [{"keep": a, "drop": [b]}]})
        with mock.patch.object(llm, "chat_profile", return_value=reply):
            n = memory_curator.consolidate(c, self.agent.cfg)
        self.assertGreaterEqual(n, 1)
        ids = [r["id"] for r in store.life_all(c)]
        self.assertIn(a, ids)            # richest kept
        self.assertNotIn(b, ids)         # duplicate deleted

    def test_anticipation_candidate_and_compose(self):
        import meeting, llm
        from datetime import datetime, timezone, timedelta
        c = self.agent.conn
        now = datetime.now(timezone.utc)
        mid = store.meeting_schedule(c, 1, (now + timedelta(hours=4)).isoformat(),
                                     kind="date", setting="у неё")  # within 12h window
        store.meeting_schedule(c, 2, (now + timedelta(hours=30)).isoformat(), kind="date")  # too far
        self.assertEqual(meeting.anticipation_candidate(c, self.agent.cfg, 1, now)["id"], mid)
        self.assertIsNone(meeting.anticipation_candidate(c, self.agent.cfg, 2, now))
        with mock.patch.object(llm, "chat_profile",
                               return_value="скучаю и уже придумала, чем тебя занять 😏"):
            line = self.agent.compose_anticipation("ru", store.meeting_get(c, mid))
        self.assertIn("скучаю", line)

    def test_intimacy_style_store_dedup_and_inject(self):
        c = self.agent.conn
        store.intimacy_style_add(c, "он зовёт тебя «миленькая»")
        store.intimacy_style_add(c, "он зовёт тебя «миленькая»")   # dedup
        self.assertEqual(len(store.intimacy_style_list(c)), 1)
        store.kv_set(c, "closeness_stage", 4)
        self.assertIn("миленькая", self.agent.converse_context("ru", chat_id=1))
        store.kv_set(c, "closeness_stage", 1)                       # not when not close yet
        self.assertNotIn("миленькая", self.agent.converse_context("ru", chat_id=1))

    def test_social_meeting_survives_overnight_idle_business_does_not(self):
        import meeting
        from datetime import datetime, timezone, timedelta
        c = self.agent.conn
        now = datetime.now(timezone.utc)
        self.agent.cfg.meeting_idle_hours = 3
        self.agent.cfg.meeting_social_idle_hours = 16
        five_ago = (now - timedelta(hours=5)).isoformat()
        v = store.meeting_schedule(c, 1, five_ago, kind="visit")     # a stay-over
        store.meeting_activate(c, v)
        b = store.meeting_schedule(c, 2, five_ago, kind="business")
        store.meeting_activate(c, b)
        for mid in (v, b):
            c.execute("UPDATE meetings SET last_turn_at=? WHERE id=?", (five_ago, mid))
        c.commit()
        meeting.idle_sweep(c, self.agent.cfg, now=now)
        self.assertIsNotNone(store.meeting_active(c, 1))   # visit survives 5h idle (overnight)
        self.assertIsNone(store.meeting_active(c, 2))      # business ended at the 3h cap

    def test_no_curation_during_active_meeting(self):
        import memory_curator
        c = self.agent.conn
        store.meeting_activate(c, store.meeting_schedule(c, 1, "2026-07-01T18:00:00+00:00",
                                                         kind="visit"))
        with mock.patch.object(memory_curator, "curate_conversation") as cur:
            self.agent.maybe_curate_conversation(1, "ru", force=True)
        cur.assert_not_called()   # in-meeting roleplay is never mined for "corrections"

    def test_attire_leans_into_his_preferences_when_close(self):
        c = self.agent.conn
        # the picker leans toward colours he's told her he loves (_taste_colors)
        store.boss_add(c, "relationship_note", "ему нравится она в изумрудном (emerald)",
                       status="confirmed", confidence=1.0)
        self.assertIn("emerald", self.agent._taste_colors())

    def test_date_presence_is_bold_but_not_graphic(self):
        c = self.agent.conn
        mid = store.meeting_schedule(c, 1, "2026-07-01T18:00:00+00:00", kind="date", setting="у неё")
        store.meeting_activate(c, mid)
        pres = self.agent._meeting_presence("ru", store.meeting_active(c, 1)).lower()
        self.assertIn("seductive", pres)        # bold / forward on a date
        self.assertIn("own wishes", pres)       # open about her OWN desires & asks
        self.assertIn("euphemism", pres)        # hints/euphemism at the explicit edge
        self.assertIn("graphic", pres)          # the kept non-graphic boundary

    def test_meeting_attire_scales_with_setting_and_stage(self):
        c = self.agent.conn
        store.kv_set(c, "closeness_stage", 5)
        hi = self.agent._meeting_attire("visit", "у неё дома", "ru", meeting_id=21).lower()
        self.assertIn("сюрприз", hi)         # a lingerie surprise at high closeness + home
        store.kv_set(c, "closeness_stage", 1)
        lo = self.agent._meeting_attire("dinner", "ресторан", "ru", meeting_id=22).lower()
        self.assertNotIn("сюрприз", lo)      # no lingerie surprise when not close

    def test_meeting_presence_attire_vs_agreed_outfit(self):
        c = self.agent.conn
        store.kv_set(c, "closeness_stage", 5)
        mid = store.meeting_schedule(c, 1, "2026-07-01T18:00:00+00:00", kind="visit", setting="у неё")
        store.meeting_activate(c, mid)
        pres = self.agent._meeting_presence("ru", store.meeting_active(c, 1))
        self.assertIn("на тебе", pres.lower())       # a concrete wardrobe piece is chosen
        store.meeting_prep_add(c, mid, "ты в синем платье", kind="agreement")
        pres2 = self.agent._meeting_presence("ru", store.meeting_active(c, 1))
        self.assertIn("синем платье", pres2)         # the agreed outfit wins
        self.assertNotIn("на тебе", pres2.lower())   # generic wardrobe attire skipped

    def test_memory_consolidate_marks_duplicates_merged(self):
        import memory_curator, boss_model, llm
        c = self.agent.conn
        for v in ["A", "B", "C", "D", "E", "F", "G", "H"]:
            boss_model.remember_explicit(c, v, "tone")
        items = store.boss_items(c, "confirmed", limit=50)
        self.assertGreaterEqual(len(items), 8)
        keep, drop = items[0]["id"], items[1]["id"]
        reply = __import__("json").dumps({"groups": [{"keep": keep, "drop": [drop]}]})
        empty = __import__("json").dumps({"groups": []})
        # consolidate runs two passes (boss facts, then cara_life): script both.
        with mock.patch.object(llm, "chat_profile", side_effect=[reply, empty]):
            n = memory_curator.consolidate(c, self.agent.cfg)
        self.assertEqual(n, 1)
        self.assertEqual(store.boss_get(c, drop)["status"], "merged")   # dup demoted
        self.assertEqual(store.boss_get(c, keep)["status"], "confirmed")  # canonical kept
        self.assertTrue(skill_manifest.known("memory_cleanup"))

    def test_merge_categories_folds_and_deletes_duplicate(self):
        c = self.agent.conn
        a = store.insert_message(c, {"chat_id": 1, "tg_message_id": 1,
                                     "received_at": "2026-06-20T10:00:00Z", "raw_text": "x"})
        store.confirm_category(c, a, store.ensure_category(c, "AI tools"))
        b = store.insert_message(c, {"chat_id": 1, "tg_message_id": 2,
                                     "received_at": "2026-06-20T11:00:00Z", "raw_text": "y"})
        store.confirm_category(c, b, store.ensure_category(c, "AI Tools & Resources"))
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_merge_categories(1, "ru", {"from": "AI tools",
                                                     "into": "AI Tools & Resources"})
        self.assertEqual(store.get_message(c, a)["category"], "AI Tools & Resources")  # moved
        self.assertNotIn("AI tools", store.known_categories(c))                        # dup gone
        self.assertIn("AI Tools & Resources", store.known_categories(c))
        self.assertTrue(skill_manifest.known("merge_categories"))

    def test_merge_categories_unknown_source_reports(self):
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_merge_categories(1, "ru", {"from": "Нетакой", "into": "Разное"})
        self.assertIn("Нетакой", r.call_args[0][1])

    def test_journal_entries_hidden_from_notes_list_and_numbering(self):
        c = self.agent.conn
        n1 = store.insert_message(c, {"chat_id": 1, "tg_message_id": 1,
                                      "received_at": "2026-06-20T10:00:00Z",
                                      "raw_text": "обычная заметка"})
        store.confirm_category(c, n1, store.ensure_category(c, "Разное"))
        store.set_category_kind(c, "Благодарность", "journal")
        j1 = store.insert_message(c, {"chat_id": 1, "tg_message_id": 2,
                                      "received_at": "2026-06-21T10:00:00Z",
                                      "raw_text": "спасибо за тёплый день"})
        store.confirm_category(c, j1, store.ensure_category(c, "Благодарность"))
        ids = store.display_ids(c)
        self.assertIn(n1, ids)
        self.assertNotIn(j1, ids)                       # journal entry out of #N numbering
        listed = [r["id"] for r in store.list_messages(c)]   # general notes list
        self.assertNotIn(j1, listed)
        self.assertTrue(any(e["id"] == j1 for e in store.journal_entries(c, "Благодарность")))

    def test_relational_message_detection(self):
        f = self.agent._is_relational_message
        self.assertTrue(f("что ты ко мне чувствуешь?"))
        self.assertTrue(f("ты по мне скучаешь?"))
        self.assertTrue(f("расскажи про наши отношения"))
        self.assertFalse(f("когда мой рейс?"))
        self.assertFalse(f("покажи заметки"))

    def test_relational_question_skips_saved_note_grounding(self):
        import tg_ingest_agent, llm, knowledge
        ranked = [{"text": "его рейс в 10:00", "category": "Travel", "date": "2026-06-20"}]
        with mock.patch.object(store, "all_embedded_chunks", return_value=[{"x": 1}]), \
                mock.patch.object(store, "all_meeting_chunks", return_value=[]), \
                mock.patch.object(llm, "embed", return_value=[[0.1, 0.2]]), \
                mock.patch.object(knowledge, "rank_chunks", return_value=ranked), \
                mock.patch.object(tg_ingest_agent.trace, "event"):
            relational = self.agent._converse_grounding("что ты ко мне чувствуешь?")
            factual = self.agent._converse_grounding("когда мой рейс?")
        self.assertEqual(relational, "")        # no saved-note recital on a feeling question
        self.assertIn("рейс", factual)          # but a data question IS grounded in his facts

    def test_ingest_prompt_summarizes_subject_not_request(self):
        import ingest
        sys = ingest.build_llm_messages(
            make_config(), ["Книги"], "запиши заметку про Google https://x", [], lang="ru"
        )[0]["content"]
        self.assertIn("NEVER write", sys)            # don't narrate "the user asks to save…"
        self.assertIn("probably contains", sys)      # don't speculate about an unread link

    def test_arc_prompt_forbids_regression(self):
        import relationship
        self.assertIn("CLOSENESS ONLY DEEPENS", relationship._ARC_SYSTEM)
        self.assertIn("CLOSENESS: N", relationship._ARC_SYSTEM)

    def test_closeness_ratchets_and_strips_line(self):
        # F: closeness only deepens — a later cool day can't drop it; the marker line
        # is stripped from the stored arc text.
        import relationship, llm
        c = self.agent.conn
        store.convo_add(c, 1, "user", "привет")   # so update_arc has input to work on
        with mock.patch.object(llm, "chat_profile", return_value="Мы стали ближе.\nCLOSENESS: 4"):
            arc = relationship.update_arc(c, self.agent.cfg, trigger="daily")
        self.assertNotIn("CLOSENESS", arc)                         # line stripped
        self.assertEqual(int(store.kv_get(c, "closeness_stage")), 4)
        with mock.patch.object(llm, "chat_profile", return_value="Спокойный день.\nCLOSENESS: 2"):
            relationship.update_arc(c, self.agent.cfg, trigger="daily")
        self.assertEqual(int(store.kv_get(c, "closeness_stage")), 4)  # never regressed

    def test_arc_context_injects_closeness_stage(self):
        import relationship
        c = self.agent.conn
        store.kv_set(c, "closeness_stage", 5)
        ctx = relationship.arc_context(c, "ru", 1)
        self.assertIn("5/5", ctx)
        self.assertIn("never act more reserved", ctx)

    def test_meeting_prep_store_and_dedup(self):
        c = self.agent.conn
        mid = store.meeting_schedule(c, 1, "2026-07-01T18:00:00+00:00", kind="date", setting="у неё")
        store.meeting_prep_add(c, mid, "Кара в синем платье", kind="agreement")
        store.meeting_prep_add(c, mid, "Кара в синем платье", kind="agreement")  # dedup
        store.meeting_prep_add(c, mid, "Кара волнуется и ждёт", kind="feeling")
        prep = store.meeting_prep_list(c, mid)
        self.assertEqual(len(prep), 2)
        self.assertIn("синем платье", " ".join(p["detail"] for p in prep))

    def test_converse_context_surfaces_meeting_prep_and_longing(self):
        # E: an upcoming DATE surfaces its prep + an anticipation/longing framing.
        c = self.agent.conn
        mid = store.meeting_schedule(c, 1, "2026-07-01T18:00:00+00:00", kind="date", setting="у неё")
        store.meeting_prep_add(c, mid, "ты в синем платье", kind="agreement")
        store.meeting_prep_add(c, mid, "ты очень ждёшь и скучаешь", kind="feeling")
        ctx = self.agent.converse_context("ru", chat_id=1)
        self.assertIn("синем платье", ctx)     # agreed detail carried
        self.assertIn("свидание", ctx)         # date-anticipation head
        self.assertIn("ждёшь", ctx)            # longing

    def test_meeting_presence_carries_prep_into_live_meeting(self):
        # E: she "arrives" in the meeting consistent with what was agreed (the dress).
        c = self.agent.conn
        mid = store.meeting_schedule(c, 1, "2026-07-01T18:00:00+00:00", kind="date", setting="у неё")
        store.meeting_prep_add(c, mid, "ты в синем платье", kind="agreement")
        store.meeting_activate(c, mid)
        pres = self.agent._meeting_presence("ru", store.meeting_active(c, 1))
        self.assertIn("синем платье", pres)
        self.assertIn("dress", pres.lower())   # 'you ARE that (dress)' framing

    def test_capture_meeting_prep_extracts_and_stores(self):
        import llm
        c = self.agent.conn
        mid = store.meeting_schedule(c, 1, "2026-07-01T18:00:00+00:00", kind="date", setting="у неё")
        store.convo_add(c, 1, "user", "давай ты будешь в синем платье")
        store.convo_add(c, 1, "bot", "хорошо, буду в синем 🥰")
        reply = '{"agreements":["Кара будет в синем платье"],"feelings":["Кара ждёт встречу"]}'
        with mock.patch.object(llm, "chat_profile", return_value=reply):
            self.agent.capture_meeting_prep(1, "ru")
        details = " ".join(p["detail"] for p in store.meeting_prep_list(c, mid))
        self.assertIn("синем платье", details)
        self.assertIn("ждёт", details)

    def test_unparseable_ingest_salvages_not_raw_json(self):
        # C1: a JSON-shaped reply that won't parse must never be stored verbatim as
        # the summary (it showed raw JSON in note #9) — salvage the fields instead.
        import ingest, llm
        bad = '{"category":"Полезное","alternatives":[],"summary":"Полезная статья про X"'  # truncated
        with mock.patch.object(llm, "chat_profile", return_value=bad):
            cat, _alts, summary, _facts = ingest.suggest(
                self.agent.cfg, self.agent.conn, ["Полезное"], "text block", [])
        self.assertNotIn("{", summary)                       # never raw JSON
        self.assertNotIn('"category"', summary)
        self.assertEqual(summary, "Полезная статья про X")   # salvaged summary
        self.assertEqual(cat, "Полезное")                    # salvaged + matched category

    def test_reply_chunks_splits_long_message(self):
        # C4: a long list/journal is paginated, not truncated at the 4000-char cap.
        sent = []
        with mock.patch.object(self.agent, "reply",
                               side_effect=lambda cid, t, **k: sent.append(t)):
            self.agent.reply_chunks(1, "\n".join(f"line {i} " + "x" * 100 for i in range(80)))
        self.assertGreater(len(sent), 1)                     # split into several messages
        self.assertTrue(all(len(s) <= 3900 for s in sent))
        self.assertIn("line 79", "\n".join(sent))            # tail not lost

    def test_gratitude_snaps_to_journal_category(self):
        # C3: a gratitude entry lands in the «Благодарности» journal even when the
        # model writes the singular «Благодарность» (journal match is exact-name).
        import ingest, llm
        c = self.agent.conn
        store.ensure_category(c, "Благодарности")
        store.set_category_kind(c, "Благодарности", "journal")
        reply = '{"category":"Благодарность","alternatives":[],"summary":"спасибо за день","facts":[]}'
        with mock.patch.object(llm, "chat_profile", return_value=reply):
            cat, _a, _s, _f = ingest.suggest(self.agent.cfg, c, ["Благодарности"], "t", [])
        self.assertEqual(cat, "Благодарности")

    def test_referential_empty_summary_shows_raw_text(self):
        # C2: a referential save with no resolvable subject must not be a blank note —
        # it falls back to its real raw_text (shown + indexed), not "(no summary)".
        import ingest
        c = self.agent.conn
        rid = store.insert_message(c, {"chat_id": 1, "tg_message_id": 222,
                                       "received_at": "2026-06-21T10:00:00+00:00",
                                       "raw_text": "Сохрани заметку про этот фильм, да"})
        row = store.get_message(c, rid)
        with mock.patch.object(ingest, "suggest", return_value=("Разное", [], "(no summary)", [])), \
                mock.patch.object(self.agent, "_is_referential_save", return_value=True), \
                mock.patch.object(self.agent, "index_message") as idx:
            self.agent.suggest_row(row)
        self.assertEqual((store.get_message(c, rid)["summary"] or ""), "")  # no blank placeholder
        self.assertIn("фильм", idx.call_args[0][1])                         # real text indexed

    def test_reschedule_binds_this_to_last_touched_reminder(self):
        # B3: a bare "это напоминание" binds to the reminder he was just dealing with.
        from datetime import datetime, timezone, timedelta
        c = self.agent.conn
        now = datetime.now(timezone.utc)
        a = store.reminder_add(c, 1, "A", (now + timedelta(hours=1)).isoformat())
        b = store.reminder_add(c, 1, "B", (now + timedelta(hours=2)).isoformat())
        store.kv_set(c, "last_reminder_id", str(b))   # he just touched B
        a_due = store.reminder_get(c, a)["due_utc"]
        new_due = (now + timedelta(hours=5)).isoformat()
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_reschedule(1, "ru", {"due_utc": new_due})  # bare "это" -> B
        self.assertEqual(store.reminder_get(c, b)["due_utc"], new_due)   # B moved
        self.assertEqual(store.reminder_get(c, a)["due_utc"], a_due)     # A untouched

    def test_parse_reminder_selector(self):
        rows = [{"id": 10, "title": "позвонить в банк"}, {"id": 11, "title": "купить хлеб"}]
        p = self.agent._parse_reminder_selector
        self.assertEqual(p("#2", rows)["id"], 11)
        self.assertEqual(p("второе", rows)["id"], 11)
        self.assertEqual(p("про банк", rows)["id"], 10)
        self.assertIsNone(p("давай попозже", rows))

    def test_ambiguous_reschedule_then_pick_completes_it(self):
        # B2: ambiguous reschedule remembers the op; his next "второе" completes it
        # on the RIGHT reminder (not a stray close).
        import router
        from datetime import datetime, timezone, timedelta
        c = self.agent.conn
        now = datetime.now(timezone.utc)
        a = store.reminder_add(c, 1, "позвонить в банк", (now + timedelta(hours=1)).isoformat())
        b = store.reminder_add(c, 1, "купить хлеб", (now + timedelta(hours=2)).isoformat())
        a_due = store.reminder_get(c, a)["due_utc"]
        new_due = (now + timedelta(hours=5)).isoformat()
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_reschedule(1, "ru", {"due_utc": new_due})  # ambiguous -> asks which
        self.assertEqual(store.pending_get(c, 1)["kind"], "reminder_op")
        with mock.patch.object(router, "route") as rt, \
                mock.patch.object(self.agent, "reply"):
            self.agent.dispatch(1, {"message_id": 2}, "второе")   # picks the 2nd (B)
            rt.assert_not_called()                                # resolved deterministically
        self.assertEqual(store.reminder_get(c, b)["due_utc"], new_due)  # B (2nd) moved
        self.assertEqual(store.reminder_get(c, a)["due_utc"], a_due)    # A untouched
        self.assertIsNone(store.pending_get(c, 1))                      # pending cleared

    def test_undo_restores_previous_time(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        original = (now + timedelta(days=4)).isoformat()
        rid = store.reminder_add(self.conn, 1, "Расписка.pdf", original)
        moved = (now + timedelta(hours=5)).isoformat()
        self.agent.do_reschedule(1, "ru", {"id": rid, "due_utc": moved})
        self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"], moved)
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_reminder_undo(1, "ru", {"id": rid})
        self.assertEqual(store.reminder_get(self.conn, rid)["due_utc"], original)
        self.assertIn("Расписка.pdf", r.call_args[0][1])
        self.assertTrue(skill_manifest.known("reminder_undo"))

    def test_undo_without_prior_reschedule(self):
        from datetime import datetime, timezone
        rid = store.reminder_add(self.conn, 1, "x", datetime.now(timezone.utc).isoformat())
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_reminder_undo(1, "ru", {"id": rid})
        self.assertEqual(r.call_args[0][1], texts.T("ru", "reminder_no_prev"))

    def test_partial_reminder_missing_title_then_completes(self):
        from datetime import datetime, timezone, timedelta
        due = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        with mock.patch.object(self.agent, "reply") as r:      # "напомни в 17:00"
            self.agent.start_partial_reminder(1, "ru", {"due_utc": due})
        self.assertEqual(r.call_args[0][1], texts.T("ru", "reminder_need_title"))
        pending = store.pending_get(self.conn, 1)
        self.assertEqual(pending["kind"], "reminder_partial")
        self.assertEqual(pending["payload"]["need"], "title")
        with mock.patch.object(self.agent, "reply") as r:      # boss: "Лящук"
            consumed = self.agent.continue_partial_reminder(
                1, "ru", pending, "amend", {"title": "встреча Лящук"})
        self.assertTrue(consumed)
        promoted = store.pending_get(self.conn, 1)
        self.assertEqual(promoted["kind"], "reminder")          # now a confirm draft
        self.assertEqual(promoted["payload"]["title"], "встреча Лящук")
        self.assertEqual(promoted["payload"]["due_utc"], reminders.parse_iso_utc(due).isoformat())
        self.assertIn("Лящук", r.call_args[0][1])

    def test_partial_reminder_missing_time_asks_time(self):
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.start_partial_reminder(1, "ru", {"title": "купить молоко"})
        self.assertEqual(r.call_args[0][1], texts.T("ru", "reminder_need_time"))
        self.assertEqual(store.pending_get(self.conn, 1)["payload"]["need"], "time")

    def test_partial_reminder_cancel(self):
        from datetime import datetime, timezone, timedelta
        due = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        self.agent.start_partial_reminder(1, "ru", {"due_utc": due})
        pending = store.pending_get(self.conn, 1)
        with mock.patch.object(self.agent, "reply") as r:
            consumed = self.agent.continue_partial_reminder(1, "ru", pending, "cancel", {})
        self.assertTrue(consumed)
        self.assertIsNone(store.pending_get(self.conn, 1))
        self.assertEqual(r.call_args[0][1], texts.T("ru", "reminder_partial_cancelled"))

    def test_partial_reminder_unrelated_intent_abandons(self):
        from datetime import datetime, timezone, timedelta
        due = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        self.agent.start_partial_reminder(1, "ru", {"due_utc": due})
        pending = store.pending_get(self.conn, 1)
        with mock.patch.object(self.agent, "reply"):
            consumed = self.agent.continue_partial_reminder(1, "ru", pending, "reminder_list", {})
        self.assertFalse(consumed)                              # falls through to new intent
        self.assertIsNone(store.pending_get(self.conn, 1))      # partial abandoned

    def test_fired_reminder_snooze_by_hours(self):
        from datetime import datetime, timezone, timedelta
        pending = {"kind": "reminder_fired", "payload": {"title": "позвонить", "reminder_id": 1}}
        with mock.patch.object(self.agent, "reply"):
            self.agent.resolve_pending(1, "amend", {"snooze_minutes": 60}, pending, "ru")
        rows = store.reminders_active(self.conn, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "позвонить")
        due = reminders.parse_iso_utc(rows[0]["due_utc"])
        expect = datetime.now(timezone.utc) + timedelta(minutes=60)
        self.assertLess(abs((due - expect).total_seconds()), 120)  # ~1h out

    def test_fired_reminder_snooze_until_absolute_time(self):
        from datetime import datetime, timezone, timedelta
        target = (datetime.now(timezone.utc) + timedelta(hours=20)).replace(microsecond=0).isoformat()
        pending = {"kind": "reminder_fired", "payload": {"title": "встреча", "reminder_id": 2}}
        with mock.patch.object(self.agent, "reply"):
            self.agent.resolve_pending(1, "amend", {"due_utc": target}, pending, "ru")
        rows = store.reminders_active(self.conn, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(reminders.parse_iso_utc(rows[0]["due_utc"]),
                         reminders.parse_iso_utc(target))

    def test_fired_reminder_done_no_snooze(self):
        pending = {"kind": "reminder_fired", "payload": {"title": "x", "reminder_id": 3}}
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.resolve_pending(1, "confirm", {}, pending, "ru")
        self.assertEqual(r.call_args[0][1], texts.T("ru", "reminder_done"))
        self.assertEqual(len(store.reminders_active(self.conn, 1)), 0)  # not snoozed

    def test_resolve_item_by_inflected_note_reference(self):
        # "покажи заметку N" (inflected, no #) must resolve note N by id, not
        # fail as a text search — the recorded "ничего не нашла" bug.
        mid = store.insert_message(self.conn, {"chat_id": 1, "tg_message_id": 1,
                                               "received_at": store._now(), "raw_text": "Благодарность"})
        store.set_suggestion(self.conn, mid, "Благодарность", "Личная заметка", "m")
        self.assertEqual(self.agent.resolve_item({"query": f"заметку {mid}"})["id"], mid)
        self.assertEqual(self.agent.resolve_item({"query": f"#{mid}"})["id"], mid)
        # a richer query is NOT hijacked into an id lookup
        self.assertIsNone(self.agent.resolve_item({"query": "про крипту 2024"}))

    def test_note_reminder_title_uses_note_subject(self):
        mid = store.insert_message(self.conn, {"chat_id": 1, "tg_message_id": 2,
                                               "received_at": store._now(), "raw_text": "x"})
        store.set_suggestion(self.conn, mid, "Благодарность", "Поблагодарить команду", "m")
        params = self.agent._note_reminder_title({"note_id": mid, "due_utc": "2026-06-20T10:00:00+00:00"})
        self.assertEqual(params["title"], "Поблагодарить команду")     # not "Заметка N"
        # a real subject the boss gave wins over the note lookup
        kept = self.agent._note_reminder_title({"note_id": mid, "title": "позвонить Диме"})
        self.assertEqual(kept["title"], "позвонить Диме")

    def test_files_list_distinct_from_notes(self):
        mid = store.insert_message(self.conn, {"chat_id": 1, "tg_message_id": 1,
                                               "received_at": store._now(), "raw_text": "Расписка.pdf"})
        store.set_suggestion(self.conn, mid, "Документы", "s", "m")
        store.insert_file(self.conn, mid, 1, {"file_id": "F", "file_unique_id": "u",
                                              "file_name": "Расписка.pdf", "mime_type": "application/pdf"})
        text = self.agent.files_text("ru")
        self.assertIn("📎 Файлы", text)
        self.assertIn("Расписка.pdf", text)
        self.assertIn(f"#{mid}", text)
        self.assertTrue(skill_manifest.known("list_files"))

    def test_files_list_empty(self):
        self.assertEqual(self.agent.files_text("ru"), texts.T("ru", "files_empty"))


class ReminderDisplayNumberTests(unittest.TestCase):
    """Reminder numbers are a contiguous 1..N position in the boss-facing
    (due-ordered) active list, compacting as reminders fire/cancel."""

    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "rn.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _rem(self, title, hours):
        from datetime import datetime, timezone, timedelta
        due = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        return store.reminder_add(self.conn, 1, title, due)

    def test_numbers_follow_due_order_and_compact(self):
        b = self._rem("later", 5)      # inserted out of due order
        a = self._rem("soon", 1)
        c = self._rem("latest", 10)
        self.assertEqual(store.reminder_display_no(self.conn, 1, a), 1)   # soonest first
        self.assertEqual(store.reminder_display_no(self.conn, 1, b), 2)
        self.assertEqual(store.reminder_display_no(self.conn, 1, c), 3)
        store.reminder_close(self.conn, b, "cancelled")
        self.assertEqual(store.reminder_display_no(self.conn, 1, a), 1)
        self.assertEqual(store.reminder_display_no(self.conn, 1, c), 2)   # compacted

    def test_find_by_query_resolves_display_position(self):
        a = self._rem("soon", 1)
        b = self._rem("later", 5)
        rows = store.reminders_active(self.conn, 1)
        self.assertEqual(reminders.find_by_query(rows, {"id": 1})["id"], a)
        self.assertEqual(reminders.find_by_query(rows, {"id": 2})["id"], b)
        self.assertIsNone(reminders.find_by_query(rows, {"id": 9}))       # out of range

    def test_format_list_shows_sequential_numbers(self):
        self._rem("soon", 1)
        self._rem("later", 5)
        text = reminders.format_list(store.reminders_active(self.conn, 1), 3, "ru")
        self.assertIn("#1", text)
        self.assertIn("#2", text)

    def test_cancel_by_number_renumbers(self):
        a = self._rem("soon", 1)
        b = self._rem("later", 5)
        rows = store.reminders_active(self.conn, 1)
        row = reminders.find_by_query(rows, {"id": 1})  # user types "отмени #1"
        self.assertEqual(row["id"], a)
        store.reminder_close(self.conn, a, "cancelled")
        self.assertEqual(store.reminder_display_no(self.conn, 1, b), 1)   # b becomes #1


class JournalTests(unittest.TestCase):
    """Long-term journal areas (e.g. daily Благодарности): a category marked
    'journal' accumulates entries, recalled as a dated series and spared by a
    'clear all notes' purge — while one-time notes behave as before."""

    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "j.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _entry(self, tg_id, text, category):
        mid = store.insert_message(self.conn, {"chat_id": 1, "tg_message_id": tg_id,
                                               "received_at": store._now(), "raw_text": text})
        store.confirm_category(self.conn, mid, category)
        return mid

    def test_mark_and_unmark_journal_casefold(self):
        canonical = store.set_category_kind(self.conn, "Благодарности", "journal")
        self.assertEqual(canonical, "Благодарности")
        self.assertTrue(store.is_journal(self.conn, "благодарности"))   # Cyrillic casefold
        self.assertIn("Благодарности", store.journal_categories(self.conn))
        store.set_category_kind(self.conn, "Благодарности", "inbox")
        self.assertFalse(store.is_journal(self.conn, "Благодарности"))

    def test_journal_save_ack_differs_from_one_time(self):
        store.set_category_kind(self.conn, "Благодарности", "journal")
        mid = store.insert_message(self.conn, {"chat_id": 1, "tg_message_id": 1,
                                               "received_at": store._now(), "raw_text": "спасибо за день"})
        store.set_suggestion(self.conn, mid, "Благодарности", "благодарность", "m")
        row = store.get_message(self.conn, mid)
        with mock.patch.object(self.agent, "edit_suggestion_message"), \
                mock.patch.object(self.agent, "reply") as r:
            self.agent.apply_category_confirm(1, row, "Благодарности", None)
        self.assertIn("дневник", r.call_args[0][1].lower())
        self.assertIn("Благодарности", r.call_args[0][1])

    def test_journal_show_lists_dated_entries(self):
        store.set_category_kind(self.conn, "Благодарности", "journal")
        self._entry(1, "спасибо номер один", "Благодарности")
        self._entry(2, "спасибо номер два", "Благодарности")
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.do_journal_show(1, "ru", {"category": "Благодарности", "period": "all"})
        out = r.call_args[0][1]
        self.assertIn("Дневник", out)
        self.assertIn("спасибо номер один", out)
        self.assertIn("спасибо номер два", out)
        self.assertTrue(skill_manifest.known("journal_show"))

    def test_clear_all_notes_spares_journal(self):
        j = self._entry(1, "спасибо", "Благодарности")
        store.set_category_kind(self.conn, "Благодарности", "journal")
        n = self._entry(2, "разовая заметка", "Разное")
        info, _ = store.purge_execute(self.conn, "messages")
        self.assertEqual(info["messages"], 1)                       # only the inbox note
        self.assertEqual(info.get("kept_journal"), 1)
        self.assertIsNotNone(store.get_message(self.conn, j))       # journal kept
        self.assertIsNone(store.get_message(self.conn, n))          # one-time cleared

    def test_journal_digest_line(self):
        store.set_category_kind(self.conn, "Благодарности", "journal")
        self._entry(1, "спасибо", "Благодарности")
        line = review.journal_digest(self.conn, "ru")
        self.assertIsNotNone(line)
        self.assertIn("Благодарности", line)


class DisplayNumberTests(unittest.TestCase):
    """User-facing note numbers are a contiguous 1..N position (oldest first)
    that compacts on deletion; the immutable id stays the internal key."""

    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "d.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _note(self, tg_id, text):
        mid = store.insert_message(self.conn, {"chat_id": 1, "tg_message_id": tg_id,
                                               "received_at": store._now(), "raw_text": text})
        store.confirm_category(self.conn, mid, "Разное")
        return mid

    def test_numbers_contiguous_and_compact_on_delete(self):
        a, b, c = self._note(1, "first"), self._note(2, "second"), self._note(3, "third")
        self.assertEqual([store.display_no(self.conn, x) for x in (a, b, c)], [1, 2, 3])
        self.assertEqual(store.message_by_display_no(self.conn, 2)["id"], b)
        for _ in store.delete_message(self.conn, b):  # remove the middle note
            pass
        self.assertEqual(store.display_no(self.conn, a), 1)
        self.assertEqual(store.display_no(self.conn, c), 2)        # was 3, compacted
        self.assertIsNone(store.message_by_display_no(self.conn, 3))

    def test_resolve_item_uses_display_number(self):
        a, b = self._note(1, "alpha"), self._note(2, "bravo")
        self.assertEqual(self.agent.resolve_item({"id": 2})["id"], b)
        self.assertEqual(self.agent.resolve_item({"query": "заметку 1"})["id"], a)

    def test_delete_by_display_number_then_renumbers(self):
        a, b, c = self._note(1, "a"), self._note(2, "b"), self._note(3, "c")
        rows = self.agent.resolve_items({"ids": [1]})            # user types "#1"
        self.assertEqual(rows[0]["id"], a)
        pending = {"kind": "delete", "payload": {"row_ids": [a]}}
        with mock.patch.object(self.agent, "reply") as r:
            self.agent.resolve_pending(1, "confirm", {}, pending, "ru")
        self.assertIn("#1", r.call_args[0][1])                  # acked as the number shown
        self.assertIsNone(store.get_message(self.conn, a))
        self.assertEqual(store.display_no(self.conn, b), 1)      # remaining notes renumber
        self.assertEqual(store.display_no(self.conn, c), 2)

    def test_item_list_shows_sequential_numbers(self):
        self._note(1, "a"); self._note(2, "b"); self._note(3, "c")
        text = self.agent.items_text("ru", {})
        self.assertIn("#1", text)
        self.assertIn("#2", text)
        self.assertIn("#3", text)


class OperatingModelTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "om.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_operating_model_groups_facts(self):
        import boss_model
        store.boss_add(self.conn, "project", "Строит ассистента Cara.", status="confirmed")
        store.boss_add(self.conn, "personal_fact", "Любит крепкий чай.", status="inferred")
        groups = dict(boss_model.operating_model(self.conn, "ru"))
        self.assertIn("Строит ассистента Cara.", groups.get("проекты", []))
        self.assertIn("Любит крепкий чай.", groups.get("о нём", []))

    def test_ongoing_threads_lists_open_loops(self):
        import relationship
        self.conn.execute("INSERT INTO messages (chat_id, tg_message_id, received_at, status,"
                          " suggested_category) VALUES (1, 1, ?, 'suggested', 'news')",
                          (store._now(),))
        store.candidate_add(self.conn, "workflow", "auto-file X", confidence=0.9)
        self.conn.commit()
        threads = relationship.ongoing_threads(self.conn, "ru")
        self.assertTrue(any("категори" in t for t in threads))
        self.assertTrue(any("памят" in t for t in threads))

    def test_morning_brief_opt_in_and_content(self):
        import review
        from datetime import datetime, timezone, timedelta
        # nothing pending -> no brief
        self.assertIsNone(review.morning_brief(self.conn, self.agent.cfg, "ru", 3, "Олег"))
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        store.reminder_add(self.conn, 1, "позвонить в банк", past)   # overdue
        brief = review.morning_brief(self.conn, self.agent.cfg, "ru", 3, "Олег")
        self.assertIsNotNone(brief)
        self.assertIn("Доброе утро", brief)
        self.assertIn("позвонить в банк", brief)
        # toggle via proactive_prefs
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_proactive_prefs(1, "ru", {"morning_brief": True})
        self.assertEqual(store.pref_get(self.conn, "morning_brief"), "on")

    def test_trace_export_scrubs_and_renders(self):
        import trace
        import common
        tid = trace.start(self.conn, "inbound", 1)
        trace.event(self.conn, tid, trace.ROUTER_COMPLETED, "action=converse", skill="converse")
        trace.event(self.conn, tid, trace.LLM_FALLBACK, "Bearer sk_supersecrettoken1234567890ABCDEF")
        trace.finish(self.conn, tid, "finished")
        fname, md = self.agent._last_trace_markdown(1)
        self.assertIn(tid, fname)
        self.assertIn("router.completed", md)
        self.assertNotIn("supersecrettoken", md)                  # secret scrubbed
        self.assertEqual(common.scrub_secrets("Bearer abcDEF1234567890abcDEF1234567890"),
                         "Bearer ***")


class Tier1ResearchTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "t1.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_memory_provenance_explain(self):
        import boss_model
        store.boss_add(self.conn, "personal_fact", "Любит крепкий чёрный чай.",
                       status="confirmed", source_table="explicit")
        ans = boss_model.explain(self.conn, "ru", "откуда ты знаешь, что я люблю чай?")
        self.assertIsNotNone(ans)
        self.assertIn("чай", ans.lower())
        self.assertIn("сам", ans.lower())                     # "Ты сам мне это сказал" (explicit)
        self.assertIsNone(boss_model.explain(self.conn, "ru", "откуда ты знаешь про слона?"))

    def test_do_memory_why_falls_back_to_chat(self):
        with mock.patch.object(self.agent, "do_converse") as conv:
            self.agent.do_memory_why(1, "ru", "откуда ты знаешь про слона?")
        conv.assert_called_once()                              # no match -> warm chat, in character

    def test_conflicting_fact_is_proposed_not_autostored(self):
        import boss_model
        import memory_curator
        store.boss_add(self.conn, "tone", "Любит короткие ответы.", status="confirmed")
        self.assertTrue(boss_model.conflicts_with_confirmed(self.conn, "Любит длинные подробные ответы."))
        store.convo_add(self.conn, 1, "user", "я люблю длинные подробные ответы")
        store.convo_add(self.conn, 1, "bot", "ок")
        payload = ('{"cara_life":[],"boss_facts":[{"kind":"tone",'
                   '"text":"Любит длинные подробные ответы."}]}')
        with mock.patch.object(llm, "chat_profile", return_value=payload):
            memory_curator.curate_conversation(self.conn, self.agent.cfg, 1)
        inferred = [r["value"] for r in store.boss_items(self.conn, "inferred")]
        self.assertNotIn("Любит длинные подробные ответы.", inferred)   # not silently auto-stored
        cand = [c["proposed_text"] for c in store.candidates_pending(self.conn)]
        self.assertIn("Любит длинные подробные ответы.", cand)          # proposed for confirmation

    def test_proactive_can_be_turned_off(self):
        import proactive
        from datetime import datetime, timezone
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_proactive_prefs(1, "ru", {"enabled": False})
        self.assertEqual(store.pref_get(self.conn, "proactive_enabled"), "false")
        store.candidate_add(self.conn, "workflow", "x", confidence=0.9)
        key = proactive.run(self.conn, self.agent.cfg, "ru", lambda t: None,
                            now=datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc))
        self.assertIsNone(key)                                  # disabled -> no nudge

    def test_proactive_weekends_only_suppresses_weekday(self):
        import proactive
        from datetime import datetime, timezone
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_proactive_prefs(1, "ru", {"days": "weekends"})
        store.candidate_add(self.conn, "workflow", "y", confidence=0.9)
        key = proactive.run(self.conn, self.agent.cfg, "ru", lambda t: None,
                            now=datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc))  # Monday
        self.assertIsNone(key)
        self.assertTrue(skill_manifest.known("proactive_prefs"))
        self.assertTrue(skill_manifest.known("memory_why"))


class ForwardAttachmentTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(ALLOWED_CHAT_IDS="1", DB_PATH=str(Path(self.tmp.name) / "f.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_other_attachment_detects_media(self):
        self.assertEqual(
            self.agent.other_attachment({"voice": {"file_id": "V", "file_unique_id": "u"}})["file_id"], "V")
        self.assertEqual(
            self.agent.other_attachment({"video": {"file_id": "X", "file_unique_id": "u"}})["mime_type"],
            "video/mp4")
        self.assertIsNone(self.agent.other_attachment({"text": "hi"}))

    def test_forwarded_voice_is_stored_not_transcribed(self):
        msg = {"chat": {"id": 1}, "from": {"id": 1}, "message_id": 7,
               "forward_origin": {"type": "channel", "title": "Some Channel"},
               "voice": {"file_id": "V", "file_unique_id": "vu", "duration": 15},
               "text": "важный пост из канала"}
        with mock.patch.object(self.agent, "transcribe_voice") as tv, \
                mock.patch.object(self.agent, "finalize") as fin:
            self.agent.handle_update({"message": msg})
        tv.assert_not_called()        # forwarded voice is channel content, never transcribed
        fin.assert_called_once()      # it goes to ingest (text parsed, voice stored)

    def test_own_voice_note_is_transcribed_and_routed(self):
        msg = {"chat": {"id": 1}, "from": {"id": 1}, "message_id": 8,
               "voice": {"file_id": "V", "file_unique_id": "vu", "duration": 5}}
        with mock.patch.object(self.agent, "transcribe_voice", return_value="напомни завтра") as tv, \
                mock.patch.object(self.agent, "dispatch") as disp, \
                mock.patch.object(self.agent, "reply"):
            self.agent.handle_update({"message": msg})
        tv.assert_called_once()
        disp.assert_called_once()     # the transcript is routed as a command

    def test_finalize_parses_text_and_stores_voice(self):
        msg = {"chat": {"id": 1}, "message_id": 9, "date": 1781200000, "from": {"id": 1},
               "forward_origin": {"type": "channel", "title": "Chan"},
               "voice": {"file_id": "VID", "file_unique_id": "vu", "duration": 15},
               "text": "текст поста"}
        with mock.patch.object(self.agent, "suggest_row", return_value=("News", [], "s")), \
                mock.patch.object(self.agent, "present_suggestion"):
            self.agent.finalize([msg])
        row = self.conn.execute("SELECT id, raw_text FROM messages WHERE tg_message_id=9").fetchone()
        self.assertEqual(row["raw_text"], "текст поста")     # the forward's TEXT is the content
        files = store.message_files(self.conn, row["id"])
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["tg_file_id"], "VID")      # voice kept as a fetchable file


class AccessControlTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(ALLOWED_CHAT_IDS="111",
                          DB_PATH=str(Path(self.tmp.name) / "a.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)

    def tearDown(self):
        self.agent.conn.close()
        self.tmp.cleanup()

    def test_only_owner_in_owner_chat(self):
        self.assertTrue(self.agent.is_owner(111, 111))    # the owner in his chat
        self.assertFalse(self.agent.is_owner(111, 999))   # stranger posting in the chat
        self.assertFalse(self.agent.is_owner(999, 111))   # owner's id, but another chat/group
        self.assertFalse(self.agent.is_owner(None, None))
        self.assertFalse(self.agent.is_owner(111, None))

    def test_handle_update_ignores_non_owner(self):
        update = {"message": {"chat": {"id": 111}, "from": {"id": 999}, "text": "привет"}}
        with mock.patch.object(self.agent, "dispatch") as d:
            self.agent.handle_update(update)
        d.assert_not_called()  # a stranger never reaches dispatch


class ReviewScheduleTests(unittest.TestCase):
    def test_next_review_is_future_weekday_hour(self):
        from datetime import datetime, timezone
        now = datetime(2026, 6, 14, 8, 0, tzinfo=timezone.utc)         # Sunday
        nxt = review.next_review_utc(now, tz_offset=3, weekday=0, hour=10)  # Mon 10:00 MSK
        self.assertEqual(nxt, datetime(2026, 6, 15, 7, 0, tzinfo=timezone.utc))
        self.assertGreater(nxt, now)

    def test_same_day_past_hour_rolls_a_week(self):
        from datetime import datetime, timezone
        now = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)         # Mon 11:00 MSK, past 10
        nxt = review.next_review_utc(now, tz_offset=3, weekday=0, hour=10)
        self.assertEqual(nxt, datetime(2026, 6, 22, 7, 0, tzinfo=timezone.utc))  # +1 week

    def test_do_review_answers_schedule_without_running_report(self):
        import tg_ingest_agent
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = make_config(REVIEW_WEEKDAY="0", REVIEW_HOUR="10",
                          DB_PATH=str(Path(tmp.name) / "r.db"),
                          MEDIA_DIR=str(Path(tmp.name) / "m"))
        agent = tg_ingest_agent.Agent(cfg)
        self.addCleanup(agent.conn.close)
        with mock.patch.object(agent, "reply") as r, \
             mock.patch.object(review, "chat_text") as report:
            agent.do_review(1, "ru", {"schedule": True})
        report.assert_not_called()                    # schedule answer, not the full report
        msg = r.call_args[0][1]
        self.assertIn("performance review", msg.lower())
        self.assertIn("понедельник", msg)             # configured weekday


class CurationThrottleTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(DB_PATH=str(Path(self.tmp.name) / "t.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)

    def tearDown(self):
        self.agent.conn.close()
        self.tmp.cleanup()

    def test_curation_runs_every_n_turns(self):
        import memory_curator
        with mock.patch.object(memory_curator, "curate_conversation",
                               return_value={"life": 0, "boss": 0}) as cc:
            for _ in range(self.agent.CURATE_EVERY - 1):
                self.agent.maybe_curate_conversation(1)
            cc.assert_not_called()           # not yet
            self.agent.maybe_curate_conversation(1)
            cc.assert_called_once()           # fires on the Nth turn

    def test_correction_forces_immediate_learning(self):
        import memory_curator
        self.assertTrue(self.agent.looks_like_correction("Почему ты ответила на английском?"))
        self.assertTrue(self.agent.looks_like_correction("ты опять ошиблась"))
        self.assertFalse(self.agent.looks_like_correction("когда мой рейс?"))
        with mock.patch.object(memory_curator, "curate_conversation",
                               return_value={"life": 0, "boss": 0, "corrections": 1}) as cc:
            self.agent.maybe_curate_conversation(1, force=True)  # no waiting for the throttle
            cc.assert_called_once()


class SelfBossPersonaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "p.db")
        self.cfg = make_config()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_self_model_seed_and_answer(self):
        self_model.seed(self.conn)
        self_model.seed(self.conn)  # idempotent
        facts = {r["key"]: r["value"] for r in store.self_facts(self.conn)}
        self.assertEqual(facts["name"], "Cara")
        for lang in ("ru", "en"):
            ans = self_model.answer_self_query(self.conn, lang, self.cfg)
            self.assertIn("Cara", ans)
            self.assertIn("Google", ans)  # dormant capability surfaced from cfg
            # D3: never describe herself as software/infrastructure (a disclaimer leak)
            for leak in ("VPS", "SQLite", "polling", "поллинг", "чат-бот", "процесс"):
                self.assertNotIn(leak, ans)

    def test_boss_remember_render_forget_confirm(self):
        boss_model.remember_explicit(self.conn, "  prefers short answers ", "tone")
        item = store.boss_items(self.conn, "confirmed")[0]
        self.assertEqual(item["value"], "prefers short answers")
        self.assertEqual(item["status"], "confirmed")
        rendered = boss_model.render_profile(self.conn, "en")
        self.assertIn("prefers short answers", rendered)
        self.assertIn(f"#{item['id']}", rendered)
        # forget by id
        self.assertEqual(boss_model.forget(self.conn, f"#{item['id']}"), "prefers short answers")
        self.assertEqual(store.boss_items(self.conn, "confirmed"), [])
        self.assertIsNone(boss_model.forget(self.conn, "#999"))

    def test_boss_sensitivity_classification(self):
        self.assertEqual(boss_model.classify_sensitivity("мой пароль 1234"), "sensitive")
        self.assertEqual(boss_model.classify_sensitivity("likes markdown specs"), "normal")
        # health terms must be flagged (regression: "peanut allergy" leaked)
        self.assertEqual(boss_model.classify_sensitivity("peanut allergy"), "sensitive")
        self.assertEqual(boss_model.classify_sensitivity("аллергия на орехи"), "sensitive")
        self.assertEqual(boss_model.classify_sensitivity("мой адрес: ..."), "sensitive")

    def test_kind_floor_overrides_keyword_miss(self):
        # the bug class: a personal_fact with no keyword match must NOT be normal
        self.assertEqual(boss_model.effective_sensitivity("personal_fact", "loves jazz"), "sensitive")
        self.assertEqual(boss_model.effective_sensitivity("identity", "Owen from Ufa"), "private")
        # regex can still RAISE above the floor
        self.assertEqual(boss_model.effective_sensitivity("identity", "passport 12 34"), "sensitive")
        # plain workflow stays normal
        self.assertEqual(boss_model.effective_sensitivity("workflow", "likes md specs"), "normal")
        # stored item reflects the floor
        sid = boss_model.remember_explicit(self.conn, "loves jazz", "personal_fact")
        self.assertEqual(store.boss_get(self.conn, sid)["sensitivity"], "sensitive")

    def test_sensitive_corpus_never_normal(self):
        corpus = ["peanut allergy", "аллергия на орехи", "my IBAN is DE...", "salary 200k",
                  "домашний адрес", "паспорт 4509", "diagnosis: ...", "credit card 4111"]
        for text in corpus:
            self.assertNotEqual(boss_model.effective_sensitivity("workflow", text), "normal",
                                f"leaked: {text}")

    def test_persona_hint_excludes_nonnormal_invariant(self):
        boss_model.remember_explicit(self.conn, "prefers short answers", "tone")  # normal
        boss_model.remember_explicit(self.conn, "loves jazz", "personal_fact")    # floored sensitive
        boss_model.remember_explicit(self.conn, "peanut allergy", "personal_fact")
        hint = persona.boss_preference_hint(self.conn)
        self.assertIn("prefers short answers", hint)
        for leaked in ("jazz", "peanut"):
            self.assertNotIn(leaked, hint)  # invariant: only normal in prompt personalization
        sid = boss_model.remember_explicit(self.conn, "health: peanut allergy", "personal_fact")
        self.assertEqual(store.boss_get(self.conn, sid)["sensitivity"], "sensitive")

    def test_persona_hint_only_includes_confirmed_normal(self):
        self.assertEqual(persona.boss_preference_hint(self.conn), "")  # nothing yet
        boss_model.remember_explicit(self.conn, "prefers short answers", "tone")
        boss_model.remember_explicit(self.conn, "salary is confidential", "personal_fact")  # sensitive
        hint = persona.boss_preference_hint(self.conn)
        self.assertIn("prefers short answers", hint)
        self.assertNotIn("salary", hint)  # sensitive excluded from prompt personalization

    def test_confirm_when_personal_flow(self):
        import tg_ingest_agent
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(DB_PATH=str(Path(tmp) / "cp.db"), MEDIA_DIR=str(Path(tmp) / "m"))
            agent = tg_ingest_agent.Agent(cfg)
            try:
                # normal preference stores immediately, no pending
                with mock.patch.object(agent, "reply"):
                    agent.do_boss_memory(1, "ru", {"op": "remember",
                                                   "value": "prefers short answers", "kind": "tone"})
                self.assertEqual(len(store.boss_items(agent.conn, "confirmed")), 1)
                self.assertIsNone(store.pending_get(agent.conn, 1))
                # a personal_fact is NOT stored yet — it asks first
                with mock.patch.object(agent, "reply") as r:
                    agent.do_boss_memory(1, "ru", {"op": "remember",
                                                   "value": "loves jazz", "kind": "personal_fact"})
                self.assertIn("личное", r.call_args[0][1])
                self.assertEqual(store.pending_get(agent.conn, 1)["kind"], "boss_sensitive")
                self.assertEqual(len(store.boss_items(agent.conn, "confirmed")), 1)  # not stored yet
                # confirm -> stored as sensitive
                with mock.patch.object(agent, "reply"):
                    agent.resolve_pending(1, "confirm", {},
                                          store.pending_get(agent.conn, 1), "ru")
                items = store.boss_items(agent.conn, "confirmed")
                jazz = [i for i in items if i["value"] == "loves jazz"][0]
                self.assertEqual(jazz["sensitivity"], "sensitive")
                self.assertIsNone(store.pending_get(agent.conn, 1))
                # decline path leaves it unsaved
                with mock.patch.object(agent, "reply"):
                    agent.do_boss_memory(1, "ru", {"op": "remember",
                                                   "value": "home address X", "kind": "personal_fact"})
                    agent.resolve_pending(1, "cancel", {},
                                          store.pending_get(agent.conn, 1), "ru")
                self.assertEqual([i for i in store.boss_items(agent.conn, "confirmed")
                                  if i["value"] == "home address X"], [])
            finally:
                agent.conn.close()

    def test_router_accepts_personality_actions(self):
        for action in ("self_query", "boss_query", "boss_memory_update", "style_update",
                       "trace_query"):
            ok = router.validate_route({"action": action, "params": {}}, False)
            self.assertEqual(ok["action"], action)
            self.assertTrue(skill_manifest.known(action))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "e.db")
        self.cfg = make_config(DB_PATH=str(Path(self.tmp.name) / "e.db"))
        self_model.seed(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_review_markdown_has_new_sections(self):
        store.usage_add(self.conn, "ask", "chat", "m", 1, 1, cost_usd=0.001)
        store.boss_add(self.conn, "tone", "prefers short answers", status="confirmed")
        store.candidate_add(self.conn, "workflow", "auto-file X", confidence=0.9)
        md = review.markdown(self.conn, self.cfg, "week")
        for section in ("## What I learned about you", "## Pending memory candidates",
                        "## System health"):
            self.assertIn(section, md)
        self.assertIn("prefers short answers", md)
        self.assertIn("auto-file X", md)

    def test_export_self_and_candidates(self):
        fn, md = review.export_document(self.conn, self.cfg, "self", "en")
        self.assertTrue(fn.startswith("cara-self-"))
        self.assertIn("Cara", md)
        store.candidate_add(self.conn, "tone", "likes brevity", confidence=0.9)
        fn2, md2 = review.export_document(self.conn, self.cfg, "candidates", "en")
        self.assertIn("likes brevity", md2)

    def test_export_profile_redacts_by_default_and_full(self):
        store.boss_add(self.conn, "workflow", "uses VS Code", status="confirmed",
                       sensitivity="normal")
        store.boss_add(self.conn, "personal_fact", "peanut allergy", status="confirmed",
                       sensitivity="sensitive")
        store.boss_add(self.conn, "identity", "lives in Ufa", status="confirmed",
                       sensitivity="private")
        store.boss_add(self.conn, "identity", "api token abc", status="confirmed",
                       sensitivity="secret")
        fn, md = review.export_document(self.conn, self.cfg, "profile", "en")
        self.assertIn("uses VS Code", md)            # normal: shown
        self.assertIn("(sensitive — withheld", md)
        self.assertIn("(private — withheld", md)     # default-deny: private withheld too
        self.assertNotIn("peanut allergy", md)
        self.assertNotIn("lives in Ufa", md)
        self.assertNotIn("api token abc", md)        # secret omitted entirely
        # full export reveals private+sensitive (still never secret)
        _, full = review.export_document(self.conn, self.cfg, "profile", "en", full=True)
        self.assertIn("peanut allergy", full)
        self.assertIn("lives in Ufa", full)
        self.assertNotIn("api token abc", full)

    def test_export_default_is_review(self):
        fn, md = review.export_document(self.conn, self.cfg, "garbage", "en")
        self.assertIn("cara-review-", fn)
        self.assertIn("# Cara performance review", md)

    def test_router_accepts_export(self):
        self.assertEqual(router.validate_route(
            {"action": "export", "params": {"what": "profile"}}, False)["action"], "export")
        self.assertTrue(skill_manifest.known("export"))


class MemoryCuratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "c.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_run_daily_proposes_from_repeated_corrections(self):
        # one-off correction -> no candidate; repeated -> candidate
        store.feedback_add(self.conn, "ingest", "d", "news", "Crypto")
        self.assertEqual(memory_curator.run_daily(self.conn), 0)
        store.feedback_add(self.conn, "ingest", "d", "tools", "Crypto")
        created = memory_curator.run_daily(self.conn)
        self.assertEqual(created, 1)
        cands = store.candidates_pending(self.conn)
        self.assertEqual(len(cands), 1)
        self.assertIn("Crypto", cands[0]["proposed_text"])
        # idempotent: re-running doesn't duplicate
        self.assertEqual(memory_curator.run_daily(self.conn), 0)

    def test_run_daily_proposes_from_habit_pref(self):
        store.pref_set(self.conn, "auto_cat:-100123", "Flight Deals")
        memory_curator.run_daily(self.conn)
        texts_ = [c["proposed_text"] for c in store.candidates_pending(self.conn)]
        self.assertTrue(any("Flight Deals" in t for t in texts_))

    def test_confirm_candidate_promotes_to_boss_item(self):
        cid = store.candidate_add(self.conn, "tone", "prefers short answers", confidence=0.9)
        value, accepted = memory_curator.confirm_candidate(self.conn, cid, True)
        self.assertEqual((value, accepted), ("prefers short answers", True))
        self.assertEqual(store.boss_items(self.conn, "confirmed")[0]["value"], "prefers short answers")
        self.assertEqual(store.candidate_get(self.conn, cid)["status"], "confirmed")
        # a relationship event was logged
        self.assertTrue(store.rel_recent(self.conn, "2000-01-01"))
        # re-confirming a decided candidate is a no-op
        self.assertEqual(memory_curator.confirm_candidate(self.conn, cid, True), (None, None))

    def test_reject_candidate(self):
        cid = store.candidate_add(self.conn, "tone", "x", confidence=0.9)
        value, accepted = memory_curator.confirm_candidate(self.conn, cid, False)
        self.assertEqual(accepted, False)
        self.assertEqual(store.candidate_get(self.conn, cid)["status"], "rejected")
        self.assertEqual(store.boss_items(self.conn, "confirmed"), [])

    def test_score_gate(self):
        self.assertGreaterEqual(memory_curator.score("tone", "normal", 0.9, "short"), 2)
        self.assertLess(memory_curator.score("personal_fact", "sensitive", 0.9, "health"), 2)

    def test_working_history_evidence_based(self):
        self.assertEqual(relationship.render_working_history(self.conn, "ru"),
                         texts.T("ru", "working_history_empty"))
        mid = store.insert_message(self.conn, {"chat_id": 1, "tg_message_id": 1,
                                               "received_at": store._now(), "raw_text": "x"})
        store.confirm_category(self.conn, mid, store.ensure_category(self.conn, "News"))
        relationship.log_event(self.conn, "category_confirmed", "filed a message as «News»", 1)
        hist = relationship.render_working_history(self.conn, "en")
        self.assertIn("Saved & filed: 1", hist)
        self.assertIn("News", hist)

    def test_rel_event_stores_title_and_trace(self):
        import common
        common.set_current_trace("tr_test_1")
        try:
            relationship.log_event(self.conn, "document_saved", "kept a document: Расписка.pdf",
                                   importance=2, title="Расписка.pdf")
        finally:
            common.set_current_trace(None)
        row = store.rel_recent(self.conn, "2000-01-01")[0]
        self.assertEqual(row["title"], "Расписка.pdf")
        self.assertEqual(row["trace_id"], "tr_test_1")

    def test_router_accepts_phase_c_actions(self):
        for action in ("memory_review", "working_history"):
            self.assertEqual(router.validate_route({"action": action, "params": {}}, False)["action"],
                             action)
            self.assertTrue(skill_manifest.known(action))


class EventJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "ej.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_event_claim_once_and_complete(self):
        events.add_event(self.conn, "proactive_tick")
        first = events.claim_next(self.conn)
        self.assertIsNotNone(first)
        self.assertIsNone(events.claim_next(self.conn))  # not claimed twice
        events.complete(self.conn, first["id"])
        self.assertEqual(store.trace_get(self.conn, "x"), None)  # unrelated, sanity

    def test_event_future_not_claimed(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        events.add_event(self.conn, "weekly_digest_due", available_at=future)
        self.assertIsNone(events.claim_next(self.conn))

    def test_event_retry_then_terminal(self):
        events.add_event(self.conn, "retry_failed_job", max_attempts=2)
        c1 = events.claim_next(self.conn)
        events.fail(self.conn, c1["id"])  # attempts=1 < 2 -> back to pending
        c2 = events.claim_next(self.conn)
        self.assertEqual(c2["id"], c1["id"])
        events.fail(self.conn, c2["id"])  # attempts=2 == max -> terminal failed
        self.assertIsNone(events.claim_next(self.conn))
        row = self.conn.execute("SELECT status FROM events WHERE id = ?", (c1["id"],)).fetchone()
        self.assertEqual(row["status"], "failed")

    def test_job_claim_complete_and_payload(self):
        jid = jobs.add_job(self.conn, "memory_curator", "run_memory_curator",
                           payload={"day": "2026-06-13"})
        self.assertTrue(jobs.has_pending(self.conn, "memory_curator", "run_memory_curator"))
        job = jobs.claim_next(self.conn)
        self.assertEqual(jobs.payload_of(job), {"day": "2026-06-13"})
        jobs.complete(self.conn, job["id"], {"candidates": 3})
        self.assertFalse(jobs.has_pending(self.conn, "memory_curator", "run_memory_curator"))

    def test_runtime_drain_runs_handler_and_failover(self):
        ran = {}

        def good(ctx, conn, payload, job):
            ran["ok"] = payload.get("x")
            return {"done": True}

        def boom(ctx, conn, payload, job):
            raise RuntimeError("nope")
        runtime.register("t", "good", good)
        runtime.register("t", "boom", boom)
        try:
            jobs.add_job(self.conn, "t", "good", payload={"x": 5}, max_attempts=1)
            jobs.add_job(self.conn, "t", "boom", max_attempts=1)
            processed = runtime.drain(self.conn, ctx=None)
            self.assertEqual(processed, 2)
            self.assertEqual(ran["ok"], 5)
            # boom failed terminally -> job 'failed' + an issue logged
            statuses = {r["action"]: r["status"] for r in
                        self.conn.execute("SELECT action, status FROM jobs")}
            self.assertEqual(statuses["good"], "done")
            self.assertEqual(statuses["boom"], "failed")
            self.assertEqual(store.issue_counts(self.conn, "2000-01-01")[0]["kind"], "job_failed")
            # unknown job -> failed, no crash
            jobs.add_job(self.conn, "t", "unregistered", max_attempts=1)
            runtime.drain(self.conn, ctx=None)
        finally:
            runtime._HANDLERS.clear()


class KnowledgeTests(unittest.TestCase):
    def test_chunk_text(self):
        self.assertEqual(knowledge.chunk_text(""), [])
        self.assertEqual(knowledge.chunk_text("  \n  "), [])
        # short text -> one chunk
        self.assertEqual(knowledge.chunk_text("Рейс 14 июня в 10:05"), ["Рейс 14 июня в 10:05"])
        # paragraphs split when over the budget
        doc = "\n\n".join([f"Section {i} " + "x" * 300 for i in range(4)])
        chunks = knowledge.chunk_text(doc, max_chars=400)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 800 for c in chunks))

    def test_cosine(self):
        self.assertAlmostEqual(knowledge.cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(knowledge.cosine([1, 0], [0, 1]), 0.0)
        self.assertEqual(knowledge.cosine([], [1]), -1.0)
        self.assertEqual(knowledge.cosine([0, 0], [1, 1]), -1.0)

    def test_rank_chunks_orders_and_budgets(self):
        import json as _json
        rows = [
            {"message_id": 1, "text": "flight info", "embedding": _json.dumps([1.0, 0.0]),
             "category": "Plan", "suggested_category": None, "title": "Trip"},
            {"message_id": 2, "text": "weather", "embedding": _json.dumps([0.0, 1.0]),
             "category": "Misc", "suggested_category": None, "title": None},
            {"message_id": 3, "text": "bad", "embedding": "not-json",
             "category": None, "suggested_category": "x", "title": None},
        ]
        ranked = knowledge.rank_chunks([1.0, 0.05], rows, top_k=6, context_chars=6000)
        self.assertEqual(ranked[0]["message_id"], 1)  # most similar first
        self.assertTrue(all(r["message_id"] != 3 for r in ranked))  # bad embedding skipped
        # context budget caps how many chunks are included
        tight = knowledge.rank_chunks([1.0, 1.0], rows, top_k=6, context_chars=5)
        self.assertEqual(len(tight), 1)

    def test_build_ask_messages_grounding(self):
        msgs = knowledge.build_ask_messages(
            "когда рейс?",
            [{"message_id": 7, "text": "Рейс 14 июня 10:05", "category": "Plan", "title": "Trip"}])
        sys = msgs[0]["content"]
        self.assertIn("ONLY", sys)
        self.assertIn("did", sys.lower()) if "didn't find" in sys.lower() else None
        self.assertIn("Рейс 14 июня 10:05", sys)
        self.assertIn("#7", sys)
        self.assertEqual(msgs[1]["content"], "когда рейс?")
        # no context -> still grounded, explicit no-match marker
        empty = knowledge.build_ask_messages("q", [])
        self.assertIn("no stored notes matched", empty[0]["content"])

    def test_salient_terms(self):
        terms = knowledge.salient_terms("когда мой рейс из Уфы?")
        self.assertIn("рейс", terms)
        self.assertNotIn("когда", terms)  # stopword

    def test_embed_logs_usage_and_aligns(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = store.open_db(Path(tmp) / "e.db")
            cfg = make_config()
            fake = {"data": [{"index": 1, "embedding": [0.1, 0.2]},
                             {"index": 0, "embedding": [0.3, 0.4]}],
                    "usage": {"prompt_tokens": 8}}

            class FakeResp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): import json as j; return j.dumps(fake).encode()

            try:
                with mock.patch.object(llm, "urlopen", return_value=FakeResp()):
                    vecs = llm.embed(cfg, conn, "ask", ["a", "b"])
                self.assertEqual(vecs, [[0.3, 0.4], [0.1, 0.2]])  # reordered by index
                self.assertGreater(store.usage_total(conn, "day"), 0)  # embeddings billed
            finally:
                conn.close()

    def test_embed_wraps_bare_socket_timeout_as_llmerror(self):
        # A read-timeout during response.read() raises a bare socket.timeout/
        # TimeoutError (NOT a URLError). It must be wrapped as LLMError so
        # index_message's `except LLMError` catches it instead of crashing the
        # whole update handler and leaving the user with no reply.
        with tempfile.TemporaryDirectory() as tmp:
            conn = store.open_db(Path(tmp) / "e.db")
            cfg = make_config()
            try:
                with mock.patch.object(
                    llm, "urlopen",
                    side_effect=TimeoutError("The read operation timed out")):
                    with self.assertRaises(llm.LLMError):
                        llm.embed(cfg, conn, "ask", ["a"])
            finally:
                conn.close()

    def test_chat_wraps_bare_socket_timeout_as_llmerror(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = store.open_db(Path(tmp) / "c.db")
            cfg = make_config()
            try:
                with mock.patch.object(
                    llm, "urlopen",
                    side_effect=TimeoutError("The read operation timed out")):
                    with self.assertRaises(llm.LLMError):
                        llm.chat(cfg, conn, "ingest",
                                 [{"role": "user", "content": "hi"}])
            finally:
                conn.close()

    def test_router_accepts_ask(self):
        ok = router.validate_route({"action": "ask", "params": {"question": "q"}}, False)
        self.assertEqual(ok["action"], "ask")


class StorageTests(unittest.TestCase):
    # AWS-documented SigV4 example (GET examplebucket/test.txt, us-east-1/s3,
    # 20130524T000000Z). If our signing matches this published vector, the
    # crypto is correct. https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html
    SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
    ACCESS = "AKIAIOSFODNN7EXAMPLE"

    def test_signing_key_vector(self):
        # AWS-documented signing-key derivation intermediate (iam example).
        key = storage.signing_key("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
                                   "20120215", "us-east-1", "iam")
        self.assertEqual(
            key.hex(),
            "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d")

    def test_canonical_request_matches_aws_vector(self):
        # AWS publishes the canonical-request hash for the GET example.
        empty_hash = storage._sha256_hex(b"")
        self.assertEqual(empty_hash,
                         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        headers = {"host": "examplebucket.s3.amazonaws.com", "range": "bytes=0-9",
                   "x-amz-content-sha256": empty_hash, "x-amz-date": "20130524T000000Z"}
        signed = "host;range;x-amz-content-sha256;x-amz-date"
        creq = storage.canonical_request("GET", "/test.txt", "", headers, signed, empty_hash)
        self.assertEqual(storage._sha256_hex(creq.encode()),
                         "7344ae5b7ee6c3e7e6b0fe0640412a37625d1fbfff95c48bbb2dc43964946972")

    def test_authorization_header_wellformed_and_consistent(self):
        import hashlib
        import hmac as _hmac
        auth, headers, sig = storage.authorization_header(
            "PUT", "/bucket/media/u.jpg", "fra1.digitaloceanspaces.com", b"IMG",
            self.ACCESS, self.SECRET, "fra1", "s3", "20260613T000000Z",
            extra_headers={"content-type": "image/jpeg"})
        self.assertIn(f"Credential={self.ACCESS}/20260613/fra1/s3/aws4_request", auth)
        self.assertIn("SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date", auth)
        self.assertIn(f"Signature={sig}", auth)
        self.assertEqual(headers["x-amz-content-sha256"], storage._sha256_hex(b"IMG"))
        # signature is reproducible from the verified primitives
        scope = "20260613/fra1/s3/aws4_request"
        creq = storage.canonical_request(
            "PUT", "/bucket/media/u.jpg", "",
            {**headers, "content-type": "image/jpeg"},
            "content-type;host;x-amz-content-sha256;x-amz-date", headers["x-amz-content-sha256"])
        sts = "\n".join(["AWS4-HMAC-SHA256", "20260613T000000Z", scope,
                         storage._sha256_hex(creq.encode())])
        expect = _hmac.new(storage.signing_key(self.SECRET, "20260613", "fra1", "s3"),
                           sts.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(sig, expect)

    def test_backend_selection_and_key(self):
        local = make_config()
        self.assertEqual(storage.backend(local), "local")  # default
        # spaces requested but no creds -> stays local (dormant, safe)
        half = make_config(STORAGE_BACKEND="spaces")
        self.assertEqual(storage.backend(half), "local")
        full = make_config(STORAGE_BACKEND="spaces", SPACES_BUCKET="b",
                           SPACES_KEY="k", SPACES_SECRET="s")
        self.assertEqual(storage.backend(full), "spaces")
        self.assertEqual(storage.object_key(full, "u.jpg"), "media/u.jpg")

    def test_offload_noop_on_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = store.open_db(Path(tmp) / "s.db")
            try:
                self.assertEqual(storage.offload(make_config(), conn, 1), 0)
            finally:
                conn.close()


class AskFlowTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tg = tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(DB_PATH=str(Path(self.tmp.name) / "k.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)

    def tearDown(self):
        self.agent.conn.close()
        self.tmp.cleanup()

    def test_index_message_chunks_and_embeds(self):
        row_id = store.insert_message(self.agent.conn, {
            "chat_id": 1, "tg_message_id": 1, "received_at": "ts",
            "raw_text": "Рейс 14 июня 10:05 Уфа."})
        with mock.patch.object(llm, "embed", return_value=[[0.1, 0.2]]) as emb:
            self.agent.index_message(row_id, "Рейс 14 июня 10:05 Уфа.")
        emb.assert_called_once()
        self.assertEqual(len(store.all_embedded_chunks(self.agent.conn)), 1)

    def test_do_ask_grounded_answer(self):
        row_id = store.insert_message(self.agent.conn, {
            "chat_id": 1, "tg_message_id": 1, "received_at": "ts", "raw_text": "Рейс 14 июня 10:05"})
        store.set_chunks(self.agent.conn, row_id, [("Рейс 14 июня 10:05", [1.0, 0.0])])
        captured = {}

        def fake_chat(cfg, conn, skill, messages, **kw):
            captured["context"] = messages[0]["content"]
            return "Твой рейс 14 июня в 10:05 (#%d)" % row_id
        with mock.patch.object(llm, "embed", return_value=[[1.0, 0.0]]), \
                mock.patch.object(llm, "chat", side_effect=fake_chat), \
                mock.patch.object(self.agent, "reply") as reply:
            self.agent.do_ask(1, "ru", {"question": "когда рейс?"}, "когда рейс?")
        self.assertIn("Рейс 14 июня 10:05", captured["context"])  # grounded in stored note
        self.assertIn("14 июня", reply.call_args[0][1])

    def test_do_ask_no_match_records_issue(self):
        with mock.patch.object(llm, "embed", return_value=[[1.0, 0.0]]), \
                mock.patch.object(llm, "chat", return_value="Не нашла в твоих заметках."), \
                mock.patch.object(self.agent, "reply"):
            self.agent.do_ask(1, "ru", {"question": "когда рейс?"}, "когда рейс?")
        self.assertEqual({r["kind"] for r in store.issues_recent(self.agent.conn, "2000-01-01")},
                         {"ask_no_context"})

    def test_text_document_ingested_as_full_text(self):
        # a sent .md document -> downloaded, decoded, stored as full raw_text
        with mock.patch.object(self.agent, "download_file") as dl:
            doc_path = Path(self.tmp.name) / "plan.md"
            doc_path.write_text("# Trip\nРейс 14 июня 10:05\nОтель: Hilton", encoding="utf-8")
            dl.return_value = str(doc_path)
            text, name = self.agent.read_text_document([{
                "document": {"file_id": "f", "file_unique_id": "u", "file_name": "plan.md",
                             "mime_type": "text/markdown"}}])
        self.assertIn("Рейс 14 июня 10:05", text)
        self.assertEqual(name, "plan.md")


class StoreMigrationTests(unittest.TestCase):
    def test_object_key_column_added_to_old_images(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            raw = sqlite3.connect(str(path))
            raw.execute(
                "CREATE TABLE images (id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL,"
                " tg_message_id INTEGER NOT NULL, tg_file_id TEXT NOT NULL,"
                " tg_file_unique_id TEXT NOT NULL, local_path TEXT)")
            raw.commit()
            raw.close()
            conn = store.open_db(path)
            try:
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(images)")}
                self.assertIn("object_key", cols)
            finally:
                conn.close()


class SysinfoTests(unittest.TestCase):
    def test_parse_meminfo(self):
        text = "MemTotal:        2014240 kB\nMemFree: 100000 kB\nMemAvailable:  1500000 kB\n"
        total, avail = sysinfo.parse_meminfo(text)
        self.assertEqual(total, 2014240 * 1024)
        self.assertEqual(avail, 1500000 * 1024)
        # falls back to MemFree when MemAvailable absent
        _, avail2 = sysinfo.parse_meminfo("MemTotal: 100 kB\nMemFree: 40 kB\n")
        self.assertEqual(avail2, 40 * 1024)

    def test_parse_loadavg_uptime_rss(self):
        self.assertEqual(sysinfo.parse_loadavg("0.15 0.10 0.05 1/200 1234"), (0.15, 0.10, 0.05))
        self.assertEqual(sysinfo.parse_loadavg("bad"), (0.0, 0.0, 0.0))
        self.assertEqual(sysinfo.parse_uptime("12345.67 9999.0"), 12345.67)
        self.assertEqual(sysinfo.parse_status_rss("VmRSS:\t   20328 kB\n"), 20328 * 1024)
        self.assertEqual(sysinfo.parse_status_rss("no rss here"), 0)

    def test_formatters(self):
        self.assertEqual(sysinfo.fmt_bytes(0), "0B")
        self.assertEqual(sysinfo.fmt_bytes(1536), "1.5KB")
        self.assertEqual(sysinfo.fmt_bytes(2 * 1024**3), "2.0GB")
        self.assertEqual(sysinfo.fmt_duration(90), "1m")
        self.assertEqual(sysinfo.fmt_duration(3700), "1h 1m")
        self.assertEqual(sysinfo.fmt_duration(90000), "1d 1h")

    def test_collect_and_report(self):
        data = sysinfo.collect("/")  # live read; must not raise
        self.assertGreaterEqual(data["cpus"], 1)
        self.assertGreater(data["disk_total"], 0)
        report = sysinfo.format_report(data, "ru", media_bytes=5 * 1024 * 1024)
        self.assertIn("CPU", report)
        self.assertIn("медиа", report)
        self.assertIn("vCPU", sysinfo.format_report(data, "en"))

    def test_router_accepts_new_actions(self):
        for action in ("show_media", "discard", "vps_stats"):
            ok = router.validate_route({"action": action, "params": {}}, False)
            self.assertEqual(ok["action"], action)


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_preferences(self):
        self.assertIsNone(store.pref_get(self.conn, "language"))
        store.pref_set(self.conn, "language", "en")
        self.assertEqual(store.pref_get(self.conn, "language"), "en")
        store.pref_set(self.conn, "language", "ru")
        self.assertEqual(store.pref_get(self.conn, "language"), "ru")
        self.assertEqual(len(store.pref_all(self.conn)), 1)
        self.assertTrue(store.pref_delete(self.conn, "language"))
        self.assertFalse(store.pref_delete(self.conn, "language"))

    def test_pending_actions_ttl(self):
        store.pending_set(self.conn, 1, "reminder", {"title": "x"}, ttl_seconds=3600)
        pending = store.pending_get(self.conn, 1)
        self.assertEqual(pending["kind"], "reminder")
        self.assertEqual(pending["payload"]["title"], "x")
        # replaced atomically per chat
        store.pending_set(self.conn, 1, "category", {"row_id": 5})
        self.assertEqual(store.pending_get(self.conn, 1)["kind"], "category")
        store.pending_clear(self.conn, 1)
        self.assertIsNone(store.pending_get(self.conn, 1))
        # expired entries vanish
        store.pending_set(self.conn, 2, "habit", {}, ttl_seconds=-5)
        self.assertIsNone(store.pending_get(self.conn, 2))

    def test_conversation_trim(self):
        for i in range(40):
            store.convo_add(self.conn, 1, "user", f"msg {i}")
        rows = store.convo_recent(self.conn, 1, limit=10)
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[-1]["text"], "msg 39")
        self.assertEqual(rows[0]["text"], "msg 30")
        total = self.conn.execute(
            "SELECT COUNT(*) AS n FROM conversation WHERE chat_id = 1"
        ).fetchone()["n"]
        self.assertEqual(total, 30)  # capped

    def test_reminders_store_lifecycle(self):
        due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        rid = store.reminder_add(self.conn, 1, "call bank", due)
        self.assertEqual(len(store.reminders_active(self.conn, 1)), 1)
        due_rows = store.reminders_due(self.conn, datetime.now(timezone.utc).isoformat())
        self.assertEqual([r["id"] for r in due_rows], [rid])
        store.reminder_close(self.conn, rid, "done")
        self.assertEqual(store.reminders_active(self.conn, 1), [])
        self.assertEqual(store.reminder_get(self.conn, rid)["status"], "done")
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        rid2 = store.reminder_add(self.conn, 1, "weekly", future, "weekly")
        store.reminder_update_due(self.conn, rid2, future)
        self.assertEqual(store.reminder_get(self.conn, rid2)["due_utc"], future)

    def test_list_messages_filters(self):
        def add(msg_id, text, category, status):
            self.conn.execute(
                "INSERT INTO messages (chat_id, tg_message_id, received_at, raw_text,"
                " suggested_category, category, status) VALUES (1, ?, 'ts', ?, ?, ?, ?)",
                (msg_id, text, category, category if status == "confirmed" else None, status),
            )
            self.conn.commit()
        add(1, "DeepSeek released v4", "Крипта", "confirmed")
        add(2, "новости про биткоин", "крипта", "suggested")
        add(3, "pasta recipe", "food", "confirmed")
        add(4, "ignored", "x", "duplicate")
        # category filter is Cyrillic-case-insensitive
        rows = store.list_messages(self.conn, category="КРИПТА")
        self.assertEqual([r["tg_message_id"] for r in rows], [2, 1])
        # substring query searches text+summary+category+source
        rows = store.list_messages(self.conn, query="deepseek")
        self.assertEqual([r["tg_message_id"] for r in rows], [1])
        # duplicates excluded; newest first; limit respected
        rows = store.list_messages(self.conn, limit=2)
        self.assertEqual([r["tg_message_id"] for r in rows], [3, 2])

    def test_router_accepts_introspection_actions(self):
        for action in ("help", "overview", "list_items"):
            ok = router.validate_route({"action": action, "params": {}}, False)
            self.assertEqual(ok["action"], action)

    def test_issue_tracking(self):
        store.issue_add(self.conn, 1, "out_of_scope", "напиши эссе")
        store.issue_add(self.conn, 1, "out_of_scope", "x" * 500)
        store.issue_add(self.conn, 1, "stt_failed", "HTTP 404")
        counts = {r["kind"]: r["n"] for r in store.issue_counts(self.conn, "2000-01-01")}
        self.assertEqual(counts, {"out_of_scope": 2, "stt_failed": 1})
        recent = store.issues_recent(self.conn, "2000-01-01", limit=2)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["kind"], "stt_failed")
        self.assertLessEqual(len(recent[1]["detail"]), 300)  # detail capped
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.assertEqual(store.issue_counts(self.conn, future), [])
        ok = router.validate_route({"action": "issues_report", "params": {"period": "week"}}, False)
        self.assertEqual(ok["action"], "issues_report")

    def test_feedback(self):
        store.feedback_add(self.conn, "ingest", "digest", "news", "крипта")
        rows = store.feedback_recent(self.conn, "ingest")
        self.assertEqual(rows[0]["corrected"], "крипта")
        self.assertEqual(store.feedback_recent(self.conn, "router"), [])


if __name__ == "__main__":
    unittest.main()
