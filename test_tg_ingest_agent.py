#!/usr/bin/env python3
"""Offline unit tests: config, ingest pipeline, storage (no network)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
import ingest
import llm
import store


def make_config(**overrides):
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "ALLOWED_CHAT_IDS": "111,222",
        "DO_MODEL_ACCESS_KEY": "do-key",
    }
    env.update(overrides)
    return common.load_config(env)


class ConfigTests(unittest.TestCase):
    def test_config_list_separators(self):
        self.assertEqual(common.config_list("a, b|c;d\ne"), ["a", "b", "c", "d", "e"])
        self.assertEqual(common.config_list(""), [])
        self.assertEqual(common.config_list(None), [])

    def test_parse_chat_ids(self):
        self.assertEqual(common.parse_chat_ids("111, -100222"), {111, -100222})
        with self.assertRaises(SystemExit):
            common.parse_chat_ids("111,abc")

    def test_load_config_defaults(self):
        cfg = make_config()
        self.assertEqual(cfg.allowed_chat_ids, {111, 222})
        self.assertEqual(cfg.do_model, "anthropic-claude-haiku-4.5")
        self.assertEqual(cfg.router_model, cfg.do_model)
        self.assertEqual(cfg.poll_timeout, 50)
        self.assertEqual(cfg.seed_categories, [])
        self.assertEqual(cfg.fallback_category, "uncategorized")
        self.assertEqual(cfg.language, "ru")
        self.assertEqual(cfg.timezone_offset, 3)
        self.assertTrue(cfg.stt_enabled)
        self.assertEqual(cfg.budget_daily_usd, 1.0)
        self.assertEqual(cfg.budget_monthly_usd, 15.0)
        self.assertEqual(cfg.habit_threshold, 10)

    def test_load_config_required(self):
        for missing in ("TELEGRAM_BOT_TOKEN", "ALLOWED_CHAT_IDS", "DO_MODEL_ACCESS_KEY"):
            with self.assertRaises(SystemExit):
                make_config(**{missing: ""})

    def test_seed_categories_and_file_precedence(self):
        cfg = make_config(CATEGORIES="news, tools")
        self.assertEqual(cfg.seed_categories, ["news", "tools"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "categories.txt"
            path.write_text("alpha\n# comment\n\nbeta\n", encoding="utf-8")
            cfg = make_config(CATEGORIES="ignored", CATEGORIES_FILE=str(path))
            self.assertEqual(cfg.seed_categories, ["alpha", "beta"])


class UrlExtractionTests(unittest.TestCase):
    def test_utf16_offsets_with_emoji_and_cyrillic(self):
        # Emoji is 2 UTF-16 code units; naive Python slicing would be off by one.
        text = "\U0001F600 Привет https://example.com/a конец"
        offset = len("\U0001F600 Привет ".encode("utf-16-le")) // 2
        url = "https://example.com/a"
        entities = [{"type": "url", "offset": offset, "length": len(url)}]
        self.assertIn(url, ingest.extract_urls(text, entities))

    def test_text_link_and_regex_fallback_dedup(self):
        text = "see https://a.example/x and that's it."
        entities = [
            {"type": "text_link", "url": "https://b.example/y"},
            {"type": "url", "offset": 4, "length": len("https://a.example/x")},
        ]
        urls = ingest.extract_urls(text, entities)
        self.assertEqual(urls, ["https://b.example/y", "https://a.example/x"])

    def test_regex_only(self):
        urls = ingest.extract_urls("plain http://c.example/z, tail", None)
        self.assertEqual(urls, ["http://c.example/z"])


class LlmParsingTests(unittest.TestCase):
    def test_parse_clean_json(self):
        self.assertEqual(llm.parse_llm_json('{"category": "news"}'), {"category": "news"})

    def test_parse_fenced_json(self):
        text = 'Here you go:\n```json\n{"category": "news", "summary": "s"}\n```'
        self.assertEqual(llm.parse_llm_json(text)["category"], "news")

    def test_parse_prose_wrapped(self):
        text = 'Sure! {"category": "tools", "summary": "x"} hope that helps'
        self.assertEqual(llm.parse_llm_json(text)["category"], "tools")

    def test_parse_garbage(self):
        self.assertIsNone(llm.parse_llm_json("no json here"))
        self.assertIsNone(llm.parse_llm_json(""))
        self.assertIsNone(llm.parse_llm_json('["list", "not", "dict"]'))

    def test_normalize_category(self):
        self.assertEqual(llm.normalize_category("  AI   tools \n"), "AI tools")
        self.assertEqual(len(llm.normalize_category("x" * 100)), llm.MAX_CATEGORY_CHARS)
        self.assertIsNone(llm.normalize_category("   "))
        self.assertIsNone(llm.normalize_category(None))

    def test_match_category(self):
        cats = ["News", "Tools"]
        self.assertEqual(llm.match_category("news", cats), "News")
        self.assertEqual(llm.match_category(" TOOLS ", cats), "Tools")
        self.assertIsNone(llm.match_category("nope", cats))
        self.assertIsNone(llm.match_category(None, cats))


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_build_text_block(self):
        block = ingest.build_text_block("hello", "channel", "Some Channel", ["https://a.example"])
        self.assertIn("Forwarded from channel: Some Channel", block)
        self.assertIn("hello", block)
        self.assertIn("- https://a.example", block)
        block2 = ingest.build_text_block(None, None, None, [])
        self.assertIn("(no text)", block2)
        self.assertNotIn("Forwarded", block2)

    def test_build_llm_messages_taxonomy_injection_and_feedback(self):
        cfg = make_config()
        with_known = ingest.build_llm_messages(cfg, ["news", "tools"], "text", [])
        self.assertIn("Categories used so far: news, tools", with_known[0]["content"])
        self.assertIn("<message>", with_known[1]["content"][0]["text"])
        corrections = [{"suggested": "news", "corrected": "крипта"}]
        with_feedback = ingest.build_llm_messages(cfg, [], "text", [], corrections)
        self.assertIn("крипта", with_feedback[0]["content"])
        self.assertIn("no categories yet", with_feedback[0]["content"])
        # language policy: summaries stay in the source language, new
        # categories are proposed in English (service metadata)
        self.assertIn("STRICTLY in the language of the source", with_feedback[0]["content"])
        self.assertIn("NEW categories in English", with_feedback[0]["content"])

    def test_build_llm_messages_image_cap_and_oversize(self):
        cfg = make_config(MAX_LLM_IMAGES="2")
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(3):
                p = Path(tmp) / f"img{i}.jpg"
                p.write_bytes(b"\xff\xd8" + bytes(10))
                paths.append(str(p))
            big = Path(tmp) / "big.jpg"
            big.write_bytes(b"x" * (ingest.MAX_LLM_IMAGE_BYTES + 1))
            messages = ingest.build_llm_messages(cfg, [], "text", [str(big)] + paths)
            content = messages[1]["content"]
            image_parts = [c for c in content if c.get("type") == "image_url"]
            self.assertEqual(len(image_parts), 2)  # oversize skipped, cap respected

    def test_suggest_with_alternatives_and_canonical_match(self):
        cfg = make_config()
        reply = '{"category": "NEWS", "alternatives": ["Tools", "news", "ai"], "summary": "ok"}'
        with mock.patch.object(llm, "chat", return_value=reply):
            category, alternatives, summary = ingest.suggest(
                cfg, self.conn, ["news", "tools"], "t", []
            )
        self.assertEqual(category, "news")  # canonical existing spelling reused
        self.assertEqual(alternatives, ["tools", "ai"])  # deduped vs category, canonicalized
        self.assertEqual(summary, "ok")

    def test_suggest_corrective_retry_and_fallback(self):
        cfg = make_config()
        good = '{"category": "ideas", "alternatives": [], "summary": "ok"}'
        with mock.patch.object(llm, "chat", side_effect=["garbage", good]):
            self.assertEqual(ingest.suggest(cfg, self.conn, [], "t", []), ("ideas", [], "ok"))
        with mock.patch.object(llm, "chat", side_effect=["garbage", "still garbage"]):
            category, alternatives, summary = ingest.suggest(cfg, self.conn, [], "t", [])
            self.assertEqual(category, cfg.fallback_category)
            self.assertEqual(alternatives, [])
            self.assertEqual(summary, "still garbage")


class KeyboardTests(unittest.TestCase):
    def test_build_suggestion_keyboard(self):
        keyboard = ingest.build_suggestion_keyboard(7, "news", ["tools", "News", "ideas"])
        self.assertEqual(keyboard[0][0]["callback_data"], "s|7")
        self.assertIn("news", keyboard[0][0]["text"])
        alt_data = [b["callback_data"] for b in keyboard[1]]
        self.assertEqual(alt_data, ["a|7|tools", "a|7|ideas"])  # 'News' == category, dropped

    def test_keyboard_callback_byte_limit(self):
        long_cyrillic = "очень длинная категория на кириллице ww"  # > 64 bytes in UTF-8
        keyboard = ingest.build_suggestion_keyboard(7, "news", [long_cyrillic])
        self.assertEqual(len(keyboard), 1)  # alternative dropped, no alt row

    def test_parse_callback_data(self):
        self.assertEqual(ingest.parse_callback_data("s|12"), ("suggested", 12, None))
        self.assertEqual(ingest.parse_callback_data("a|12|AI tools"), ("named", 12, "AI tools"))
        for bad in (None, "", "s", "s|x", "a|12|", "z|12|cat"):
            self.assertIsNone(ingest.parse_callback_data(bad))


class ForwardOriginTests(unittest.TestCase):
    def test_channel(self):
        info = ingest.parse_forward_origin(
            {"type": "channel", "date": 5, "message_id": 42,
             "chat": {"id": -100123, "title": "My Channel"}}
        )
        self.assertEqual(info["chat_id"], -100123)
        self.assertEqual(info["title"], "My Channel")
        self.assertEqual(info["message_id"], 42)

    def test_hidden_user_and_empty(self):
        info = ingest.parse_forward_origin(
            {"type": "hidden_user", "sender_user_name": "Bob", "date": 1}
        )
        self.assertEqual(info["title"], "Bob")
        self.assertEqual(ingest.parse_forward_origin(None), {})

    def test_first_text_and_collect_urls(self):
        parts = [
            {"photo": [], "caption": ""},
            {"caption": "caption here", "caption_entities": [
                {"type": "text_link", "url": "https://x.example"}]},
        ]
        self.assertEqual(ingest.first_text(parts), "caption here")
        self.assertEqual(ingest.collect_urls(parts), ["https://x.example"])


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _insert(self, chat_id=1, msg_id=10, **extra):
        fields = {
            "chat_id": chat_id,
            "tg_message_id": msg_id,
            "received_at": "2026-06-11T00:00:00+00:00",
        }
        fields.update(extra)
        return store.insert_message(self.conn, fields)

    def test_kv_roundtrip(self):
        self.assertIsNone(store.kv_get(self.conn, "offset"))
        store.kv_set(self.conn, "offset", 7)
        self.assertEqual(store.kv_get(self.conn, "offset"), "7")

    def test_insert_dedup(self):
        first = self._insert()
        self.assertIsNotNone(first)
        self.assertIsNone(self._insert())  # redelivery is a no-op

    def test_ensure_category_case_insensitive_ascii(self):
        self.assertEqual(store.ensure_category(self.conn, "News"), "News")
        self.assertEqual(store.ensure_category(self.conn, "NEWS"), "News")
        self.assertEqual(store.ensure_category(self.conn, "tools"), "tools")
        self.assertEqual(set(store.known_categories(self.conn)), {"News", "tools"})

    def test_ensure_category_case_insensitive_cyrillic(self):
        # SQLite NOCASE is ASCII-only; the norm_key casefold must catch this.
        self.assertEqual(store.ensure_category(self.conn, "Крипта"), "Крипта")
        self.assertEqual(store.ensure_category(self.conn, "крипта"), "Крипта")
        self.assertEqual(store.ensure_category(self.conn, "КРИПТА"), "Крипта")
        self.assertEqual(store.known_categories(self.conn), ["Крипта"])

    def test_known_categories_ordered_by_usage(self):
        store.ensure_category(self.conn, "rare")
        store.ensure_category(self.conn, "common")
        for msg_id in (20, 21):
            row_id = self._insert(msg_id=msg_id, raw_text="x")
            store.confirm_category(self.conn, row_id, "common")
        self.assertEqual(store.known_categories(self.conn), ["common", "rare"])

    def test_suggestion_confirm_flow(self):
        row_id = self._insert(msg_id=30, raw_text="hello")
        store.set_suggestion(self.conn, row_id, "AI tools", "a summary", "model-x")
        row = store.get_message(self.conn, row_id)
        self.assertEqual(row["status"], "suggested")
        self.assertEqual(row["suggested_category"], "AI tools")
        self.assertIsNone(row["category"])
        store.set_suggestion_message(self.conn, row_id, 555)
        found = store.find_by_suggestion_message(self.conn, 1, 555)
        self.assertEqual(found["id"], row_id)
        self.assertIsNone(store.find_by_suggestion_message(self.conn, 1, 556))
        self.assertIsNone(store.find_by_suggestion_message(self.conn, 1, None))
        canonical = store.ensure_category(self.conn, "AI tools")
        store.confirm_category(self.conn, row_id, canonical)
        row = store.get_message(self.conn, row_id)
        self.assertEqual(row["status"], "confirmed")
        self.assertEqual(row["category"], "AI tools")

    def test_forward_duplicate_flow(self):
        original = self._insert(msg_id=10, forward_origin_chat_id=-5, forward_origin_message_id=99)
        store.set_suggestion(self.conn, original, "news", "summary", "model-x")
        store.confirm_category(self.conn, original, "news")
        dup = self._insert(msg_id=11, forward_origin_chat_id=-5, forward_origin_message_id=99)
        found = store.find_forward_duplicate(self.conn, -5, 99, dup)
        self.assertEqual(found["id"], original)
        store.mark_duplicate(self.conn, dup, found)
        row = store.get_message(self.conn, dup)
        self.assertEqual(row["duplicate_of"], original)
        self.assertEqual(row["category"], "news")
        self.assertEqual(row["status"], "duplicate")
        # duplicates are excluded from later duplicate lookups and retry sweeps
        self.assertEqual(store.find_forward_duplicate(self.conn, -5, 99, 999)["id"], original)
        self.assertEqual([r["id"] for r in store.pending_messages(self.conn, 5)], [])

    def test_pending_and_failure_path(self):
        row_id = self._insert(msg_id=20, raw_text="hello")
        self.assertEqual([r["id"] for r in store.pending_messages(self.conn, 5)], [row_id])
        for expected in (1, 2):
            self.assertEqual(store.bump_attempts(self.conn, row_id), expected)
        self.assertEqual(store.pending_messages(self.conn, 2), [])  # attempts exhausted
        store.mark_failed(self.conn, row_id)
        self.assertEqual(store.get_message(self.conn, row_id)["status"], "failed")

    def test_urls_and_images(self):
        row_id = self._insert(msg_id=30)
        store.insert_url(self.conn, row_id, "https://a.example")
        photo = {"file_id": "f", "file_unique_id": "u", "width": 10, "height": 20, "file_size": 5}
        store.insert_image(self.conn, row_id, 30, photo, "/tmp/u.jpg")
        self.assertEqual(
            [r["url"] for r in store.message_urls(self.conn, row_id)], ["https://a.example"]
        )
        images = store.message_images(self.conn, row_id)
        self.assertEqual(images[0]["tg_file_unique_id"], "u")

    def test_habit_streak(self):
        self.assertEqual(store.habit_streak(self.conn, -7), (None, 0))
        for i, category in enumerate(["news", "news", "crypto", "crypto", "crypto"]):
            row_id = self._insert(msg_id=100 + i, forward_origin_chat_id=-7,
                                  forward_origin_message_id=i, raw_text="x")
            store.confirm_category(self.conn, row_id, category)
        category, streak = store.habit_streak(self.conn, -7)
        self.assertEqual((category, streak), ("crypto", 3))


if __name__ == "__main__":
    unittest.main()
