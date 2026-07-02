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

    def test_idle_sweep_absolute_cap_ends_continuously_active_meeting(self):
        # A forgotten-open meeting kept "fresh" by ongoing messages (last_turn_at always
        # recent) used to never end and froze reminders for days. The absolute cap ends it
        # once it is older than meeting_max_hours, no matter how recently it was active.
        self.cfg.meeting_max_hours = 24
        meeting.start(self.conn, 111, kind="visit")
        m = meeting.active(self.conn, 111)
        started = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()   # > 24h cap
        recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()   # just active
        self.conn.execute("UPDATE meetings SET started_at = ?, last_turn_at = ? WHERE id = ?",
                          (started, recent, m["id"]))
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

    def test_activate_refreshes_started_at_and_survives_age_cap(self):
        """A LATE come-in must not inherit the stale scheduled_for as its start:
        started_at was seeded with the agreed time, so the meeting_max_hours age
        cap auto-ended a late-activated date minutes after she welcomed him in,
        and the duration note claimed fabricated hours together."""
        past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        mid = store.meeting_schedule(self.conn, 111, past, kind="visit")
        self.assertEqual(store.meeting_get(self.conn, mid)["started_at"], past)
        m = meeting.activate(self.conn, mid)
        self.assertEqual(m["status"], "active")
        self.assertGreater(m["started_at"], past)    # refreshed to the real start
        self.assertEqual(m["scheduled_for"], past)   # the agreed time is preserved
        ended = meeting.idle_sweep(self.conn, self.cfg)  # age cap no longer kills it
        self.assertEqual(ended, [])
        self.assertIsNotNone(store.meeting_active(self.conn, 111))

    def test_stale_scheduled_meetings_expire(self):
        """A plan he never came to lapses (status 'cancelled') instead of
        lingering in 'upcoming' and hijacking a later meeting_start."""
        now = datetime.now(timezone.utc)
        stale = store.meeting_schedule(
            self.conn, 111, (now - timedelta(hours=30)).isoformat(), kind="visit")
        fresh = store.meeting_schedule(
            self.conn, 111, (now + timedelta(hours=3)).isoformat(), kind="dinner")
        n = store.meetings_expire_scheduled(
            self.conn, (now - timedelta(hours=24)).isoformat())
        self.assertEqual(n, 1)
        self.assertEqual([r["id"] for r in store.meetings_upcoming(self.conn, 111)],
                         [fresh])
        self.assertEqual(store.meeting_get(self.conn, stale)["status"], "cancelled")


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

    def test_arc_folds_recent_meeting_turns(self):
        # A meeting's verbatim dialogue must reach the storyline even with no written summary —
        # otherwise a long/just-ended meeting goes blind and the arc just echoes the prior one.
        meeting.start(self.conn, 111, kind="visit")
        m = meeting.active(self.conn, 111)
        store.meeting_turn_add(self.conn, m["id"], "boss", "мы решили лететь в Рим осенью")
        captured = {}

        def cp(cfg, conn, skill, messages, **k):
            captured["user"] = messages[1]["content"]
            return "Мы планируем Рим вместе. CLOSENESS: 4"
        with mock.patch.object(llm, "chat_profile", side_effect=cp):
            relationship.update_arc(self.conn, self.cfg, trigger="daily")
        self.assertIn("лететь в Рим", captured["user"])   # verbatim meeting turn fed to the arc

    def test_closeness_ratchet_up_is_audited(self):
        # The stage is LLM-authored and gates intimate behavior, so a jump is recorded
        # (with the evidence) — inspectable, and reversible via closeness_set.
        store.arc_set(self.conn, "Мы вместе.")   # prior content so update_arc runs the LLM pass
        store.kv_set(self.conn, "closeness_stage", "2")
        with mock.patch.object(llm, "chat_profile", return_value="Ближе.\nCLOSENESS: 4"):
            relationship.update_arc(self.conn, self.cfg, trigger="daily")
        self.assertEqual(store.kv_get(self.conn, "closeness_stage"), "4")   # ratcheted up
        events = store.rel_recent(self.conn, "2000-01-01T00:00:00+00:00", limit=10)
        self.assertTrue(any("closeness 2→4" in (e["summary"] or "") for e in events))

    def test_cool_arc_rewrites_narrative_down(self):
        # No prior arc -> nothing to cool, no LLM call.
        with mock.patch.object(llm, "chat_profile",
                               side_effect=AssertionError("should not call LLM")):
            self.assertEqual(relationship.cool_arc(self.conn, self.cfg, 2), "")
        # The one path allowed to LOWER the arc prose (owner reset) — bypasses "only deepens".
        store.arc_set(self.conn, "Мы очень близки, почти любовники.")
        captured = {}

        def cp(cfg, conn, skill, messages, **k):
            captured["sys"] = messages[0]["content"]
            return "Мы добрые друзья, тепло, но без близости."
        with mock.patch.object(llm, "chat_profile", side_effect=cp):
            out = relationship.cool_arc(self.conn, self.cfg, 2)
        self.assertEqual(out, "Мы добрые друзья, тепло, но без близости.")
        self.assertEqual(relationship.current_arc(self.conn), out)   # stored
        self.assertIn("level 2/5", captured["sys"])                  # target level in the prompt

    def test_arc_context_stage_line_is_ceiling_aware(self):
        store.arc_set(self.conn, "Мы близки.")
        store.kv_set(self.conn, "closeness_stage", "4")
        # no ceiling (organic) -> "only deepens"
        ctx = relationship.arc_context(self.conn, "en")
        self.assertIn("only deepens", ctx.lower())
        # owner capped (ceiling < 5) -> hold-here framing, NOT "only deepens"
        store.kv_set(self.conn, "closeness_ceiling", "2")
        store.kv_set(self.conn, "closeness_stage", "2")
        ctx2 = relationship.arc_context(self.conn, "en")
        self.assertNotIn("only deepens", ctx2.lower())
        self.assertIn("settled for now", ctx2.lower())

    def test_closeness_ratchet_no_audit_when_unchanged(self):
        # A lower evidenced stage never drops it (ratchet) and logs nothing (no noise).
        store.arc_set(self.conn, "Мы вместе.")   # prior content so update_arc runs the LLM pass
        store.kv_set(self.conn, "closeness_stage", "5")
        with mock.patch.object(llm, "chat_profile", return_value="Тепло.\nCLOSENESS: 3"):
            relationship.update_arc(self.conn, self.cfg, trigger="daily")
        self.assertEqual(store.kv_get(self.conn, "closeness_stage"), "5")   # never drops
        events = store.rel_recent(self.conn, "2000-01-01T00:00:00+00:00", limit=10)
        self.assertFalse(any("closeness" in (e["summary"] or "") for e in events))

    def test_meeting_promise_becomes_agreement(self):
        # A promise made during a meeting is captured as a (passive) agreement, closing the gap
        # where meeting commitments slipped out of memory.
        meeting.start(self.conn, 111, kind="visit")
        meeting.active(self.conn, 111)
        store.meeting_turn_add(self.conn, meeting.active(self.conn, 111)["id"], "boss",
                               "обещаю свозить тебя к морю летом")
        with mock.patch.object(
                llm, "chat_profile",
                return_value=('{"summary":"тёплый вечер","decisions":[],"highlights":[],'
                              '"promises":["свозить её к морю летом"]}')), \
                mock.patch.object(llm, "embed", side_effect=fake_embed):
            meeting.end(self.conn, self.cfg, 111)
        self.assertTrue(any("к морю" in a["text"] for a in store.agreements_open(self.conn, 111)))

    def test_resummarize_recovers_unsummarized_meeting(self):
        # A meeting whose recap LLM failed at end (empty summary) is recovered by the sweep:
        # summary written, folded into the arc, and no longer pending.
        meeting.start(self.conn, 111, kind="dinner")
        m = meeting.active(self.conn, 111)
        store.meeting_turn_add(self.conn, m["id"], "boss", "вино у реки, было волшебно")
        store.meeting_end(self.conn, m["id"], summary=None, decisions="[]")  # recap failed
        store.meeting_bump_summary_try(self.conn, m["id"])
        self.assertEqual(len(store.meetings_unsummarized(self.conn)), 1)
        with mock.patch.object(
                llm, "chat_profile",
                return_value='{"summary":"ужин у реки","decisions":[],"highlights":[]}'), \
                mock.patch.object(llm, "embed", side_effect=fake_embed):
            ok = meeting.resummarize(self.conn, self.cfg, m["id"])
        self.assertTrue(ok)
        self.assertEqual(store.meeting_get(self.conn, m["id"])["summary"], "ужин у реки")
        self.assertEqual(len(store.meetings_unsummarized(self.conn)), 0)   # no longer pending
        self.assertTrue(relationship.current_arc(self.conn))               # folded into the arc


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
            if skill == "scene" and skill not in responses:
                return "{}"   # incidental scene-state refresh during a date: keep state as-is
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

    def test_narration_kept_during_meeting(self):
        # On a live date narration is immersive roleplay he's part of — it is NOT stripped
        # (the words/emojis-only texting voice applies only OUTSIDE a meeting).
        store.meeting_start(self.conn, 111, kind="visit")
        sent = self.drive({"message": self._msg(9, "я рядом")},
                          {"router": '{"action":"converse","params":{},"confidence":0.9}',
                           "converse": "*обнимаю тебя* как же я рада тебе 🤍"})
        joined = " ".join(sent)
        self.assertIn("обнимаю", joined)           # narration survives during a date
        self.assertIn("рада", joined)

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

    def test_arrival_kind_visit_still_activates_scheduled_dinner(self):
        # The router tags EVERY arrival line ("я у двери", "ну вот и я") as
        # kind='visit'. That must still activate the agreed dinner/walk — kind
        # compatibility is by REGISTER (social vs business), not exact equality,
        # else every non-visit social plan is orphaned by the come-in.
        soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        mid = store.meeting_schedule(self.conn, 111, soon, kind="dinner", setting="ресторан")
        store.meeting_prep_add(self.conn, mid, "столик у окна", kind="agreement")
        with mock.patch.object(self.agent, "reply"), \
                mock.patch.object(self.agent, "compose_meeting_greeting", return_value=""):
            self.agent.do_meeting_start(111, "ru", {"kind": "visit"}, "ну вот и я")
        active = store.meeting_active(self.conn, 111)
        self.assertEqual(active["id"], mid)              # the agreed dinner, not a blank visit
        self.assertEqual(active["kind"], "dinner")
        # a generic plan (kind='other') is likewise activated by any arrival
        store.meeting_end(self.conn, mid, summary="x")
        soon2 = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        gid = store.meeting_schedule(self.conn, 111, soon2, kind="other")
        with mock.patch.object(self.agent, "reply"), \
                mock.patch.object(self.agent, "compose_meeting_greeting", return_value=""):
            self.agent.do_meeting_start(111, "ru", {"kind": "visit"})
        self.assertEqual(store.meeting_active(self.conn, 111)["id"], gid)

    def test_explicit_different_kind_does_not_hijack_scheduled_date(self):
        # A date is on the books for tonight; an explicit business sit-down NOW must
        # start its own meeting, not consume the date five hours early in the wrong
        # register (leaving the agreed date row spent).
        tonight = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        did = store.meeting_schedule(self.conn, 111, tonight, kind="visit", setting="у неё")
        with mock.patch.object(self.agent, "reply"), \
                mock.patch.object(self.agent, "compose_meeting_greeting", return_value=""):
            self.agent.do_meeting_start(111, "ru", {"kind": "business"})
        active = store.meeting_active(self.conn, 111)
        self.assertEqual(active["kind"], "business")     # a fresh business meeting
        self.assertNotEqual(active["id"], did)
        # the date is still scheduled for tonight, untouched
        self.assertEqual([r["id"] for r in store.meetings_upcoming(self.conn, 111)], [did])

    def test_stale_scheduled_meeting_does_not_hijack_new_start(self):
        # A days-old plan he never came to must NOT be what a fresh "давай встретимся"
        # activates — _scheduled_now is bounded, so a blank meeting starts instead.
        stale = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        old = store.meeting_schedule(self.conn, 111, stale, kind="visit")
        with mock.patch.object(self.agent, "reply"), \
                mock.patch.object(self.agent, "compose_meeting_greeting", return_value=""):
            self.agent.do_meeting_start(111, "ru", {"kind": "other"})
        active = store.meeting_active(self.conn, 111)
        self.assertNotEqual(active["id"], old)   # not the stale plan

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

    def _prime_outreach(self, now, *, stage=4, business=None, last_msg_hours=1.0):
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
        sent = self._run_outreach(now, stage=4)
        self.assertEqual(len(sent), 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) n FROM proactive_log WHERE check_name='intimacy_outreach'"
            " AND sent_message=1").fetchone()["n"], 1)

    def test_intimacy_outreach_skips_when_business_recent(self):
        now = self._outreach_now()
        # recent business -> mobilized 'working' register, not her relaxed time
        sent = self._run_outreach(now, stage=4, business=now.isoformat())
        self.assertEqual(len(sent), 0)

    def test_intimacy_outreach_skips_when_not_close(self):
        now = self._outreach_now()
        sent = self._run_outreach(now, stage=0)
        self.assertEqual(len(sent), 0)

    def test_intimacy_outreach_skips_on_long_silence(self):
        now = self._outreach_now()
        # last heard from him 10h ago (> after_contact window) -> don't pester
        sent = self._run_outreach(now, stage=4, last_msg_hours=10)
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

    def test_outfit_anticipation_tease_and_continuity(self):
        # "Что наденешь?" — she has a piece in mind for the upcoming date, teases it (not
        # reveal), and wears exactly that when the date goes live.
        store.kv_set(self.conn, "closeness_stage", "5")
        mid = store.meeting_schedule(self.conn, 111, "2026-07-01T16:00:00+00:00",
                                     kind="visit", setting="у неё")
        ctx = self.agent.converse_context("ru", 111)
        self.assertIn("присмотрела", ctx)                       # the tease directive is present
        planned = store.kv_get(self.conn, f"planned_outfit:{mid}")
        self.assertTrue(planned)                                # an outfit is planned + cached
        # asking again keeps the SAME planned piece (consistent tease)
        self.agent.converse_context("ru", 111)
        self.assertEqual(store.kv_get(self.conn, f"planned_outfit:{mid}"), planned)
        # go live -> she wears exactly what she teased (continuity)
        store.meeting_activate(self.conn, mid)
        self.agent._meeting_attire("visit", "у неё", "ru", meeting_id=mid)
        self.assertEqual(store.kv_get(self.conn, f"meeting_outfit:{mid}"), planned)

    def test_outfit_question_routes_to_converse(self):
        self.assertIn("что наденешь", router.ROUTER_EXAMPLES)

    def test_wardrobe_add_via_chat(self):
        before = store.wardrobe_count(self.conn)
        sent = self.drive(
            {"message": self._msg(60, "добавь себе в гардероб бордовое кружевное бельё")},
            {"router": '{"action":"wardrobe_add","params":{"description":'
                       '"бордовое кружевное бельё"},"confidence":0.9}'})
        self.assertEqual(store.wardrobe_count(self.conn), before + 1)
        o = store.wardrobe_get(self.conn, "user_" + wardrobe._slug("бордовое кружевное бельё"))
        self.assertIsNotNone(o)
        self.assertEqual(o["family"], "intimate")        # 'бельё/кружев' -> intimate tier
        self.assertIn("burgundy", o["colors"])
        self.assertTrue(any("гардероб" in s.lower() for s in sent))

    def test_outfit_preference_learned_and_biases_taste(self):
        self.drive(
            {"message": self._msg(61, "тебе идёт изумрудное")},
            {"router": '{"action":"outfit_preference","params":{"detail":'
                       '"ему нравится она в изумрудном (emerald)"},"confidence":0.9}'})
        self.assertIn("emerald", self.agent._taste_colors())   # taste now biases picks

    def test_wardrobe_show(self):
        sent = self.drive(
            {"message": self._msg(62, "покажи свой гардероб")},
            {"router": '{"action":"wardrobe_show","params":{},"confidence":0.9}'})
        self.assertTrue(any(("повседневное" in s.lower() or "daywear" in s.lower()) for s in sent))

    def test_anticipation_hints_at_planned_outfit(self):
        import llm
        store.kv_set(self.conn, "closeness_stage", "5")
        store.meeting_schedule(self.conn, 111, "2026-07-01T16:00:00+00:00",
                               kind="visit", setting="у неё")
        captured = {}

        def cap(cfg, conn, skill, messages, **kw):
            captured["msgs"] = messages
            return "Жду вечера 🙈"
        with mock.patch.object(llm, "chat_profile", side_effect=cap), \
                mock.patch.object(llm, "embed", side_effect=fake_embed):
            self.agent.compose_anticipation("ru", store.meetings_upcoming(self.conn, 111)[0])
        self.assertIn("присмотрела, что наденешь", captured["msgs"][1]["content"])

    def test_date_presence_unlocks_roleplay_only_at_intimate_stage(self):
        # The imaginative sexual-roleplay layer gates at the INTIMATE tier (stage 4) — so a
        # reset to 2/3 keeps it locked. Physical-continuity (non-sexual scene tracking) is
        # independent and present once a date is open.
        mid = store.meeting_start(self.conn, 111, kind="dinner", setting="у неё")
        m = store.meeting_active(self.conn, 111)
        store.kv_set(self.conn, "closeness_stage", "3")            # below the intimate gate
        self.assertNotIn("take on a role", self.agent._meeting_presence("en", m))
        store.kv_set(self.conn, "closeness_stage", "4")            # intimate tier
        pres = self.agent._meeting_presence("en", m)
        self.assertIn("take on a role", pres)
        self.assertIn("PHYSICAL CONTINUITY", pres)

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

    # -- Phase 3: closeness owner-control + surface-once agreements ------------

    def test_closeness_set_routes_and_has_policy(self):
        import skill_manifest
        self.assertEqual(router.validate_route(
            {"action": "closeness_set", "params": {"stage": 3}}, False)["action"], "closeness_set")
        self.assertTrue(skill_manifest.known("closeness_set"))
        skill_manifest.assert_covers(router.ACTIONS)  # every action still has a policy

    def test_closeness_set_can_lower_and_is_audited(self):
        # The owner override is the ONE way to LOWER closeness (the arc only ratchets up),
        # so a hallucinated jump is correctable; every set is audited.
        store.kv_set(self.conn, "closeness_stage", "5")
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_closeness_set(111, "ru", {"stage": 2})
        self.assertEqual(store.kv_get(self.conn, "closeness_stage"), "2")   # lowered
        events = store.rel_recent(self.conn, "2000-01-01T00:00:00+00:00", limit=5)
        self.assertTrue(any("set by owner: 5→2" in (e["summary"] or "") for e in events))
        # out-of-range clamps to 1..5; a non-numeric stage changes nothing
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_closeness_set(111, "ru", {"stage": 9})
        self.assertEqual(store.kv_get(self.conn, "closeness_stage"), "5")   # clamped
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_closeness_set(111, "ru", {"stage": "abc"})
        self.assertEqual(store.kv_get(self.conn, "closeness_stage"), "5")   # unchanged

    def test_closeness_set_cools_the_arc_only_on_lower(self):
        # Lowering also rewrites the arc NARRATIVE down (so the reset reaches her tone, not
        # just the number); a raise leaves the arc to grow organically.
        store.arc_set(self.conn, "Мы почти любовники, очень близки.")
        store.kv_set(self.conn, "closeness_stage", "5")
        calls = []

        def cp(cfg, conn, skill, messages, **k):
            calls.append(skill)
            return "Мы просто добрые друзья."
        with mock.patch.object(self.agent, "reply"), \
                mock.patch.object(llm, "chat_profile", side_effect=cp):
            self.agent.do_closeness_set(111, "ru", {"stage": 2})   # lower -> cool
        self.assertEqual(relationship.current_arc(self.conn), "Мы просто добрые друзья.")
        self.assertEqual(calls, ["relationship"])                  # cool_arc ran once
        # a RAISE does not cool the arc
        calls.clear()
        with mock.patch.object(self.agent, "reply"), \
                mock.patch.object(llm, "chat_profile", side_effect=cp):
            self.agent.do_closeness_set(111, "ru", {"stage": 4})   # raise
        self.assertEqual(calls, [])                                # no cool_arc

    def test_intimacy_outreach_blocked_below_intimate_gate(self):
        # The proactive craving-outreach now gates at the intimate tier (stage 4); a reset
        # to 2/3 keeps it off.
        now = self._outreach_now()
        self.assertEqual(len(self._run_outreach(now, stage=3)), 0)

    def test_agreements_surfaced_once_then_correctable(self):
        # Auto-captured (surfaced=0) agreements are shown ONCE for a "did we really agree
        # this?" check, marked surfaced, then a bare denial cancels exactly those.
        store.agreement_add(self.conn, 111, "ты подаришь ей цветы", source="meeting", surfaced=0)
        store.agreement_add(self.conn, 111, "она приготовит ужин", source="conversation", surfaced=0)
        # an explicitly-stated one is already surfaced and must NOT be swept up
        keep = store.agreement_add(self.conn, 111, "едем к морю летом", source="explicit")
        block, ids = self.agent._pending_surface_agreements(111, "ru")
        self.assertIn("цветы", block)
        self.assertIn("ужин", block)
        self.assertNotIn("морю", block)                             # explicit one not surfaced
        # NOT marked until the send is confirmed (delivery-safe surface-once)
        self.assertEqual(len(store.agreements_unsurfaced(self.conn, 111)), 2)
        self.agent._commit_surfaced(ids)
        self.assertEqual(store.agreements_unsurfaced(self.conn, 111), [])  # now marked
        self.assertEqual(self.agent._pending_surface_agreements(111, "ru"), ("", []))  # surface-once
        # an unsurfaced agreement is NOT honored in context until shown; explicit one is
        honored = store.agreements_open(self.conn, 111, surfaced_only=True)
        self.assertTrue(all(a["status"] == "open" for a in honored))
        # a fully bare "не договаривались" right after cancels exactly the surfaced pair
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_agreement_close(111, "ru", {"surfaced": True}, "не договаривались")
        open_texts = [a["text"] for a in store.agreements_open(self.conn, 111)]
        self.assertEqual(open_texts, ["едем к морю летом"])         # only the explicit one remains
        self.assertEqual(store.agreement_get(self.conn, keep)["status"], "open")

    def test_surfaced_denial_targets_only_the_named_one(self):
        # "про море не договаривались, а отчёт да" must cancel ONLY море — never nuke the
        # отчёт he reaffirmed in the same breath (the data-loss path the review flagged).
        a_sea = store.agreement_add(self.conn, 111, "поездка на море летом",
                                    source="meeting", surfaced=0)
        a_rep = store.agreement_add(self.conn, 111, "прислать отчёт к пятнице",
                                    source="meeting", surfaced=0)
        _, ids = self.agent._pending_surface_agreements(111, "ru")
        self.agent._commit_surfaced(ids)
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_agreement_close(
                111, "ru", {"surfaced": True, "query": "море"}, "про море не договаривались, а отчёт да")
        self.assertEqual(store.agreement_get(self.conn, a_sea)["status"], "cancelled")
        self.assertEqual(store.agreement_get(self.conn, a_rep)["status"], "open")   # reaffirmed, kept
        # отчёт stays addressable for a follow-up denial within the window
        self.assertIn(str(a_rep), store.kv_get(self.conn, "agreements_surfaced_ids"))
        # a named subject NOT in the surfaced set cancels nothing
        with mock.patch.object(self.agent, "reply"):
            self.agent.do_agreement_close(111, "ru", {"surfaced": True, "query": "кино"}, "про кино нет")
        self.assertEqual(store.agreement_get(self.conn, a_rep)["status"], "open")   # untouched

    def test_closeness_owner_ceiling_survives_arc_reinflation(self):
        # After a manual lower, the STALE arc re-emitting a high CLOSENESS must NOT
        # re-inflate the stage past the owner-set ceiling (the reset must stick).
        import relationship
        store.arc_set(self.conn, "Мы очень близки.")
        store.kv_set(self.conn, "closeness_stage", "5")
        # do_closeness_set(stage<prior) now calls cool_arc -> mock chat_profile so the unit
        # test makes no real network call.
        with mock.patch.object(self.agent, "reply"), \
                mock.patch.object(llm, "chat_profile", return_value="Мы просто друзья."):
            self.agent.do_closeness_set(111, "ru", {"stage": 2})   # stage=2, ceiling=2
        captured = {}

        def cp(cfg, conn, skill, messages, **k):
            captured["sys"] = messages[0]["content"]
            return "Всё так же близки.\nCLOSENESS: 5"
        with mock.patch.object(llm, "chat_profile", side_effect=cp), \
                mock.patch.object(llm, "embed", side_effect=fake_embed):
            relationship.update_arc(self.conn, self.agent.cfg, trigger="daily")
        self.assertEqual(store.kv_get(self.conn, "closeness_stage"), "2")   # numeric ceiling held
        # AND the prose is capped: update_arc's system prompt carries the OWNER CAP directive,
        # so the daily/meeting arc can't re-warm the narrative past the cap.
        self.assertIn("OWNER CAP", captured["sys"])
        self.assertIn("2/5", captured["sys"])

    def test_meeting_end_surfaces_agreements_after_recap(self):
        # End-to-end: a meeting recap promise is surfaced — as its OWN message right after the
        # recap (separate reply, so the recap can't truncate it and still commit it).
        meeting.start(self.conn, 111, kind="visit")
        store.meeting_turn_add(self.conn, meeting.active(self.conn, 111)["id"], "boss",
                               "договорились, ты придёшь в субботу")
        sent = self.drive(
            {"message": self._msg(80, "давай закончим")},
            {"router": '{"action":"meeting_end","params":{},"confidence":0.9}',
             "meeting": ('{"summary":"тёплый вечер","decisions":[],"highlights":[],'
                         '"promises":["ты придёшь в субботу"]}'),
             "relationship": "Нам было хорошо вместе."})
        self.assertTrue(any("в субботу" in s for s in sent))       # surfaced (its own message)
        self.assertTrue(store.kv_get(self.conn, "agreements_surfaced_at"))


class SceneModuleTests(unittest.TestCase):
    """Pure scene-state helpers: change detection, merge-on-update, rendering."""

    def test_likely_change_detects_movement_items_people(self):
        import scene
        for t in ("ты ложишься на живот", "перейдём на кровать", "иди ко мне",
                  "сними чулки", "lie down on your back", "let's move to the couch",
                  "достаю вибратор", "I take out a toy", "Лера приходит к нам"):
            self.assertTrue(scene.likely_change(t), t)
        for t in ("как же хорошо с тобой", "я так тебя люблю", "ты счастлив?"):
            self.assertFalse(scene.likely_change(t), t)

    def test_parse_update_carries_unchanged_forward(self):
        import scene
        current = {"location": "спальня", "her_posture": "на спине", "his_position": "",
                   "her_clothing": ["платье", "чулки"], "removed_clothing": [],
                   "items_in_play": ["вибратор"], "people_present": [], "other_facts": []}
        # only the posture changes; clothing and items-in-play must persist verbatim
        new = scene.parse_update('{"her_posture": "на животе, подушка под бёдрами"}', current)
        self.assertEqual(new["her_posture"], "на животе, подушка под бёдрами")
        self.assertEqual(new["location"], "спальня")              # carried forward
        self.assertEqual(new["her_clothing"], ["платье", "чулки"])  # not changed on its own
        self.assertEqual(new["items_in_play"], ["вибратор"])       # item not forgotten

    def test_configuration_and_accessibility_tracked(self):
        import scene
        # an established arrangement with body-part accessibility, and it carries forward
        current = {"location": "спальня", "her_posture": "на спине", "his_position": "сверху",
                   "configuration": "Кара на спине, Олег сверху между её бёдер, её запястья прижаты",
                   "accessibility": "её руки прижаты/заняты; свободны рот и ноги",
                   "contact_map": ["его правая рука — держит её запястья над головой", "её рот — свободен"],
                   "her_clothing": [], "removed_clothing": [], "items_in_play": [],
                   "people_present": [], "other_facts": []}
        kept = scene.parse_update('{"her_posture": "на спине"}', current)   # nothing about arrangement
        self.assertIn("прижаты", kept["configuration"])      # arrangement carried forward
        self.assertIn("руки прижаты", kept["accessibility"])  # accessibility carried forward
        self.assertEqual(kept["contact_map"], current["contact_map"])   # per-part occupancy carried forward
        block = scene.render(current, "ru")
        self.assertIn("Расположение тел", block)
        self.assertIn("Занятость по частям", block)          # the per-part contact map renders
        self.assertIn("держит её запястья", block)
        self.assertIn("СЧИТАЙСЯ С ДОСТУПНОСТЬЮ", block)      # the reach/occlusion constraint
        self.assertIn("недосягаема", block)

    def test_parse_update_moves_clothing_and_adds_item(self):
        import scene
        current = {"location": "спальня", "her_clothing": ["платье", "чулки"],
                   "removed_clothing": [], "items_in_play": [], "people_present": [],
                   "her_posture": "", "his_position": "", "other_facts": []}
        new = scene.parse_update(
            '{"her_clothing": ["чулки"], "removed_clothing": ["платье на полу"], '
            '"items_in_play": ["вибратор"]}', current)
        self.assertEqual(new["her_clothing"], ["чулки"])           # dress came off
        self.assertEqual(new["removed_clothing"], ["платье на полу"])
        self.assertEqual(new["items_in_play"], ["вибратор"])       # newly introduced

    def test_parse_update_bad_json_returns_none(self):
        import scene
        self.assertIsNone(scene.parse_update("not json", {"location": "x"}))

    def test_render_block_and_empty(self):
        import scene
        self.assertEqual(scene.render({}, "ru"), "")
        block = scene.render({"location": "спальня", "her_posture": "на животе"}, "ru")
        self.assertIn("спальня", block)
        self.assertIn("на животе", block)
        self.assertIn("ПОЗУ", block)        # pose held like clothing — no sudden pose jumps
        en = scene.render({"her_posture": "on her back"}, "en")
        self.assertIn("POSE", en)
        self.assertIn("no sudden pose", en)

    def test_third_participant_pose_tracked_in_people_present(self):
        import scene
        current = {"location": "комната", "her_posture": "на коленях", "his_position": "стоит",
                   "configuration": "", "accessibility": "", "contact_map": [],
                   "her_clothing": [], "removed_clothing": [], "items_in_play": [],
                   "people_present": ["Лера — на спине, ноги раздвинуты, привязана"],
                   "other_facts": []}
        # an update about someone else must NOT drop a third participant's tracked position
        kept = scene.parse_update('{"his_position": "подходит ближе"}', current)
        self.assertIn("Лера — на спине, ноги раздвинуты, привязана", kept["people_present"])
        # when the dialogue moves her, the entry updates (на спине -> на животе)
        moved = scene.parse_update(
            '{"people_present": ["Лера — на животе, попой кверху, ноги раздвинуты"]}', current)
        self.assertEqual(moved["people_present"],
                         ["Лера — на животе, попой кверху, ноги раздвинуты"])
        block = scene.render(moved, "ru")
        self.assertIn("Кто ещё в сцене", block)   # third participant + pose rendered
        self.assertIn("на животе", block)

    def test_scene_str_slots_allow_multi_person_room(self):
        import scene
        long_cfg = "Кара на коленях рядом с привязанной Лерой; " * 4   # > 120 chars
        new = scene.parse_update('{"configuration": ' + __import__("json").dumps(long_cfg) + '}',
                                 scene._empty())
        self.assertGreater(len(new["configuration"]), 120)   # not truncated at 120 anymore


class SceneStateTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.mod = tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(ALLOWED_CHAT_IDS="111",
                          DB_PATH=str(Path(self.tmp.name) / "s.db"),
                          MEDIA_DIR=str(Path(self.tmp.name) / "m"))
        self.agent = tg_ingest_agent.Agent(cfg)
        self.conn = self.agent.conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_store_roundtrip_and_clear(self):
        mid = store.meeting_start(self.conn, 111, kind="visit")
        store.scene_set(self.conn, mid, {"location": "диван", "other_facts": ["плед"]})
        got = store.scene_get(self.conn, mid)
        self.assertEqual(got["location"], "диван")
        self.assertEqual(got["other_facts"], ["плед"])
        store.scene_clear(self.conn, mid)
        self.assertEqual(store.scene_get(self.conn, mid), {})

    def test_meeting_end_records_body_changes(self):
        import json
        mid = store.meeting_start(self.conn, 111, kind="visit")
        store.meeting_turn_add(self.conn, mid, "boss", "оставлю тебе засос на шее")
        recap = json.dumps({"title": "вечер", "summary": "тёплый вечер", "highlights": [],
                            "endearments": [], "body_changes": [
                                {"feature": "засос на шее", "permanence": "mark", "note": "от Олега"}]})
        with mock.patch.object(llm, "chat_profile", return_value=recap), \
                mock.patch.object(llm, "embed", side_effect=fake_embed):
            meeting.end(self.conn, self.agent.cfg, 111)
        feats = {r["feature"] for r in store.body_active(self.conn)}
        self.assertIn("засос на шее", feats)     # the mark persists in long-term body memory

    def test_scene_cleared_when_meeting_ends(self):
        mid = store.meeting_start(self.conn, 111, kind="visit")
        store.scene_set(self.conn, mid, {"location": "спальня"})
        with mock.patch.object(llm, "chat_profile", return_value='{"summary":"вечер"}'), \
                mock.patch.object(llm, "embed", side_effect=fake_embed):
            meeting.end(self.conn, self.agent.cfg, 111)
        self.assertEqual(store.scene_get(self.conn, mid), {})   # ended -> snapshot gone

    def test_meeting_presence_injects_scene(self):
        mid = store.meeting_start(self.conn, 111, kind="visit", setting="у неё")
        store.kv_set(self.conn, "closeness_stage", "3")
        store.scene_set(self.conn, mid, {"her_posture": "на животе, подушка под бёдрами"})
        pres = self.agent._meeting_presence("ru", store.meeting_active(self.conn, 111))
        self.assertIn("на животе", pres)        # the established pose is in her context

    def test_do_converse_updates_scene_and_uses_meeting_profile(self):
        mid = store.meeting_start(self.conn, 111, kind="visit")
        store.kv_set(self.conn, "closeness_stage", "3")
        store.meeting_turn_add(self.conn, mid, "boss", "ты ложишься на живот")
        captured = {}

        def fake_cp(cfg, conn, skill, messages, **kw):
            captured.setdefault(skill, kw.get("profile"))
            if skill == "scene":
                return '{"her_posture": "на животе"}'
            return "Я ложусь, как ты хочешь 🤍"

        with mock.patch.object(llm, "chat_profile", side_effect=fake_cp), \
                mock.patch.object(self.agent, "reply"), \
                mock.patch.object(self.agent, "send_chat_action"), \
                mock.patch.object(self.agent, "maybe_curate_conversation"), \
                mock.patch.object(self.agent, "_converse_grounding", return_value=""):
            self.agent.do_converse(111, "ru", "ты ложишься на живот")
        self.assertEqual(captured.get("converse"), "converse_meeting")   # roomier profile on a date
        self.assertEqual(captured.get("scene"), "scene_update")          # scene refreshed
        self.assertEqual(store.scene_get(self.conn, mid)["her_posture"], "на животе")

    def test_meeting_duration_note(self):
        from datetime import datetime, timezone, timedelta
        started = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        note = self.agent._meeting_duration_note({"started_at": started}, "ru")
        self.assertIn("3", note)        # ~3h together is surfaced to her
        # under an hour -> no note (avoid noise)
        fresh = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        self.assertEqual(self.agent._meeting_duration_note({"started_at": fresh}, "ru"), "")

    def test_clarify_during_date_not_logged_unclear(self):
        # P4: a non-command roleplay line during a live date just converses; it must NOT be
        # logged as unclear_request (that count was almost all date roleplay).
        store.meeting_start(self.conn, 111, kind="visit")
        with mock.patch.object(router, "route",
                               return_value={"action": "clarify", "params": {}, "confidence": 0.2}), \
                mock.patch.object(llm, "chat_profile", return_value="иди ко мне 🤍"), \
                mock.patch.object(self.agent, "reply"), \
                mock.patch.object(self.agent, "send_chat_action"), \
                mock.patch.object(self.agent, "maybe_curate_conversation"), \
                mock.patch.object(self.agent, "_converse_grounding", return_value=""):
            self.agent.dispatch(111, {}, "я снова рядом с тобой сзади")
        n = self.conn.execute(
            "SELECT COUNT(*) n FROM issues WHERE kind='unclear_request'").fetchone()["n"]
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
