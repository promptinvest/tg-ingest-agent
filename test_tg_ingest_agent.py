#!/usr/bin/env python3
"""Offline unit tests for tg_ingest_agent (no network, temp SQLite)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tg_ingest_agent as agent


def make_config(**overrides):
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "ALLOWED_CHAT_IDS": "111,222",
        "DO_MODEL_ACCESS_KEY": "do-key",
        "CATEGORIES": "news,tools,jobs",
    }
    env.update(overrides)
    return agent.load_config(env)


class ConfigTests(unittest.TestCase):
    def test_config_list_separators(self):
        self.assertEqual(agent.config_list("a, b|c;d\ne"), ["a", "b", "c", "d", "e"])
        self.assertEqual(agent.config_list(""), [])
        self.assertEqual(agent.config_list(None), [])

    def test_parse_chat_ids(self):
        self.assertEqual(agent.parse_chat_ids("111, -100222"), {111, -100222})
        with self.assertRaises(SystemExit):
            agent.parse_chat_ids("111,abc")

    def test_load_config_defaults_and_fallback(self):
        cfg = make_config()
        self.assertEqual(cfg.allowed_chat_ids, {111, 222})
        self.assertEqual(cfg.do_model, "anthropic-claude-haiku-4.5")
        self.assertEqual(cfg.poll_timeout, 50)
        # fallback category appended because not in the list
        self.assertIn("uncategorized", cfg.categories)
        cfg2 = make_config(CATEGORIES="news,other", FALLBACK_CATEGORY="Other")
        # case-insensitive match: no duplicate appended
        self.assertEqual([c.casefold() for c in cfg2.categories].count("other"), 1)

    def test_load_config_required(self):
        for missing in ("TELEGRAM_BOT_TOKEN", "ALLOWED_CHAT_IDS", "DO_MODEL_ACCESS_KEY", "CATEGORIES"):
            with self.assertRaises(SystemExit):
                make_config(**{missing: ""})

    def test_categories_file_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "categories.txt"
            path.write_text("alpha\n# comment\n\nbeta\n", encoding="utf-8")
            cfg = make_config(CATEGORIES="ignored", CATEGORIES_FILE=str(path))
            self.assertEqual(cfg.categories[:2], ["alpha", "beta"])


class UrlExtractionTests(unittest.TestCase):
    def test_utf16_offsets_with_emoji_and_cyrillic(self):
        # Emoji is 2 UTF-16 code units; naive Python slicing would be off by one.
        text = "\U0001F600 Привет https://example.com/a конец"
        offset = len("\U0001F600 Привет ".encode("utf-16-le")) // 2
        url = "https://example.com/a"
        entities = [{"type": "url", "offset": offset, "length": len(url)}]
        self.assertIn(url, agent.extract_urls(text, entities))

    def test_text_link_and_regex_fallback_dedup(self):
        text = "see https://a.example/x and that's it."
        entities = [
            {"type": "text_link", "url": "https://b.example/y"},
            {"type": "url", "offset": 4, "length": len("https://a.example/x")},
        ]
        urls = agent.extract_urls(text, entities)
        self.assertEqual(urls, ["https://b.example/y", "https://a.example/x"])

    def test_regex_only(self):
        urls = agent.extract_urls("plain http://c.example/z, tail", None)
        self.assertEqual(urls, ["http://c.example/z"])


class LlmParsingTests(unittest.TestCase):
    def test_parse_clean_json(self):
        self.assertEqual(agent.parse_llm_json('{"category": "news"}'), {"category": "news"})

    def test_parse_fenced_json(self):
        text = 'Here you go:\n```json\n{"category": "news", "summary": "s"}\n```'
        self.assertEqual(agent.parse_llm_json(text)["category"], "news")

    def test_parse_prose_wrapped(self):
        text = 'Sure! {"category": "tools", "summary": "x"} hope that helps'
        self.assertEqual(agent.parse_llm_json(text)["category"], "tools")

    def test_parse_garbage(self):
        self.assertIsNone(agent.parse_llm_json("no json here"))
        self.assertIsNone(agent.parse_llm_json(""))
        self.assertIsNone(agent.parse_llm_json('["list", "not", "dict"]'))

    def test_validate_category(self):
        cats = ["News", "Tools"]
        self.assertEqual(agent.validate_category("news", cats), "News")
        self.assertEqual(agent.validate_category(" TOOLS ", cats), "Tools")
        self.assertIsNone(agent.validate_category("nope", cats))
        self.assertIsNone(agent.validate_category(None, cats))


class PromptTests(unittest.TestCase):
    def test_build_text_block(self):
        block = agent.build_text_block("hello", "channel", "Some Channel", ["https://a.example"])
        self.assertIn("Forwarded from channel: Some Channel", block)
        self.assertIn("hello", block)
        self.assertIn("- https://a.example", block)
        block2 = agent.build_text_block(None, None, None, [])
        self.assertIn("(no text)", block2)
        self.assertNotIn("Forwarded", block2)

    def test_build_llm_messages_image_cap_and_oversize(self):
        cfg = make_config(MAX_LLM_IMAGES="2")
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(3):
                p = Path(tmp) / f"img{i}.jpg"
                p.write_bytes(b"\xff\xd8" + bytes(10))
                paths.append(str(p))
            big = Path(tmp) / "big.jpg"
            big.write_bytes(b"x" * (agent.MAX_LLM_IMAGE_BYTES + 1))
            messages = agent.build_llm_messages(cfg, "text", [str(big)] + paths)
            content = messages[1]["content"]
            image_parts = [c for c in content if c.get("type") == "image_url"]
            self.assertEqual(len(image_parts), 2)  # oversize skipped, cap respected
            self.assertIn("news, tools, jobs", messages[0]["content"])

    def test_classify_corrective_retry_and_fallback(self):
        cfg = make_config()
        with mock.patch.object(agent, "do_chat", side_effect=["garbage", '{"category": "news", "summary": "ok"}']):
            self.assertEqual(agent.classify(cfg, "t", []), ("news", "ok"))
        with mock.patch.object(agent, "do_chat", side_effect=["garbage", "still garbage"]):
            category, summary = agent.classify(cfg, "t", [])
            self.assertEqual(category, cfg.fallback_category)
            self.assertEqual(summary, "still garbage")
        with mock.patch.object(agent, "do_chat", side_effect=['{"category": "bogus", "summary": "s"}'] * 2):
            category, _ = agent.classify(cfg, "t", [])
            self.assertEqual(category, cfg.fallback_category)


class ForwardOriginTests(unittest.TestCase):
    def test_channel(self):
        info = agent.parse_forward_origin(
            {"type": "channel", "date": 5, "message_id": 42,
             "chat": {"id": -100123, "title": "My Channel"}}
        )
        self.assertEqual(info["chat_id"], -100123)
        self.assertEqual(info["title"], "My Channel")
        self.assertEqual(info["message_id"], 42)

    def test_hidden_user_and_empty(self):
        info = agent.parse_forward_origin({"type": "hidden_user", "sender_user_name": "Bob", "date": 1})
        self.assertEqual(info["title"], "Bob")
        self.assertEqual(agent.parse_forward_origin(None), {})

    def test_first_text_and_collect_urls(self):
        parts = [
            {"photo": [], "caption": ""},
            {"caption": "caption here", "caption_entities": [
                {"type": "text_link", "url": "https://x.example"}]},
        ]
        self.assertEqual(agent.first_text(parts), "caption here")
        self.assertEqual(agent.collect_urls(parts), ["https://x.example"])


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = agent.open_db(Path(self.tmp.name) / "test.db")

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
        return agent.insert_message(self.conn, fields)

    def test_kv_roundtrip(self):
        self.assertIsNone(agent.kv_get(self.conn, "offset"))
        agent.kv_set(self.conn, "offset", 7)
        self.assertEqual(agent.kv_get(self.conn, "offset"), "7")

    def test_insert_dedup(self):
        first = self._insert()
        self.assertIsNotNone(first)
        self.assertIsNone(self._insert())  # redelivery is a no-op

    def test_forward_duplicate_flow(self):
        original = self._insert(msg_id=10, forward_origin_chat_id=-5, forward_origin_message_id=99)
        agent.set_classification(self.conn, original, "news", "summary", "model-x")
        dup = self._insert(msg_id=11, forward_origin_chat_id=-5, forward_origin_message_id=99)
        found = agent.find_forward_duplicate(self.conn, -5, 99, dup)
        self.assertEqual(found["id"], original)
        agent.mark_duplicate(self.conn, dup, found)
        row = agent.get_message(self.conn, dup)
        self.assertEqual(row["duplicate_of"], original)
        self.assertEqual(row["category"], "news")
        self.assertEqual(row["status"], "classified")
        # duplicates are excluded from later duplicate lookups and retry sweeps
        self.assertEqual(agent.find_forward_duplicate(self.conn, -5, 99, 999)["id"], original)
        self.assertEqual(agent.pending_messages(self.conn, 5), [])

    def test_pending_and_failure_path(self):
        row_id = self._insert(msg_id=20, raw_text="hello")
        self.assertEqual([r["id"] for r in agent.pending_messages(self.conn, 5)], [row_id])
        for expected in (1, 2):
            self.assertEqual(agent.bump_attempts(self.conn, row_id), expected)
        self.assertEqual(agent.pending_messages(self.conn, 2), [])  # attempts exhausted
        agent.mark_failed(self.conn, row_id)
        self.assertEqual(agent.get_message(self.conn, row_id)["status"], "failed")

    def test_urls_images_and_stats(self):
        row_id = self._insert(msg_id=30)
        agent.insert_url(self.conn, row_id, "https://a.example")
        photo = {"file_id": "f", "file_unique_id": "u", "width": 10, "height": 20, "file_size": 5}
        agent.insert_image(self.conn, row_id, 30, photo, "/tmp/u.jpg")
        self.assertEqual([r["url"] for r in agent.message_urls(self.conn, row_id)], ["https://a.example"])
        images = agent.message_images(self.conn, row_id)
        self.assertEqual(images[0]["tg_file_unique_id"], "u")
        agent.set_classification(self.conn, row_id, "news", "s", "m")
        text = agent.stats_text(self.conn)
        self.assertIn("classified: 1", text)
        self.assertIn("news: 1", text)


if __name__ == "__main__":
    unittest.main()
