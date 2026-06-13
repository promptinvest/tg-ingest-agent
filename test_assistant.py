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

import boss_model
import common
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

        def fake_chat(c, conn, skill, messages, max_tokens=300, model=None):
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

        def fake_chat(c, conn, skill, messages, max_tokens=300, model=None):
            return next(outs)
        with mock.patch.object(llm, "chat", side_effect=fake_chat):
            out = llm.chat_profile(cfg, self.conn, "router", [], profile="router_fast")
        self.assertEqual(out, '{"ok": true}')  # fell through to JSON-clean fallback

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
        # low-confidence guesses are demoted to clarify
        with mock.patch.object(llm, "chat",
                               return_value='{"action": "reminder_create", "params": {}, "confidence": 0.3}'):
            decision = router.route(self.cfg, self.conn, 1, "что-то", None)
        self.assertEqual(decision["action"], "clarify")
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
        self.assertIn("#%d [Flight Deals]" % self.row_id, text)
        self.assertIn("🔗 https://vandrouki.ru/x/", text)

    def test_item_detail_by_id_query_and_fallback(self):
        detail = self.agent.item_detail_text("ru", {"id": self.row_id})
        self.assertIn("https://vandrouki.ru/x/", detail)
        self.assertIn("Источник: Vandrouki", detail)
        self.assertIn("Cheap June flights", detail)
        self.assertIn("Пост: https://t.me/vandrouki/777", detail)
        self.assertIn("Дата поста: ", detail)
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

    def test_show_media_uses_file_id(self):
        import tg_ingest_agent
        conn = self.agent.conn
        store.insert_image(conn, self.row_id, 5,
                           {"file_id": "FILEID123", "file_unique_id": "u1"}, None)
        with mock.patch.object(tg_ingest_agent, "tg_send_photo") as send:
            self.agent.do_show_media(1, "ru", {"id": self.row_id})
        send.assert_called_once()
        self.assertEqual(send.call_args[0][2], "FILEID123")  # re-sent by file_id, no upload
        # no photos -> friendly reply, no send
        with mock.patch.object(tg_ingest_agent, "tg_send_photo") as send2, \
                mock.patch.object(self.agent, "reply") as reply:
            other = store.insert_message(conn, {"chat_id": 1, "tg_message_id": 9,
                                                "received_at": "ts", "raw_text": "no pics"})
            self.agent.do_show_media(1, "ru", {"id": other})
            send2.assert_not_called()
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
        sid = boss_model.remember_explicit(self.conn, "health: peanut allergy", "personal_fact")
        self.assertEqual(store.boss_get(self.conn, sid)["sensitivity"], "sensitive")

    def test_persona_hint_only_includes_confirmed_normal(self):
        self.assertEqual(persona.boss_preference_hint(self.conn), "")  # nothing yet
        boss_model.remember_explicit(self.conn, "prefers short answers", "tone")
        boss_model.remember_explicit(self.conn, "salary is confidential", "personal_fact")  # sensitive
        hint = persona.boss_preference_hint(self.conn)
        self.assertIn("prefers short answers", hint)
        self.assertNotIn("salary", hint)  # sensitive excluded from prompt personalization

    def test_router_accepts_personality_actions(self):
        for action in ("self_query", "boss_query", "boss_memory_update", "style_update",
                       "trace_query"):
            ok = router.validate_route({"action": action, "params": {}}, False)
            self.assertEqual(ok["action"], action)
            self.assertTrue(skill_manifest.known(action))


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
