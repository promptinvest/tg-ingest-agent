"""Provider bodies are byte-bounded before decoding, parsing or settlement."""
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

import inference_io
import llm
import store
from testlib import make_config


class TrackedResponse(io.BytesIO):
    def __init__(self, body, *, step=None, headers=None, entered=None, release=None):
        super().__init__(body)
        self.requests = []
        self.bytes_read = 0
        self.step = step
        self.headers = headers or {}
        self.entered = entered
        self.release = release
        self.closed_event = threading.Event()

    def read(self, size=-1):
        self.requests.append(size)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None and not self.release.wait(2):
            raise AssertionError("fixture transport was not released")
        if self.step is not None and size >= 0:
            size = min(size, self.step)
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk

    def close(self):
        super().close()
        self.closed_event.set()


class ProviderBodyTransportTests(unittest.TestCase):
    CAP = 128

    def setUp(self):
        patcher = mock.patch.object(inference_io, "MAX_RESPONSE_BYTES", self.CAP, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def request(response, timeout=1):
        return inference_io.json_request(lambda *_args, **_kwargs: response, None,
                                         timeout, llm.LLMError)

    def assert_bounded(self, response):
        self.assertTrue(response.requests)
        self.assertTrue(all(type(size) is int and 0 < size <= self.CAP + 1
                            for size in response.requests))
        self.assertLessEqual(response.bytes_read, self.CAP + 1)
        self.assertTrue(response.closed_event.wait(1))
        self.assertTrue(response.closed)

    def test_short_reads_are_collected_until_actual_eof(self):
        body = json.dumps({"answer": "synthetic answer" * 4}).encode()
        response = TrackedResponse(body, step=3)
        self.assertEqual(self.request(response), json.loads(body))
        self.assertGreater(len(response.requests), 2)
        self.assertEqual(response.bytes_read, len(body))
        self.assert_bounded(response)

    def test_exact_limit_json_remains_accepted(self):
        response = TrackedResponse(b'{"ok":true}' + b" " * (self.CAP - 11))
        self.assertEqual(self.request(response), {"ok": True})
        self.assertEqual(response.bytes_read, self.CAP)
        self.assert_bounded(response)

    def test_large_exact_limit_body_uses_only_bounded_transport_chunks(self):
        cap = 2 * 64 * 1024
        response = TrackedResponse(b'{"ok":true}' + b" " * (cap - 11))
        with mock.patch.object(inference_io, "MAX_RESPONSE_BYTES", cap, create=True):
            self.assertEqual(self.request(response), {"ok": True})
        self.assertEqual(response.bytes_read, cap)
        self.assertGreaterEqual(len(response.requests), 3)
        self.assertTrue(all(0 < size <= 64 * 1024 for size in response.requests))
        self.assertTrue(response.closed_event.wait(1))
        self.assertTrue(response.closed)

    def test_limit_plus_one_is_unknown_before_json_parser_runs(self):
        body = b'{"private":"fixture response"}'
        response = TrackedResponse(body + b" " * (self.CAP + 1 - len(body)))
        with mock.patch.object(inference_io.json, "loads", wraps=json.loads) as parse:
            with self.assertRaises(llm.LLMError) as rejected:
                self.request(response)
        parse.assert_not_called()
        self.assertTrue(rejected.exception.transient)
        self.assertTrue(rejected.exception.usage_unknown)
        self.assertFalse(getattr(rejected.exception, "request_not_sent", False))
        self.assertNotIn("fixture response", str(rejected.exception))
        self.assert_bounded(response)

    def test_overflow_rejects_before_even_invalid_utf8_can_be_decoded(self):
        response = TrackedResponse(b"\xff" * (self.CAP + 1), step=7)
        with self.assertRaises(llm.LLMError) as rejected:
            self.request(response)
        self.assertTrue(rejected.exception.usage_unknown)
        self.assert_bounded(response)

    def test_multibyte_limit_counts_wire_bytes_not_characters(self):
        body = json.dumps({"text": "голос 😀"}, ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(body), len(body.decode("utf-8")))
        with mock.patch.object(inference_io, "MAX_RESPONSE_BYTES", len(body), create=True):
            self.assertEqual(self.request(TrackedResponse(body, step=1)), {"text": "голос 😀"})
        with mock.patch.object(inference_io, "MAX_RESPONSE_BYTES", len(body) - 1, create=True):
            with self.assertRaises(llm.LLMError):
                self.request(TrackedResponse(body))

    def test_actual_bytes_control_even_without_or_with_misleading_length_headers(self):
        for headers in ({}, {"Content-Length": "0"}, {"Content-Length": "1"},
                        {"Content-Length": "999999999999999999999"}):
            with self.subTest(headers=headers):
                response = TrackedResponse(b'{"ok":true}', headers=headers)
                self.assertEqual(self.request(response), {"ok": True})
                self.assert_bounded(response)
                oversized = TrackedResponse(b" " * (self.CAP + 1), headers=headers)
                with self.assertRaises(llm.LLMError):
                    self.request(oversized)
                self.assert_bounded(oversized)

    def test_under_limit_malformed_and_empty_bodies_keep_json_errors(self):
        for body in (b"", b"{", b"not json"):
            with self.subTest(body=body):
                response = TrackedResponse(body)
                with self.assertRaises(json.JSONDecodeError):
                    self.request(response)
                self.assert_bounded(response)

    def test_under_limit_invalid_utf8_keeps_decode_error(self):
        response = TrackedResponse(b'"\xff"')
        with self.assertRaises(UnicodeDecodeError):
            self.request(response)
        self.assert_bounded(response)

    def test_oversize_closes_response_and_releases_physical_and_background_slots(self):
        slots, background = threading.BoundedSemaphore(1), threading.BoundedSemaphore(1)
        lease = inference_io.BackgroundLease(background)
        self.assertTrue(lease.acquire())
        response = TrackedResponse(b" " * (self.CAP + 1))
        with mock.patch.object(inference_io, "_slots", slots):
            try:
                with self.assertRaises(llm.LLMError):
                    lease.run(self.request, response)
            finally:
                self.assertTrue(response.closed_event.wait(1))
                acquired = slots.acquire(timeout=1)
                self.assertTrue(acquired)
                if acquired:
                    slots.release()
                acquired = background.acquire(timeout=1)
                self.assertTrue(acquired)
                if acquired:
                    background.release()
        self.assert_bounded(response)

    def test_caller_timeout_keeps_slot_until_late_oversize_transport_closes(self):
        slots, background = threading.BoundedSemaphore(1), threading.BoundedSemaphore(1)
        entered, release = threading.Event(), threading.Event()
        lease = inference_io.BackgroundLease(background)
        self.assertTrue(lease.acquire())
        response = TrackedResponse(b" " * (self.CAP + 1), entered=entered, release=release)
        with mock.patch.object(inference_io, "_slots", slots), \
                mock.patch.object(inference_io.json, "loads", wraps=json.loads) as parse:
            try:
                with self.assertRaises(llm.LLMError):
                    lease.run(self.request, response, .03)
                self.assertTrue(entered.is_set())
                self.assertFalse(background.acquire(blocking=False))
            finally:
                release.set()
                self.assertTrue(response.closed_event.wait(1))
                acquired = slots.acquire(timeout=1)
                self.assertTrue(acquired)
                if acquired:
                    slots.release()
                acquired = background.acquire(timeout=1)
                self.assertTrue(acquired)
                if acquired:
                    background.release()
            parse.assert_not_called()
        self.assert_bounded(response)


class ProviderBodyGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = store.open_db(Path(self.tmp.name) / "usage.db")
        self.addCleanup(self.conn.close)
        self.cfg = make_config(BUDGET_DAILY_USD="100", BUDGET_MONTHLY_USD="100",
                               STT_MODE="remote")
        self.history_id = store.usage_add(self.conn, "fixture", "unknown_chat",
                                          "historical", cost_usd=.0123)
        self.history = self.row(self.history_id)
        self.audio = Path(self.tmp.name) / "synthetic.oga"
        self.audio.write_bytes(b"synthetic audio")

    def row(self, row_id):
        return dict(self.conn.execute("SELECT * FROM llm_usage WHERE id=?", (row_id,)).fetchone())

    @staticmethod
    def body(kind, *, oversized=False):
        value = {"chat": {"choices": [{"message": {"content": '{"ok":true}'}}],
                          "usage": {"prompt_tokens": 10, "completion_tokens": 2}},
                 "embed": {"data": [{"index": 0, "embedding": [.1, .2]}],
                           "usage": {"prompt_tokens": 8}},
                 "stt": {"text": "synthetic transcript"}}[kind]
        if oversized:
            value["padding"] = "x" * 512
        return json.dumps(value).encode()

    def call(self, kind):
        if kind == "chat":
            return llm.chat(self.cfg, self.conn, "converse", [{"role": "user", "content": "Hi"}],
                            model="deepseek-4-flash", max_tokens=40)
        if kind == "embed":
            return llm.embed(self.cfg, self.conn, "ask", ["synthetic source"])
        return llm.transcribe(self.cfg, self.conn, "stt", self.audio, 4)

    def test_all_paid_entry_points_preserve_unknown_reserve_and_history_on_overflow(self):
        for kind in ("chat", "embed", "stt"):
            with self.subTest(kind=kind):
                response = TrackedResponse(self.body(kind, oversized=True), step=19)
                with mock.patch.object(inference_io, "MAX_RESPONSE_BYTES", 256, create=True), \
                        mock.patch.object(llm, "urlopen", return_value=response) as opener:
                    with self.assertRaises(llm.LLMError) as rejected:
                        self.call(kind)
                self.assertTrue(rejected.exception.usage_unknown)
                self.assertEqual(opener.call_count, 1)
                last = dict(self.conn.execute("SELECT * FROM llm_usage ORDER BY id DESC LIMIT 1").fetchone())
                self.assertEqual(last["kind"], "unknown_" + kind)
                self.assertGreater(last["cost_usd"], 0)
                self.assertEqual(self.row(self.history_id), self.history)
                self.assertTrue(response.closed_event.wait(1))
                self.assertTrue(response.closed)
                self.assertLessEqual(response.bytes_read, 257)

    def test_small_successful_chat_embed_and_remote_stt_keep_existing_settlement(self):
        for kind in ("chat", "embed", "stt"):
            with self.subTest(kind=kind):
                response = TrackedResponse(self.body(kind), step=13)
                with mock.patch.object(inference_io, "MAX_RESPONSE_BYTES", 256, create=True), \
                        mock.patch.object(llm, "urlopen", return_value=response):
                    result = self.call(kind)
                self.assertEqual(result, {"chat": '{"ok":true}', "embed": [[.1, .2]],
                                          "stt": "synthetic transcript"}[kind])
                row = self.conn.execute("SELECT kind,cost_usd FROM llm_usage ORDER BY id DESC LIMIT 1").fetchone()
                self.assertEqual(row["kind"], kind)
                self.assertGreater(row["cost_usd"], 0)
                self.assertEqual(self.row(self.history_id), self.history)
                self.assertTrue(response.closed_event.wait(1))
                self.assertTrue(response.closed)

    def test_async_no_unknown_retry_policy_still_dispatches_only_once(self):
        response = TrackedResponse(self.body("chat", oversized=True))
        with mock.patch.object(inference_io, "MAX_RESPONSE_BYTES", 256, create=True), \
                mock.patch.object(llm, "urlopen", return_value=response) as opener:
            with self.assertRaises(llm.LLMError):
                llm.chat_profile(self.cfg, self.conn, "task", [{"role": "user", "content": "Hi"}],
                                 profile="task_synthesis", retry_unknown=False)
        self.assertEqual(opener.call_count, 1)
        rows = self.conn.execute("SELECT kind FROM llm_usage WHERE id != ?", (self.history_id,)).fetchall()
        self.assertEqual([row["kind"] for row in rows], ["unknown_chat"])
        self.assertEqual(self.row(self.history_id), self.history)

    def test_local_whisper_overflow_does_not_retry_or_fall_back_or_record_success(self):
        self.cfg.stt_mode = "local_server"
        response = TrackedResponse(self.body("stt", oversized=True))
        with mock.patch.object(inference_io, "MAX_RESPONSE_BYTES", 256, create=True), \
                mock.patch.object(llm, "urlopen", return_value=response) as opener, \
                mock.patch.object(llm, "_transcribe_local") as cold, \
                mock.patch.object(llm.time, "sleep") as sleep:
            with self.assertRaises(llm.LLMError):
                self.call("stt")
        self.assertEqual(opener.call_count, 1)
        cold.assert_not_called()
        sleep.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT count(*) FROM llm_usage").fetchone()[0], 1)
        self.assertEqual(self.row(self.history_id), self.history)
        self.assertTrue(response.closed)

    def test_local_whisper_json_and_legacy_plain_text_stay_free(self):
        self.cfg.stt_mode = "local_server"
        for body in (b'{"text":"  synthetic local transcript  "}', b"  synthetic local transcript  "):
            with self.subTest(body=body):
                response = TrackedResponse(body, step=2)
                with mock.patch.object(inference_io, "MAX_RESPONSE_BYTES", 256, create=True), \
                        mock.patch.object(llm, "urlopen", return_value=response):
                    self.assertEqual(self.call("stt"), "synthetic local transcript")
                row = self.conn.execute("SELECT kind,model,cost_usd FROM llm_usage ORDER BY id DESC LIMIT 1").fetchone()
                self.assertEqual(tuple(row), ("stt", "whisper.cpp-server", 0.0))
                self.assertEqual(self.row(self.history_id), self.history)

    def test_http_status_owns_rejected_or_unknown_usage_even_with_large_error_body(self):
        for kind in ("chat", "embed", "stt"):
            for status, prefix in ((400, "rejected_"), (429, "unknown_"), (500, "unknown_")):
                with self.subTest(kind=kind, status=status):
                    stream = TrackedResponse(b"provider diagnostic " * 5000)
                    error = HTTPError("https://fixture.invalid", status, "synthetic", {}, stream)
                    with mock.patch.object(llm, "urlopen", side_effect=error):
                        with self.assertRaises(llm.LLMError):
                            self.call(kind)
                    row = self.conn.execute("SELECT kind,cost_usd FROM llm_usage ORDER BY id DESC LIMIT 1").fetchone()
                    self.assertEqual(row["kind"], prefix + kind)
                    self.assertEqual(row["cost_usd"] == 0, prefix == "rejected_")
                    self.assertLessEqual(stream.bytes_read, 64 * 1024)
                    self.assertTrue(all(size > 0 for size in stream.requests))
                    self.assertTrue(stream.closed)
                    self.assertEqual(self.row(self.history_id), self.history)


class ProviderErrorDiagnosticTests(unittest.TestCase):
    def test_bounded_diagnostics_keep_redaction_and_existing_visible_excerpt(self):
        key = "fixture-secret-access-key"
        stream = TrackedResponse((f"Bearer {key}; normal detail " + "x" * 70000).encode(), step=17)
        error = HTTPError("https://fixture.invalid", 500, "synthetic", {}, stream)
        message = llm._redacted_http_error(error, key)
        self.assertIn("HTTP 500", message)
        self.assertIn("<redacted>", message)
        self.assertNotIn(key, message)
        self.assertLessEqual(len(message), 550)
        self.assertEqual(stream.bytes_read, 64 * 1024)
        self.assertTrue(all(size > 0 for size in stream.requests))
        self.assertTrue(stream.closed)

    def test_error_body_read_failure_still_closes_and_keeps_http_class(self):
        class BrokenResponse(TrackedResponse):
            def read(self, size=-1):
                self.requests.append(size)
                raise OSError("fixture read failure")
        stream = BrokenResponse(b"unused")
        error = HTTPError("https://fixture.invalid", 403, "synthetic", {}, stream)
        self.assertEqual(llm._redacted_http_error(error, "fixture-key"),
                         "inference request failed with HTTP 403.")
        self.assertTrue(stream.closed)
        self.assertTrue(all(size > 0 for size in stream.requests))


if __name__ == "__main__":
    unittest.main()
