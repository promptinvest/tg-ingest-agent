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

import common
import gcal
import llm
import reminders
import router
import spend
import store
import texts


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
