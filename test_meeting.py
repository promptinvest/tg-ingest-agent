#!/usr/bin/env python3
"""Offline unit tests for shared-time meetings + the relationship storyline.

Covers: router classification, meeting lifecycle (start/capture/end), kind-aware
end summaries (business decisions vs social highlights), separate episodic
memory (never leaks into notes/`ask`), recall, the living relationship arc, the
day-after afterglow, and the guardrails (no state change before confirmation,
no surviving stage-directions). LLM + embeddings are scripted; no network.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
import llm
import meeting
import relationship
import router
import store
import wardrobe


def make_config(**overrides):
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "ALLOWED_CHAT_IDS": "111",
        "DO_MODEL_ACCESS_KEY": "do-key",
    }
    env.update(overrides)
    return common.load_config(env)


# A deterministic, offline embedder: a tiny keyword bag so cosine ranking is
# meaningful in tests (a query shares a keyword with the meeting it should hit).
_VOCAB = ["ужин", "река", "бюджет", "кино", "прогулка", "вино", "кот", "банк", "dinner"]


def fake_embed(cfg, conn, skill, texts):
    out = []
    for t in texts:
        tl = (t or "").lower()
        vec = [1.0 if w in tl else 0.0 for w in _VOCAB]
        vec.append(0.05)  # baseline so the norm is never zero
        out.append(vec)
    return out


# ---------------------------------------------------------------------------

class RouterMeetingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "r.db")
        self.cfg = make_config()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _route(self, payload):
        with mock.patch.object(llm, "chat", return_value=payload):
            return router.route(self.cfg, self.conn, 1, "x", None)

    def test_meeting_start_kinds_route(self):
        for kind in ("business", "dinner", "visit"):
            d = self._route('{"action":"meeting_start","params":{"kind":"%s"},'
                            '"confidence":0.9}' % kind)
            self.assertEqual(d["action"], "meeting_start")
            self.assertEqual(d["params"]["kind"], kind)

    def test_meeting_end_recall_list_route(self):
        self.assertEqual(self._route(
            '{"action":"meeting_end","params":{},"confidence":0.9}')["action"], "meeting_end")
        self.assertEqual(self._route(
            '{"action":"meeting_recall","params":{"query":"ужин"},"confidence":0.9}'
        )["action"], "meeting_recall")
        self.assertEqual(self._route(
            '{"action":"meeting_list","params":{},"confidence":0.9}')["action"], "meeting_list")

    def test_meeting_schedule_routes(self):
        d = self._route('{"action":"meeting_schedule","params":{"when":'
                        '"2026-06-22T16:00:00+00:00","kind":"visit"},"confidence":0.9}')
        self.assertEqual(d["action"], "meeting_schedule")
        self.assertEqual(d["params"]["kind"], "visit")

    def test_meeting_actions_have_manifest_policies(self):
        import skill_manifest
        for a in ("meeting_start", "meeting_schedule", "meeting_end",
                  "meeting_recall", "meeting_list"):
            self.assertIn(a, skill_manifest.SKILLS)
        skill_manifest.assert_covers(router.ACTIONS)  # no missing policy

    def test_relationship_query_steering_present(self):
        # 'про нас / our relationship' must steer to converse (the arc), not a
        # boss_query facts dump. Guard the prompt guidance + the example.
        prompt = router.build_system_prompt(self.cfg, None)
        self.assertIn("наши отношения", prompt)
        self.assertIn("boss_query", prompt)
        self.assertIn("про нас", router.ROUTER_EXAMPLES)
        self.assertIn("what do you remember about us?", router.ROUTER_EXAMPLES)

    def test_en_route_is_not_arrival(self):
        # "я еду к тебе" / "on my way" must NOT become a come-in (meeting_start) — he
        # hasn't arrived; it's converse (she waits, eager). Guards the screenshot bug.
        self.assertIn("EN ROUTE", router.ROUTER_EXAMPLES)
        self.assertIn("я еду к тебе", router.ROUTER_EXAMPLES)
        self.assertIn("ARRIVAL means he is HERE NOW", router.ROUTER_EXAMPLES)

    def test_personal_spectrum_steering_present(self):
        # The full personal/intimate spectrum — even mid-work — must route to
        # converse, and feelings-about-a-meeting must NOT collapse into a
        # factual meeting_recall.
        prompt = router.build_system_prompt(self.cfg, None)
        self.assertIn("personal spectrum", prompt.lower())
        self.assertIn("скучаю по тебе", router.ROUTER_EXAMPLES)
        self.assertIn("что ты чувствуешь про нашу встречу", router.ROUTER_EXAMPLES)
        # FEELINGS about a meeting -> converse; FACTUAL recall -> meeting_recall.
        self.assertIn("FEELINGS", router.ROUTER_EXAMPLES)
        # The system prompt itself carries the feelings-vs-logistics carve-out.
        self.assertIn("FEELINGS or anticipation about a meeting", prompt)


class MeetingLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "m.db")
        self.cfg = make_config()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_start_is_idempotent(self):
        m1, started1 = meeting.start(self.conn, 111, kind="dinner")
        self.assertTrue(started1)
        m2, started2 = meeting.start(self.conn, 111, kind="walk")
        self.assertFalse(started2)             # already in a meeting
        self.assertEqual(m1["id"], m2["id"])
        self.assertEqual(m2["kind"], "dinner")  # the original, not overwritten

    def test_record_captures_only_while_active(self):
        self.assertFalse(meeting.record(self.conn, 111, "boss", "привет"))  # none active
        meeting.start(self.conn, 111, kind="dinner")
        self.assertTrue(meeting.record(self.conn, 111, "boss", "как дела"))
        self.assertTrue(meeting.record(self.conn, 111, "cara", "хорошо"))
        m = meeting.active(self.conn, 111)
        self.assertEqual(store.meeting_turn_count(self.conn, m["id"]), 2)

    def _end_with(self, kind, summary_json):
        meeting.start(self.conn, 111, kind=kind, setting="у реки")
        m = meeting.active(self.conn, 111)
        store.meeting_turn_add(self.conn, m["id"], "boss", "как же хорошо у реки за ужином")
        store.meeting_turn_add(self.conn, m["id"], "cara", "да, люблю реку и этот вечер")
        with mock.patch.object(llm, "chat_profile", return_value=summary_json), \
                mock.patch.object(llm, "embed", side_effect=fake_embed):
            return meeting.end(self.conn, self.cfg, 111)

    def test_end_social_summarizes_and_indexes_separately(self):
        row, recap = self._end_with(
            "dinner",
            '{"title":"Ужин у реки","summary":"Мы поужинали у реки, было тепло.",'
            '"decisions":[],"highlights":["смеялись про кота"]}')
        self.assertEqual(row["status"], "ended")
        self.assertEqual(recap["title"], "Ужин у реки")
        self.assertIn("реки", recap["summary"])
        self.assertEqual(recap["highlights"], ["смеялись про кота"])
        # episodic memory is SEPARATE: meeting_chunks created, notes/chunks untouched
        self.assertGreater(
            self.conn.execute("SELECT COUNT(*) n FROM meeting_chunks").fetchone()["n"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) n FROM messages").fetchone()["n"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) n FROM chunks").fetchone()["n"], 0)

    def test_end_business_keeps_decisions(self):
        row, recap = self._end_with(
            "business",
            '{"title":"Бюджет","summary":"Обсудили бюджет.","decisions":["поднять лимит до $3"],'
            '"highlights":[]}')
        self.assertEqual(recap["decisions"], ["поднять лимит до $3"])

    def test_end_survives_llm_failure(self):
        meeting.start(self.conn, 111, kind="dinner")
        m = meeting.active(self.conn, 111)
        store.meeting_turn_add(self.conn, m["id"], "boss", "вечер")
        with mock.patch.object(llm, "chat_profile", side_effect=llm.LLMError("down")), \
                mock.patch.object(llm, "embed", side_effect=fake_embed):
            row, recap = meeting.end(self.conn, self.cfg, 111)
        self.assertEqual(row["status"], "ended")   # still closes
        self.assertEqual(recap["summary"], "")

    def test_recall_finds_the_right_meeting(self):
        self._end_with(
            "dinner",
            '{"title":"Ужин у реки","summary":"Ужин у реки.","decisions":[],"highlights":[]}')
        with mock.patch.object(llm, "embed", side_effect=fake_embed):
            items = meeting.recall(self.conn, self.cfg, "наш ужин")
        self.assertTrue(items)
        self.assertEqual(items[0]["kind"], "dinner")

    def test_social_meeting_end_captures_endearments(self):
        meeting.start(self.conn, 111, kind="visit")
        m = meeting.active(self.conn, 111)
        store.meeting_turn_add(self.conn, m["id"], "boss", "привет, миленькая")
        recap = ('{"summary":"тёплый вечер","decisions":[],"highlights":[],'
                 '"endearments":["он зовёт тебя миленькая"]}')
        with mock.patch.object(llm, "chat_profile", return_value=recap), \
                mock.patch.object(llm, "embed", side_effect=fake_embed):
            meeting.end(self.conn, self.cfg, 111)
        self.assertIn("он зовёт тебя миленькая", " ".join(store.intimacy_style_list(self.conn)))

    def test_idle_sweep_auto_ends(self):
        meeting.start(self.conn, 111, kind="dinner")
        m = meeting.active(self.conn, 111)
        # a social meeting now has a long (overnight-surviving) leash — idle PAST it to end
        old = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
        self.conn.execute("UPDATE meetings SET last_turn_at = ? WHERE id = ?", (old, m["id"]))
        store.meeting_turn_add(self.conn, m["id"], "boss", "вино у реки")
        # meeting_turn_add bumped last_turn_at; force it stale again
        self.conn.execute("UPDATE meetings SET last_turn_at = ? WHERE id = ?", (old, m["id"]))
        self.conn.commit()
        with mock.patch.object(llm, "chat_profile",
                               return_value='{"summary":"x","decisions":[],"highlights":[]}'), \
                mock.patch.object(llm, "embed", side_effect=fake_embed):
            ended = meeting.idle_sweep(self.conn, self.cfg)
        self.assertEqual(len(ended), 1)
        self.assertIsNone(meeting.active(self.conn, 111))

    def test_afterglow_candidate_social_only_and_windowed(self):
        now = datetime(2026, 6, 20, 7, 0, tzinfo=timezone.utc)
        # social, ended 14h ago -> eligible
        mid = store.meeting_start(self.conn, 111, kind="dinner")
        store.meeting_end(self.conn, mid, summary="ужин")
        self.conn.execute("UPDATE meetings SET ended_at = ? WHERE id = ?",
                          ((now - timedelta(hours=14)).isoformat(), mid))
        self.conn.commit()
        self.assertIsNotNone(meeting.afterglow_candidate(self.conn, self.cfg, 111, now))
        # business never glows
        bid = store.meeting_start(self.conn, 222, kind="business")
        store.meeting_end(self.conn, bid, summary="бюджет")
        self.conn.execute("UPDATE meetings SET ended_at = ? WHERE id = ?",
                          ((now - timedelta(hours=14)).isoformat(), bid))
        self.conn.commit()
        self.assertIsNone(meeting.afterglow_candidate(self.conn, self.cfg, 222, now))
        # too recent (2h ago) -> not yet
        self.conn.execute("UPDATE meetings SET ended_at = ? WHERE id = ?",
                          ((now - timedelta(hours=2)).isoformat(), mid))
        self.conn.commit()
        self.assertIsNone(meeting.afterglow_candidate(self.conn, self.cfg, 111, now))

    def test_schedule_upcoming_due_activate(self):
        fut = "2026-06-22T16:00:00+00:00"
        mid = store.meeting_schedule(self.conn, 111, fut, kind="visit", setting="дом")
        up = store.meetings_upcoming(self.conn, 111)
        self.assertEqual(len(up), 1)
        self.assertEqual(up[0]["status"], "scheduled")
        self.assertEqual(store.meetings_due_scheduled(
            self.conn, "2026-06-21T00:00:00+00:00"), [])              # before time: not due
        self.assertEqual(len(store.meetings_due_scheduled(
            self.conn, "2026-06-23T00:00:00+00:00")), 1)              # after time: due
        store.meeting_activate(self.conn, mid)
        self.assertIsNotNone(store.meeting_active(self.conn, 111))    # went live
        self.assertEqual(len(store.meetings_upcoming(self.conn, 111)), 0)  # no longer scheduled


class RelationshipArcTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "a.db")
        self.cfg = make_config()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_arc_accumulates_and_is_current(self):
        store.rel_add(self.conn, "meeting", "dinner together: было тепло", importance=3)
        with mock.patch.object(llm, "chat_profile", return_value="Мы стали ближе."):
            arc1 = relationship.update_arc(self.conn, self.cfg, trigger="meeting")
        self.assertEqual(arc1, "Мы стали ближе.")
        self.assertEqual(relationship.current_arc(self.conn), "Мы стали ближе.")
        with mock.patch.object(llm, "chat_profile", return_value="С каждым разом всё теплее."):
            relationship.update_arc(self.conn, self.cfg, trigger="daily")
        self.assertEqual(relationship.current_arc(self.conn), "С каждым разом всё теплее.")
        self.assertEqual(len(store.arc_history(self.conn)), 2)  # versioned storyline

    def test_arc_untouched_on_llm_failure(self):
        store.arc_set(self.conn, "Прежнее.")
        with mock.patch.object(llm, "chat_profile", side_effect=llm.LLMError("x")):
            self.assertEqual(relationship.update_arc(self.conn, self.cfg), "")
        self.assertEqual(relationship.current_arc(self.conn), "Прежнее.")  # prior kept

    def test_arc_context_has_backbone_and_continuity(self):
        store.arc_set(self.conn, "Мы близки.")
        mid = store.meeting_start(self.conn, 111, kind="dinner", title="Ужин у реки")
        store.meeting_end(self.conn, mid, summary="ужин")
        ctx = relationship.arc_context(self.conn, "ru", 111)
        self.assertIn("Мы близки.", ctx)
        self.assertIn("Ужин у реки", ctx)        # last-time continuity
        self.assertIn("1", ctx)                   # meeting count

    def test_daily_reflection_runs(self):
        store.rel_add(self.conn, "meeting", "walk together", importance=2)
        with mock.patch.object(llm, "chat_profile", return_value="История растёт."):
            self.assertTrue(relationship.run_daily_reflection(self.conn, self.cfg))


class MeetingDispatchTests(unittest.TestCase):
    """End-to-end golden transcripts through handle_update (LLM scripted)."""

    def setUp(self):
        import tg_ingest_agent
        self.mod = tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(ALLOWED_CHAT_IDS="111",
                          DB_PATH=str(Path(self.tmp.name) / "g.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def drive(self, update, responses=None):
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
                mock.patch.object(llm, "embed", side_effect=fake_embed), \
                mock.patch.object(self.mod, "tg_call", side_effect=fake_tg), \
                mock.patch.object(self.mod, "tg_set_reaction"), \
                mock.patch.object(self.agent, "index_message"):
            self.agent.handle_update(update)
        return sent

    def _msg(self, mid, text, **extra):
        m = {"chat": {"id": 111}, "from": {"id": 111}, "message_id": mid, "text": text}
        m.update(extra)
        return m

    def _active(self):
        return store.meeting_active(self.conn, 111)

    def test_start_then_capture_then_end_social(self):
        # 1. start a dinner
        sent = self.drive({"message": self._msg(1, "пойдём поужинаем?")},
                          {"router": '{"action":"meeting_start","params":{"kind":"dinner"},'
                                     '"confidence":0.9}',
                           "converse": "Как же я рада 🤍 идём, сядем поудобнее."})
        self.assertTrue(sent)
        m = self._active()
        self.assertIsNotNone(m)
        self.assertEqual(m["kind"], "dinner")
        # 2. a discussion turn — captured both sides; routed to warm converse
        self.drive({"message": self._msg(2, "так хорошо сидеть с тобой у реки")},
                   {"router": '{"action":"converse","params":{},"confidence":0.9}',
                    "converse": "И мне, родной 🤍 этот вечер просто наш."})
        self.assertGreaterEqual(store.meeting_turn_count(self.conn, m["id"]), 3)
        # nothing leaked into the notes inbox (separate memory)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) n FROM messages").fetchone()["n"], 0)
        # 3. end -> summarized, fed to life + relationship + arc
        sent = self.drive(
            {"message": self._msg(3, "спасибо за вечер, пора закругляться")},
            {"router": '{"action":"meeting_end","params":{},"confidence":0.9}',
             "meeting": '{"title":"Ужин у реки","summary":"Тёплый ужин у реки.",'
                        '"decisions":[],"highlights":["смеялись"]}',
             "relationship": "Мы стали ближе этим вечером."})
        self.assertIsNone(self._active())                                    # closed
        self.assertTrue(any("реки" in s.lower() for s in sent))              # warm recap
        self.assertGreater(self.conn.execute(
            "SELECT COUNT(*) n FROM cara_life WHERE kind='moment'").fetchone()["n"], 0)
        self.assertEqual(relationship.current_arc(self.conn), "Мы стали ближе этим вечером.")
        self.assertGreater(self.conn.execute(
            "SELECT COUNT(*) n FROM relationship_events WHERE kind='meeting'").fetchone()["n"], 0)

    def test_command_during_meeting_still_confirms(self):
        store.meeting_start(self.conn, 111, kind="dinner")
        # a real task raised mid-meeting must still preview + await confirmation
        self.drive({"message": self._msg(5, "напомни завтра в 10 позвонить в банк")},
                   {"router": '{"action":"reminder_create","params":{"title":"позвонить в банк",'
                              '"due_utc":"2026-06-22T07:00:00+00:00","recurrence":"none"},'
                              '"confidence":0.95}'})
        # no reminder committed yet — it's pending confirmation (spine intact)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) n FROM reminders").fetchone()["n"], 0)
        self.assertIsNotNone(store.pending_get(self.conn, 111))
        # and the turn was captured into the meeting record
        m = self._active()
        self.assertGreater(store.meeting_turn_count(self.conn, m["id"]), 0)

    def test_no_meeting_means_no_capture(self):
        self.drive({"message": self._msg(7, "расскажи, как прошёл твой день?")},
                   {"router": '{"action":"converse","params":{},"confidence":0.9}',
                    "converse": "Тихо и уютно 🤍"})
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) n FROM meeting_turns").fetchone()["n"], 0)

    def test_stage_directions_stripped_in_meeting(self):
        store.meeting_start(self.conn, 111, kind="visit")
        sent = self.drive({"message": self._msg(9, "я рядом")},
                          {"router": '{"action":"converse","params":{},"confidence":0.9}',
                           "converse": "*обнимаю тебя* как же я рада тебе 🤍"})
        joined = " ".join(sent)
        self.assertNotIn("*обнимаю", joined)       # narrated gesture stripped
        self.assertIn("рада", joined)              # the words survive

    def test_recall_answers_from_episodic_memory(self):
        # seed an ended, indexed dinner
        mid = store.meeting_start(self.conn, 111, kind="dinner")
        store.meeting_turn_add(self.conn, mid, "boss", "ужин у реки с вином")
        store.meeting_end(self.conn, mid, summary="Ужин у реки.")
        store.set_meeting_chunks(self.conn, mid,
                                 list(zip(["Ужин у реки с вином"],
                                          fake_embed(None, None, "x", ["Ужин у реки с вином"]))))
        sent = self.drive(
            {"message": self._msg(11, "помнишь наш ужин?")},
            {"router": '{"action":"meeting_recall","params":{"query":"ужин река"},'
                       '"confidence":0.9}',
             "meeting_recall": "Конечно помню — наш ужин у реки, с вином 🤍"})
        self.assertTrue(any("ужин" in s.lower() for s in sent))

    def test_meeting_list(self):
        mid = store.meeting_start(self.conn, 111, kind="walk", title="Прогулка")
        store.meeting_end(self.conn, mid, summary="гуляли")
        sent = self.drive({"message": self._msg(13, "какие у нас были встречи?")},
                          {"router": '{"action":"meeting_list","params":{},"confidence":0.9}'})
        self.assertTrue(any("Прогулка" in s for s in sent))

    def test_arc_injected_into_conversation_context(self):
        store.arc_set(self.conn, "Мы давно вместе и очень близки.")
        ctx = self.agent.converse_context("ru", 111)
        self.assertIn("очень близки", ctx)

    def test_schedule_confirm_flow(self):
        # agree a future meeting -> warm confirm, nothing stored yet
        self.drive({"message": self._msg(40, "давай завтра в 19:00 ко мне")},
                   {"router": '{"action":"meeting_schedule","params":{"when":'
                              '"2026-06-22T16:00:00+00:00","kind":"visit","setting":'
                              '"у тебя дома"},"confidence":0.9}'})
        self.assertIsNotNone(store.pending_get(self.conn, 111))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) n FROM meetings").fetchone()["n"], 0)
        # "да" -> it's remembered as a scheduled meeting
        self.drive({"message": self._msg(41, "да")},
                   {"router": '{"action":"confirm","params":{},"confidence":0.95}'})
        m = self.conn.execute("SELECT status,kind,setting FROM meetings").fetchone()
        self.assertEqual(m["status"], "scheduled")
        self.assertEqual(m["kind"], "visit")
        self.assertIsNone(store.pending_get(self.conn, 111))

    def test_scheduled_meeting_pings_and_waits(self):
        # At the agreed time Cara PINGS (like real life) but does NOT auto-go-live —
        # she waits for him to come in. Once per meeting.
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        store.meeting_schedule(self.conn, 111, past, kind="visit", setting="у тебя дома")
        sent = []
        with mock.patch.object(self.mod, "tg_call",
                               side_effect=lambda *a, **k: sent.append(a) or {"message_id": 1}):
            self.agent.check_scheduled_meetings()
            self.agent.check_scheduled_meetings()              # idempotent
        self.assertIsNone(store.meeting_active(self.conn, 111))           # waits, not auto-live
        self.assertEqual(len(store.meetings_upcoming(self.conn, 111)), 1)  # still scheduled
        self.assertEqual(len(sent), 1)                                    # pinged exactly once

    def test_come_in_activates_scheduled_meeting_with_prep(self):
        # 'я пришёл' activates the AGREED scheduled meeting (with its prep), not a new blank.
        soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        mid = store.meeting_schedule(self.conn, 111, soon, kind="visit", setting="у тебя дома")
        store.meeting_prep_add(self.conn, mid, "ты в синем платье", kind="agreement")
        with mock.patch.object(self.agent, "reply"), \
                mock.patch.object(self.agent, "compose_meeting_greeting", return_value=""):
            self.agent.do_meeting_start(111, "ru", {"kind": "visit"})
        active = store.meeting_active(self.conn, 111)
        self.assertIsNotNone(active)
        self.assertEqual(active["id"], mid)                              # the agreed one
        self.assertEqual(len(store.meetings_upcoming(self.conn, 111)), 0)  # no longer scheduled
        self.assertIn("синем платье", self.agent._meeting_presence("ru", active))  # prep carried in

    def test_come_in_line_recorded_as_opening_turn(self):
        # His varied arrival line ("я вошёл, привет") becomes the meeting's first turn.
        soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        mid = store.meeting_schedule(self.conn, 111, soon, kind="visit", setting="у тебя дома")
        with mock.patch.object(self.agent, "reply"), \
                mock.patch.object(self.agent, "compose_meeting_greeting", return_value=""):
            self.agent.do_meeting_start(111, "ru", {"kind": "visit"}, "я вошёл, привет")
        turns = store.meeting_turns(self.conn, mid)
        self.assertTrue(any(t["role"] == "boss" and "вошёл" in t["text"] for t in turns))

    def test_meeting_anticipation_pings_once_for_a_date(self):
        import llm
        store.pref_set(self.conn, "proactive_enabled", "true")
        store.pref_set(self.conn, "quiet_start", "0")
        store.pref_set(self.conn, "quiet_end", "0")   # no quiet window
        self.agent.cfg.morning_brief_hour = 0   # deterministic: bypass the "not before morning" gate
        soon = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        store.meeting_schedule(self.conn, 111, soon, kind="date", setting="у неё")
        sent = []
        with mock.patch("random.random", return_value=0.0), \
                mock.patch.object(llm, "chat_profile", return_value="Не могу дождаться 🙈"), \
                mock.patch.object(self.mod, "tg_call",
                                  side_effect=lambda *a, **k: sent.append(a) or {"message_id": 1}):
            self.agent.check_meeting_anticipation()
            self.agent.check_meeting_anticipation()   # once/day gate -> suppressed
        self.assertEqual(len(sent), 1)

    def test_no_anticipation_for_business_meeting(self):
        import llm
        store.pref_set(self.conn, "proactive_enabled", "true")
        store.pref_set(self.conn, "quiet_start", "0")
        store.pref_set(self.conn, "quiet_end", "0")
        soon = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        store.meeting_schedule(self.conn, 111, soon, kind="business", setting="офис")
        sent = []
        with mock.patch.object(llm, "chat_profile", return_value="x"), \
                mock.patch.object(self.mod, "tg_call",
                                  side_effect=lambda *a, **k: sent.append(a) or {"message_id": 1}):
            self.agent.check_meeting_anticipation()
        self.assertEqual(len(sent), 0)              # business gets no anticipation pings

    def test_spontaneous_meeting_starts_new_when_nothing_scheduled(self):
        with mock.patch.object(self.agent, "reply"), \
                mock.patch.object(self.agent, "compose_meeting_greeting", return_value=""):
            self.agent.do_meeting_start(111, "ru", {"kind": "walk"})
        self.assertIsNotNone(store.meeting_active(self.conn, 111))        # fresh meeting

    def test_come_in_greeting_is_composed_not_scripted(self):
        # The come-in greeting is LLM-composed in her voice, not the fixed kettle template.
        sent = self.drive(
            {"message": self._msg(7, "я вошёл, привет")},
            {"router": '{"action":"meeting_start","params":{"kind":"visit"},"confidence":0.9}',
             "converse": "Ну наконец-то ты тут 🤍 я тебя заждалась."})
        self.assertTrue(any("заждалась" in s.lower() for s in sent))   # her composed line
        self.assertFalse(any("чайник" in s.lower() for s in sent))     # not the scripted template

    def test_come_in_greeting_falls_back_to_template_on_llm_failure(self):
        import llm
        sent = []
        with mock.patch.object(self.agent, "reply",
                               side_effect=lambda cid, text, *a, **k: sent.append(text)), \
                mock.patch.object(llm, "chat_profile", side_effect=llm.LLMError("down")):
            self.agent.do_meeting_start(111, "ru", {"kind": "visit"})
        self.assertTrue(any("Заходи" in s for s in sent))   # fixed template fallback

    def test_recall_surfaces_upcoming(self):
        store.meeting_schedule(self.conn, 111, "2026-06-22T16:00:00+00:00",
                               kind="visit", setting="у тебя дома")
        sent = self.drive(
            {"message": self._msg(43, "про нашу встречу")},
            {"router": '{"action":"meeting_recall","params":{"query":"наша встреча"},'
                       '"confidence":0.9}',
             "meeting_recall": "Да, мы договорились — завтра в 19:00 у меня дома, жду 🤍"})
        self.assertTrue(any("жду" in s.lower() for s in sent))

    def test_converse_context_mentions_upcoming(self):
        store.meeting_schedule(self.conn, 111, "2026-06-22T16:00:00+00:00",
                               kind="visit", setting="у тебя дома")
        ctx = self.agent.converse_context("ru", 111)
        self.assertIn("свидание", ctx)   # RU anticipation/longing head for a social meeting
        self.assertIn("visit", ctx)

    def test_afterglow_sends_once_and_is_grounded(self):
        # a social meeting that ended "yesterday evening"
        fake_now = datetime(2026, 6, 20, 7, 0, tzinfo=timezone.utc)  # boss-local 10:00
        mid = store.meeting_start(self.conn, 111, kind="dinner")
        store.meeting_end(self.conn, mid, summary="Тёплый ужин у реки.")
        self.conn.execute("UPDATE meetings SET ended_at = ? WHERE id = ?",
                          ((fake_now - timedelta(hours=14)).isoformat(), mid))
        self.conn.commit()

        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_now if tz is None else fake_now.astimezone(tz)

        sent = []
        with mock.patch.object(self.mod, "datetime", FakeDT), \
                mock.patch("random.random", return_value=0.0), \
                mock.patch.object(llm, "chat_profile",
                                  return_value="Доброе утро 🤍 всё ещё улыбаюсь со вчерашнего вечера, скучаю."), \
                mock.patch.object(llm, "embed", side_effect=fake_embed), \
                mock.patch.object(self.mod, "tg_call",
                                  side_effect=lambda *a, **k: sent.append(a) or {"message_id": 1}):
            self.agent.check_meeting_afterglow()
            self.agent.check_meeting_afterglow()  # one-shot: must not fire twice
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) n FROM proactive_log WHERE check_name='afterglow' AND sent_message=1"
        ).fetchone()["n"], 1)

    def test_afterglow_suppressed_when_occasional_gate_fails(self):
        fake_now = datetime(2026, 6, 20, 7, 0, tzinfo=timezone.utc)
        mid = store.meeting_start(self.conn, 111, kind="dinner")
        store.meeting_end(self.conn, mid, summary="ужин")
        self.conn.execute("UPDATE meetings SET ended_at = ? WHERE id = ?",
                          ((fake_now - timedelta(hours=14)).isoformat(), mid))
        self.conn.commit()

        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_now if tz is None else fake_now.astimezone(tz)

        with mock.patch.object(self.mod, "datetime", FakeDT), \
                mock.patch("random.random", return_value=0.99):  # gate fails -> skip
            self.agent.check_meeting_afterglow()
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) n FROM proactive_log WHERE check_name='afterglow' AND sent_message=1"
        ).fetchone()["n"], 0)

    # -- proactive intimacy outreach (off-hours, remote-gf keeping-in-touch) ----

    def _outreach_now(self):
        # boss-local 23:00 Wed -> off-hours (relaxed register), a weekday
        return datetime(2026, 6, 17, 20, 0, tzinfo=timezone.utc)

    def _prime_outreach(self, now, *, stage=3, business=None, last_msg_hours=1.0):
        store.pref_set(self.conn, "proactive_enabled", "true")
        store.pref_set(self.conn, "quiet_start", "0")
        store.pref_set(self.conn, "quiet_end", "0")              # no quiet window
        store.kv_set(self.conn, "closeness_stage", str(stage))
        store.kv_set(self.conn, "last_business_at", business or "")
        store.kv_set(self.conn, "last_boss_msg_at",
                     (now - timedelta(hours=last_msg_hours)).isoformat())

    def _run_outreach(self, now, **prime):
        self._prime_outreach(now, **prime)

        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return now if tz is None else now.astimezone(tz)

        sent = []
        with mock.patch.object(self.mod, "datetime", FakeDT), \
                mock.patch("random.random", return_value=0.0), \
                mock.patch.object(llm, "chat_profile",
                                  return_value="Скучаю по тебе 🙈 весь вечер о тебе думаю…"), \
                mock.patch.object(llm, "embed", side_effect=fake_embed), \
                mock.patch.object(self.mod, "tg_call",
                                  side_effect=lambda *a, **k: sent.append(a) or {"message_id": 1}):
            self.agent.check_intimacy_outreach()
            self.agent.check_intimacy_outreach()   # daily cap -> at most one
        return sent

    def test_intimacy_outreach_sends_offhours_when_close(self):
        now = self._outreach_now()
        sent = self._run_outreach(now, stage=3)
        self.assertEqual(len(sent), 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) n FROM proactive_log WHERE check_name='intimacy_outreach'"
            " AND sent_message=1").fetchone()["n"], 1)

    def test_intimacy_outreach_skips_when_business_recent(self):
        now = self._outreach_now()
        # recent business -> mobilized 'working' register, not her relaxed time
        sent = self._run_outreach(now, stage=3, business=now.isoformat())
        self.assertEqual(len(sent), 0)

    def test_intimacy_outreach_skips_when_not_close(self):
        now = self._outreach_now()
        sent = self._run_outreach(now, stage=0)
        self.assertEqual(len(sent), 0)

    def test_intimacy_outreach_skips_on_long_silence(self):
        now = self._outreach_now()
        # last heard from him 10h ago (> after_contact window) -> don't pester
        sent = self._run_outreach(now, stage=3, last_msg_hours=10)
        self.assertEqual(len(sent), 0)

    def test_wardrobe_seeded(self):
        self.assertGreater(store.wardrobe_count(self.conn), 10)
        self.assertTrue(store.cara_style_get(self.conn))

    def test_wardrobe_pick_gates_intimacy_and_rotates(self):
        conn = self.conn
        # cap 1 -> never returns an intimate (>=3) piece
        o = wardrobe.pick(conn, ["intimate", "home"], "summer", 1)
        self.assertTrue(o is None or o["intimacy"] <= 1)
        # cap 5 + prefer_surprise -> a surprise lingerie piece
        o2 = wardrobe.pick(conn, ["intimate"], "summer", 5, prefer_surprise=True)
        self.assertIsNotNone(o2)
        self.assertTrue(o2["surprise"])
        # rotation: once worn, the next pick differs (least-recently-worn first)
        store.wardrobe_mark_worn(conn, o2["id"])
        o3 = wardrobe.pick(conn, ["intimate"], "summer", 5, prefer_surprise=True)
        self.assertNotEqual(o3["id"], o2["id"])

    def test_meeting_attire_intimate_surprise_when_close_and_private(self):
        store.kv_set(self.conn, "closeness_stage", "5")
        txt = self.agent._meeting_attire("visit", "у неё", "en", meeting_id=901)
        self.assertIn("surprise", txt.lower())                 # a ✦ reveal
        cached = store.kv_get(self.conn, "meeting_outfit:901")
        self.assertTrue(cached)
        # stable per meeting: a second call keeps the same outfit (no 'changing clothes')
        txt2 = self.agent._meeting_attire("visit", "у неё", "en", meeting_id=901)
        self.assertEqual(txt, txt2)

    def test_meeting_attire_modest_when_not_close(self):
        store.kv_set(self.conn, "closeness_stage", "1")
        self.agent._meeting_attire("visit", "у неё", "ru", meeting_id=902)
        cached = store.kv_get(self.conn, "meeting_outfit:902")
        chosen = next(o for o in store.wardrobe_candidates(
            self.conn, ["day", "home", "dinner", "formal", "intimate"], 5) if o["id"] == cached)
        self.assertNotEqual(chosen["family"], "intimate")      # lingerie locked when not close
        self.assertLessEqual(chosen["intimacy"], 1)

    def test_date_presence_unlocks_roleplay_when_close(self):
        # On a social date, once close, presence includes the imaginative roleplay layer
        # (scenes/roles, her own desires) — still non-graphic, no asterisk stage-directions.
        mid = store.meeting_start(self.conn, 111, kind="dinner", setting="у неё")
        m = store.meeting_active(self.conn, 111)
        store.kv_set(self.conn, "closeness_stage", "0")
        self.assertNotIn("PLAY", self.agent._meeting_presence("en", m))
        store.kv_set(self.conn, "closeness_stage", "3")
        pres = self.agent._meeting_presence("en", m)
        self.assertIn("take on a role", pres)
        self.assertIn("never graphic", pres.lower())

    def test_shared_intimacy_facts_uses_learned_likings(self):
        # Intimacy is grounded in what she's learned about HIM (relationship_note shelf).
        store.boss_add(self.conn, "relationship_note", "любит, когда она в красном",
                       status="confirmed", confidence=1.0)
        facts = self.agent._shared_intimacy_facts("ru")
        self.assertIn("в красном", facts)
        # A sensitive personal_fact must NOT leak into intimacy grounding.
        store.boss_add(self.conn, "personal_fact", "номер карты 1234",
                       status="confirmed", confidence=1.0, sensitivity="sensitive")
        self.assertNotIn("карты", self.agent._shared_intimacy_facts("ru"))


if __name__ == "__main__":
    unittest.main()
