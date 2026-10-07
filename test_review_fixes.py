"""Regression cases drawn from the October 7 code and live-performance review."""
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import common
import inference_io
import issue_digest
import knowledge
import llm
import reminder_time
import reminders
import store


class TimeEvidenceTests(unittest.TestCase):
    NOW = datetime(2026, 10, 4, 17, 26, tzinfo=timezone.utc)

    def test_event_date_is_distinct_from_reminder_date(self):
        out = reminder_time.checked_params("В пятницу ужин в 19:30. Напомни за два дня до этого",
            {"title": "ужин", "due_utc": "2026-10-09T16:30:00+00:00"}, 3, self.NOW)
        self.assertEqual(out["due_utc"], "2026-10-07T16:30:00+00:00")

    def test_explicit_reminder_clock_wins(self):
        out = reminder_time.checked_params("Dinner Friday at 19:30. Remind me two days before at 18:00",
            {"title": "dinner"}, 3, self.NOW)
        self.assertEqual(out["due_utc"], "2026-10-07T15:00:00+00:00")

    def test_missing_event_time_clarifies(self):
        out = reminder_time.checked_params("Ужин в пятницу, напомни за два дня до этого",
            {"title": "ужин", "due_utc": "2026-10-09T16:30:00+00:00"}, 3, self.NOW)
        self.assertNotIn("due_utc", out)

    def test_missing_time_does_not_become_now(self):
        out = reminder_time.checked_params("Напомни ещё про ответ Anthropic",
            {"title": "ответ", "due_utc": self.NOW.isoformat()}, 3, self.NOW)
        self.assertNotIn("due_utc", out)

    def test_existing_daypart_path_is_preserved(self):
        params = {"title": "bank", "due_utc": "2026-10-05T06:00:00+00:00"}
        self.assertEqual(reminder_time.checked_params("Напомни завтра утром про банк", params, 3), params)

    def test_named_weekday_is_followup_scaffold(self):
        self.assertEqual(reminders.followup_extra_words("Напомни в воскресенье", "SSL"), [])
        self.assertEqual(reminder_time.weekday_due("в воскресенье в 19:00", 3, self.NOW),
                         "2026-10-11T16:00:00+00:00")
        self.assertTrue(reminders.followup_extra_words("в воскресенье про Эрику", "SSL"))


class GroundingTests(unittest.TestCase):
    CONTEXT = [{"message_id": 1, "note_no": 3, "text": "Рейс 14 июня в 10:05."}]

    def test_nonexistent_citation_is_rejected(self):
        self.assertIsNone(knowledge.checked_answer("Рейс завтра (#999999)", self.CONTEXT, "ru"))

    def test_real_citation_cannot_launder_an_invented_fact(self):
        answer = knowledge.checked_answer("Рейс завтра в 23:59 (#3)", self.CONTEXT, "ru")
        self.assertIn("14 июня", answer)
        self.assertNotIn("23:59", answer)
        self.assertNotIn("завтра", answer)

    def test_fabricated_excerpt_rejected(self):
        self.assertIsNone(knowledge.checked_answer(json.dumps({"excerpts": [
            {"note_no": 3, "quote": "Рейс завтра"}]}), self.CONTEXT, "ru"))

    def test_exact_excerpt_retains_provenance(self):
        answer = knowledge.checked_answer(json.dumps({"excerpts": [
            {"note_no": 3, "quote": "Рейс 14 июня в 10:05."}]}), self.CONTEXT, "ru")
        self.assertIn("(#3)", answer)

    def test_empty_selection_refuses(self):
        self.assertEqual(knowledge.checked_answer('{"excerpts":[]}', self.CONTEXT, "en"),
                         knowledge.no_answer("en"))


class LanguageEvidenceTests(unittest.TestCase):
    def test_entity_does_not_switch_language(self):
        for text in ("SSL", "HRlink"):
            self.assertIsNone(common.detect_lang(text))

    def test_explicit_english_remains_english(self):
        for text in ("good morning", "thank you", "please move SSL tomorrow", "hello"):
            self.assertEqual(common.detect_lang(text), "en")


class BudgetAndDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = store.open_db(Path(self.tmp.name) / "test.db")
        self.addCleanup(self.conn.close)
        self.cfg = common.load_config({"TELEGRAM_BOT_TOKEN": "123:fixture", "ALLOWED_CHAT_IDS": "111",
                                       "DO_MODEL_ACCESS_KEY": "fixture", "BUDGET_DAILY_USD": "0.005",
                                       "DO_CHAT_MODEL": "unknown-model"})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_call_that_can_cross_cap_never_leaves(self):
        with mock.patch.object(llm, "urlopen") as opener:
            with self.assertRaises(llm.BudgetExceeded):
                llm.chat(self.cfg, self.conn, "probe", [{"role": "user", "content": "x"}], max_tokens=1000)
            opener.assert_not_called()

    def test_timeout_keeps_durable_unknown_reserve(self):
        self.cfg.budget_daily_usd = 10
        with mock.patch.object(llm, "urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(llm.LLMError):
                llm.chat(self.cfg, self.conn, "probe", [{"role": "user", "content": "x"}])
        row = self.conn.execute("SELECT kind,cost_usd FROM llm_usage").fetchone()
        self.assertEqual(row[0], "unknown_chat")
        self.assertGreater(row[1], 0)

    def test_deadline_bounds_a_transport_ignoring_socket_timeout(self):
        release = threading.Event()
        def slow(*args, **kwargs):
            release.wait(1)
            raise TimeoutError("late")
        started = time.monotonic()
        try:
            with self.assertRaises(llm.LLMError):
                inference_io.json_request(slow, None, .03, llm.LLMError)
            self.assertLess(time.monotonic() - started, .5)
        finally:
            release.set()

    def test_definite_rejection_releases_but_server_failure_keeps_reserve(self):
        from urllib.error import HTTPError
        self.cfg.budget_daily_usd = 10
        for status, kind in ((400,"rejected_chat"),(500,"unknown_chat")):
            with mock.patch.object(llm,"urlopen",side_effect=HTTPError("https://fixture",status,"fixture",{},None)):
                with self.assertRaises(llm.LLMError):
                    llm.chat(self.cfg,self.conn,"probe",[{"role":"user","content":"x"}])
            row=self.conn.execute("SELECT kind,cost_usd FROM llm_usage ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(row[0],kind)
            if status==400: self.assertEqual(row[1],0)
            else: self.assertGreater(row[1],0)

    def test_missing_usage_retains_conservative_cost(self):
        import io
        self.cfg.budget_daily_usd=10
        class Response(io.BytesIO):
            pass
        response=Response(json.dumps({"choices":[{"message":{"content":"ok"}}]}).encode())
        with mock.patch.object(llm,"urlopen",return_value=response):
            llm.chat(self.cfg,self.conn,"probe",[{"role":"user","content":"x"}],max_tokens=1000)
        row=self.conn.execute("SELECT kind,cost_usd FROM llm_usage").fetchone()
        self.assertEqual(row[0],"unknown_chat")
        self.assertGreaterEqual(row[1],llm.chat_cost(self.cfg.do_model,513,1000,llm.pricing_table(self.cfg)))

    def test_real_chat_success_replaces_paid_health_probe(self):
        import io
        self.cfg.budget_daily_usd=10
        payload={"choices":[{"message":{"content":"actual answer"}}],"usage":{"prompt_tokens":10,"completion_tokens":5}}
        with mock.patch.object(llm,"urlopen",return_value=io.BytesIO(json.dumps(payload).encode())):
            llm.chat(self.cfg,self.conn,"converse",[{"role":"user","content":"hello"}])
        with mock.patch.object(llm,"urlopen") as opener:
            self.assertEqual(llm.model_ok(self.cfg,self.conn,self.cfg.do_model),(True,""))
        opener.assert_not_called()


class DigestTests(unittest.TestCase):
    def test_no_open_issues_is_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = store.open_db(Path(tmp) / "test.db")
            try:
                self.assertEqual(issue_digest.text(conn, "en"), "")
            finally:
                conn.close()


class DigestContentTests(unittest.TestCase):
    NOW = datetime(2026, 10, 7, 14, 35, tzinfo=timezone.utc)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "digest.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add(self, kind, detail, at, **values):
        with mock.patch.object(store, "_now", return_value=at):
            store.issue_add(self.conn, 111, kind, detail)
        for field, value in values.items():
            self.conn.execute(f"UPDATE issue_patterns SET {field}=? WHERE kind=? AND detail=?",
                              (value, kind, detail))
        self.conn.commit()

    def test_realistic_old_html_and_repair_events_do_not_become_chat_diagnostics(self):
        self.add("llm_error", "router: inference request failed with HTTP 503: <!DOCTYPE html>\n"
                 '<html><title>Provider maintenance</title><link href="https://provider.invalid/a">',
                 "2026-08-24T17:58:51+00:00")
        self.add("converse_action_repaired", "An old reply that was rewritten", "2026-08-21T10:00:00+00:00")
        self.add("correction", "A rule already learned", "2026-07-28T10:00:00+00:00")
        self.add("curation_skipped_after_fabrication", "no rule learned from a fake action",
                 "2026-08-01T10:00:00+00:00")
        before = list(self.conn.execute("SELECT * FROM issue_patterns"))
        body = issue_digest.text(self.conn, "ru", now=self.NOW, tz_offset=3)
        self.assertIn("Ранее записанные, ещё не отмеченные закрытыми", body)
        self.assertIn("24.08.2026", body)
        self.assertIn("Код ошибки: 503.", body)
        for raw in ("<html", "DOCTYPE", "provider.invalid", "router:", "An old reply", "already learned", "fake action"):
            self.assertNotIn(raw, body)
        self.assertNotIn("за месяц:", body)
        self.assertNotIn("Новые или повторившиеся", body)
        self.assertNotIn("Закрыто", body)
        self.assertEqual(list(self.conn.execute("SELECT * FROM issue_patterns")), before)

    def test_owner_example_is_localized_redacted_and_one_line(self):
        self.add("boss_reported", "Напомнила\nне про ту задачу. password=example-secret-value",
                 "2026-10-06T10:00:00+00:00")
        body = issue_digest.text(self.conn, "ru", now=self.NOW)
        self.assertIn("Проблемы, о которых ты сообщил", body)
        self.assertIn("Пример: «Напомнила не про ту задачу.", body)
        self.assertNotIn("boss_reported", body)
        self.assertNotIn("example-secret-value", body)
        self.assertIn("07.09.2026–07.10.2026", body)

    def test_recent_recurrence_does_not_report_lifetime_count_as_monthly_count(self):
        self.add("router_invalid_output", "Напомни в воскресенье", "2026-10-06T10:00:00+00:00",
                 first_seen_at="2026-07-01T10:00:00+00:00", occurrences=73)
        body = issue_digest.text(self.conn, "ru", "2026-09-07T10:00:00+00:00", now=self.NOW)
        self.assertIn("Новые или повторившиеся", body)
        self.assertIn("открытых записей: 1", body)
        self.assertNotIn("73", body)
        self.assertIn("Напомни в воскресенье", body)

    def test_only_completed_handling_stays_quiet_without_resolving_history(self):
        for kind in issue_digest.COMPLETED_HANDLING:
            self.add(kind, "Historical handling evidence", "2026-10-06T10:00:00+00:00")
        self.assertEqual(issue_digest.text(self.conn, "en", now=self.NOW), "")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM issue_patterns WHERE status='open'").fetchone()[0], 3)

    def test_unknown_kind_does_not_leak_identifiers_or_diagnostics(self):
        self.add("internal_transport_xyz", "Traceback: private diagnostic", "2026-10-06T10:00:00+00:00")
        body = issue_digest.text(self.conn, "en", now=self.NOW)
        self.assertIn("Other problems to review", body)
        self.assertNotIn("internal_transport_xyz", body)
        self.assertNotIn("Traceback", body)

    def test_previous_digest_and_resolutions_use_timezone_aware_boundaries(self):
        self.add("boss_reported", "Earlier", "2026-10-06T10:00:00+00:00")
        self.add("fetch_failed", "A closed request", "2026-10-05T10:00:00+00:00",
                 status="resolved", resolved_at="2026-10-07T15:00:00+03:00")
        self.add("calendar_failed", "Resolved before this period", "2026-09-01T10:00:00+00:00",
                 status="resolved", resolved_at="2026-09-07T09:00:00+03:00")
        body = issue_digest.text(self.conn, "en", "2026-09-07T10:00:00+03:00", now=self.NOW, tz_offset=3)
        self.assertIn("Since the previous digest: 07.09.2026–07.10.2026", body)
        self.assertIn("Issue entries marked resolved during this period: 1.", body)

    def test_first_period_uses_a_calendar_month_at_month_end(self):
        self.add("boss_reported", "Fixture", "2026-03-30T10:00:00+00:00")
        body = issue_digest.text(self.conn, "en", now=datetime(2026, 3, 31, tzinfo=timezone.utc))
        self.assertIn("28.02.2026–31.03.2026", body)

    def test_long_example_has_a_word_boundary_and_explicit_ellipsis(self):
        self.add("boss_reported", "Repeat this sentence without breaking words. " * 8,
                 "2026-10-06T10:00:00+00:00")
        body = issue_digest.text(self.conn, "en", now=self.NOW)
        line = next(line for line in body.splitlines() if "Example:" in line)
        self.assertTrue(line.endswith("…”."))
        self.assertIn(line.split("“", 1)[1][:-3].split()[-1],
                      ("Repeat", "this", "sentence", "without", "breaking", "words."))

    def test_message_budget_keeps_whole_entries_footer_and_omitted_count(self):
        for kind in ("boss_reported", "correction_unresolved", "llm_error", "router_invalid_output", "fetch_failed"):
            self.add(kind, "A reasonably long example about the request. " * 5,
                     "2026-10-06T10:00:00+00:00")
        with mock.patch.object(issue_digest, "MAX_MESSAGE_CHARS", 550):
            body = issue_digest.text(self.conn, "en", now=self.NOW)
        self.assertLessEqual(len(body), 550)
        self.assertIn("Other open entries:", body)
        self.assertTrue(body.endswith("Tell me which problem to start with."))
        self.assertTrue(all(line.endswith((".", ":")) for line in body.splitlines()))

    def test_october_receipt_prevents_any_replacement_send_on_restart(self):
        store.kv_set(self.conn, "issue_digest_month", "2026-10")
        store.kv_set(self.conn, "issue_digest_delivered_at", "2026-10-07T14:35:30.203859+00:00")
        agent = issue_digest.IssueDigestMixin()
        agent.conn = self.conn
        agent.tz_offset = lambda: 3
        agent._send_all = mock.Mock()
        with mock.patch.object(issue_digest, "datetime") as clock:
            clock.now.return_value = self.NOW
            agent.check_monthly_issue_digest()
        agent._send_all.assert_not_called()
        self.assertEqual(store.kv_get(self.conn, "issue_digest_delivered_at"), "2026-10-07T14:35:30.203859+00:00")


from testlib import make_config, setUpModule, tearDownModule


class RuntimeFixTests(unittest.TestCase):
    def setUp(self):
        import tg_ingest_agent
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_config(ALLOWED_CHAT_IDS="111", DB_PATH=str(Path(self.tmp.name)/"cara.db"),
                               MEDIA_DIR=str(Path(self.tmp.name)/"media"), STT_MODE="local_server")
        self.agent = tg_ingest_agent.Agent(self.cfg)
        self.conn = self.agent.conn
        self.addCleanup(self.conn.close)
        self.agent.reply = mock.Mock(return_value=True)
        self.agent.send_chat_action = mock.Mock()

    def test_voice_transport_never_receives_database_and_does_not_block(self):
        started, release = threading.Event(), threading.Event()
        main_thread = threading.get_ident()
        path = Path(self.tmp.name)/"voice.oga"; path.write_bytes(b"fixture")
        self.agent.download_file = mock.Mock(return_value=path)
        update = {"update_id":41,"message":{"message_id":2,"chat":{"id":111},
                  "from":{"id":111},"voice":{"duration":17,"file_id":"fixture"}}}
        store.telegram_update_receive(self.conn, update, 111)
        def transport(cfg, conn, skill, audio, duration):
            self.assertIsNone(conn)
            self.assertNotEqual(threading.get_ident(), main_thread)
            started.set(); release.wait(2)
            return llm.Transcription("Remind me tomorrow morning about the bank", "whisper.cpp-server")
        def resume(updates):
            self.assertEqual(threading.get_ident(), main_thread)
            self.assertEqual(store.kv_get(self.conn,"voice_transcript:41"),
                             "Remind me tomorrow morning about the bank")
            store.telegram_update_done(self.conn,41)
        self.agent.process_update_batch = mock.Mock(side_effect=resume)
        try:
            with mock.patch.object(llm,"transcribe",side_effect=transport):
                self.assertEqual(self.agent.start_voice_job(update,111,update["message"]["voice"]),"defer")
                self.assertTrue(started.wait(1))
                self.agent.flush_voice_jobs()
                self.agent.process_update_batch.assert_not_called()
                # Main-thread state is still usable while STT waits.
                store.kv_set(self.conn,"poll_progress","yes")
                release.set()
                self.agent.voice_jobs[41][0].result(timeout=2)
                self.agent.flush_voice_jobs(); self.agent.flush_voice_jobs()
                self.agent.process_update_batch.assert_called_once_with([update])
                self.assertFalse(store.kv_get(self.conn,"voice_transcript:41"))
                self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM llm_usage WHERE kind='stt'").fetchone()[0],1)
                self.assertFalse(path.exists())
        finally:
            release.set()
            self.agent.voice_executor.shutdown(wait=True)

    def test_cached_voice_survives_restart_without_second_transcription(self):
        self.agent.voice_async=True
        store.kv_set(self.conn,"voice_transcript:52","hello")
        update={"update_id":52,"message":{"message_id":2,"chat":{"id":111},
                "from":{"id":111},"voice":{"duration":17,"file_id":"fixture"}}}
        with mock.patch.object(self.agent,"start_voice_job") as start, \
             mock.patch.object(self.agent,"dispatch") as dispatch:
            self.agent.handle_update(update)
        start.assert_not_called()
        self.assertEqual(dispatch.call_args.args[2],"hello")

    def test_weekly_offsite_clock_is_independent_of_daily_local_clock(self):
        from datetime import timedelta
        import backup
        today=datetime.now(timezone.utc).date()
        previous=(today-timedelta(days=6)).isoformat()
        store.kv_set(self.conn,"backup_day",previous)
        with mock.patch.object(backup,"run",return_value={"file":"daily.db.gz","offsite":"not-due"}) as run:
            self.agent.run_db_backup(self.conn)
        self.assertFalse(run.call_args.kwargs["offsite_due"])
        self.assertEqual(store.kv_get(self.conn,"backup_offsite_day"),previous)
        store.kv_set(self.conn,"backup_offsite_day",(today-timedelta(days=7)).isoformat())
        with mock.patch.object(backup,"run",return_value={"file":"weekly.db.gz","offsite":"telegram"}) as run:
            self.agent.run_db_backup(self.conn)
        self.assertTrue(run.call_args.kwargs["offsite_due"])
        self.assertEqual(store.kv_get(self.conn,"backup_offsite_day"),today.isoformat())
        self.assertIn("weekly.db.gz",json.loads(store.kv_get(self.conn,"backup_weekly_pins")))

    def test_daily_rotation_keeps_previous_weekly_points(self):
        import backup
        self.cfg.backup_keep=2
        root=backup.backups_dir(self.cfg);root.mkdir(parents=True,exist_ok=True)
        names=[f"ingest-2026100{day}T120000Z.db.gz" for day in range(1,6)]
        for name in names: (root/name).write_bytes(b"fixture")
        backup.rotate(self.cfg,protected_names=[names[0]])
        self.assertEqual({p.name for p in root.glob("*.db.gz")},{names[0],names[3],names[4]})

    def test_digest_is_delivery_gated_deduplicated_and_excludes_legacy(self):
        store.issue_add(self.conn,111,"boss_reported","fixture open")
        store.issue_add(self.conn,111,"obsolete","fixture legacy")
        self.conn.execute("UPDATE issue_patterns SET status='legacy' WHERE kind='obsolete'");self.conn.commit()
        body=issue_digest.text(self.conn,"en")
        self.assertIn("fixture open",body);self.assertNotIn("fixture legacy",body)
        self.agent._sched_backing_off=mock.Mock(return_value=False)
        self.agent._sched_send_gave_up=mock.Mock(return_value=False)
        self.agent._send_all=mock.Mock(side_effect=[False,True])
        fixed=datetime(2026,10,7,12,tzinfo=timezone.utc)
        with mock.patch.object(issue_digest,"datetime") as dt:
            dt.now.return_value=fixed
            self.agent.check_monthly_issue_digest()
            self.assertIsNone(store.kv_get(self.conn,"issue_digest_month"))
            self.agent.check_monthly_issue_digest();self.agent.check_monthly_issue_digest()
        self.assertEqual(self.agent._send_all.call_count,2)
        self.assertEqual(store.kv_get(self.conn,"issue_digest_month"),"2026-10")

    def test_router_history_is_clipped_with_references_and_current_turn_once(self):
        import router
        store.convo_add(self.conn,111,"assistant","x"*3000+" #19 #22")
        store.convo_add(self.conn,111,"user","find bank")
        with mock.patch.object(llm,"chat_profile",return_value='{"action":"converse","params":{},"confidence":0.8}') as chat:
            router.route(self.cfg,self.conn,111,"find bank",None)
        prompt=chat.call_args.args[3][-1]["content"]
        self.assertLess(len(prompt),5000)
        self.assertIn("#19",prompt);self.assertIn("#22",prompt)
        self.assertEqual(prompt.count("find bank"),1)


    def test_late_gratitude_reply_is_bound_to_its_card_but_commands_are_not(self):
        import journals, router
        from datetime import timedelta
        category="\u0411\u043b\u0430\u0433\u043e\u0434\u0430\u0440\u043d\u043e\u0441\u0442\u0438"
        store.set_category_kind(self.conn,category,"journal")
        rid=store.reminder_add(self.conn,111,category,(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),recurrence="daily")
        store.reminder_touch_fired(self.conn,rid,(datetime.now(timezone.utc)-timedelta(minutes=53)).isoformat())
        self.agent._remember_fired_message(99,rid)
        for index,(text,captured) in enumerate((("Karaoke",True),("show my reminders",False),("what happened?",False))):
            store.pending_clear(self.conn,111)
            update={"update_id":60+index,"message":{"message_id":20+index,"chat":{"id":111},"from":{"id":111},"text":text,
                    "reply_to_message":{"message_id":99,"from":{"is_bot":True},"text":"journal reminder"}}}
            with mock.patch.object(self.agent,"_deterministic_capture") as capture, \
                 mock.patch.object(router,"route",return_value={"action":"converse","params":{},"confidence":1}), \
                 mock.patch.object(self.agent,"do_converse"):
                self.agent.handle_update(update)
            self.assertEqual(capture.called,captured,text)
            if captured: self.assertEqual(capture.call_args.args[3],category)


    def test_weekly_pins_exclude_manual_and_new_daily_archives(self):
        import backup
        self.cfg.backup_keep=7
        root=backup.backups_dir(self.cfg);root.mkdir(parents=True,exist_ok=True)
        weeklies=[f"ingest-2026090{day}T120000Z.db.gz" for day in range(1,8)]
        manual="ingest-pre-review-fixture.db.gz"
        daily="ingest-20260908T120000Z.db.gz"
        for name in weeklies+[manual,daily]: (root/name).write_bytes(b"fixture")
        # Simulates the first installed list, where a manual archive displaced a weekly point.
        previous=weeklies[1:]+[manual]
        pins=backup.weekly_recovery_pins(self.cfg,"2026-09-07",previous)
        self.assertEqual(pins,weeklies)
        backup.rotate(self.cfg,protected_names=pins)
        self.assertTrue(all((root/name).exists() for name in weeklies))
        self.assertTrue((root/manual).exists())
        self.assertTrue((root/daily).exists())
