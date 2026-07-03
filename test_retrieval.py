#!/usr/bin/env python3
"""Offline tests for the retrieval-storage optimization: packed float32 BLOB
embeddings, the legacy JSON->BLOB migration, and the decoded-vector cache."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import knowledge
import store


class PackUnpackTests(unittest.TestCase):
    def test_roundtrip_float32(self):
        vec = [0.1, -0.2, 0.3, 1.0, 0.0]
        out = store.unpack_embedding(store.pack_embedding(vec))
        self.assertEqual(len(out), len(vec))
        for a, b in zip(vec, out):
            self.assertAlmostEqual(a, b, places=5)  # float32 precision

    def test_blob_is_compact(self):
        vec = [0.123] * 1024
        blob = store.pack_embedding(vec)
        self.assertIsInstance(blob, (bytes, bytearray))
        self.assertEqual(len(blob), 1024 * 4)               # 4 bytes/dim float32
        self.assertLess(len(blob), len(json.dumps(vec)))    # smaller than JSON text

    def test_tolerant_decode(self):
        self.assertIsNone(store.pack_embedding(None))
        self.assertIsNone(store.unpack_embedding(None))
        self.assertEqual(store.unpack_embedding(json.dumps([1.0, 2.0])), [1.0, 2.0])  # legacy
        self.assertIsNone(store.unpack_embedding("not-json"))


class StorageFormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.open_db(Path(self.tmp.name) / "r.db")

    def tearDown(self):
        store.invalidate_vector_cache(self.conn)
        self.conn.close()
        self.tmp.cleanup()

    _next_tg_id = 0

    def _note_with_chunks(self, pairs):
        StorageFormatTests._next_tg_id += 1
        mid = store.insert_message(self.conn, {"chat_id": 111,
                                               "tg_message_id": StorageFormatTests._next_tg_id,
                                               "received_at": store._now(),
                                               "raw_text": "note"})
        store.set_chunks(self.conn, mid, pairs)
        return mid

    def test_embeddings_stored_as_blob(self):
        self._note_with_chunks([("ужин у реки", [1.0, 0.0, 0.5])])
        t = self.conn.execute(
            "SELECT typeof(embedding) t FROM chunks WHERE embedding IS NOT NULL"
        ).fetchone()["t"]
        self.assertEqual(t, "blob")

    def test_decoded_accessor_returns_vec_and_meta(self):
        self._note_with_chunks([("ужин у реки", [1.0, 0.0, 0.5])])
        rows = store.all_embedded_chunks(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertIn("vec", rows[0])
        self.assertAlmostEqual(rows[0]["vec"][0], 1.0, places=5)

    def test_legacy_json_migrated_to_blob(self):
        # Simulate an old row written as JSON text, then run the migration.
        mid = store.insert_message(self.conn, {"chat_id": 111, "tg_message_id": 2,
                                               "received_at": store._now(),
                                               "raw_text": "note"})
        self.conn.execute(
            "INSERT INTO chunks (message_id, chunk_index, text, embedding)"
            " VALUES (?, 0, ?, ?)", (mid, "вечер", json.dumps([0.2, 0.4, 0.6])))
        self.conn.commit()
        self.assertEqual(self.conn.execute(
            "SELECT typeof(embedding) t FROM chunks").fetchone()["t"], "text")
        store._migrate(self.conn)                       # the one-time conversion
        self.assertEqual(self.conn.execute(
            "SELECT typeof(embedding) t FROM chunks").fetchone()["t"], "blob")
        rows = store.all_embedded_chunks(self.conn)
        self.assertAlmostEqual(rows[0]["vec"][1], 0.4, places=5)  # still decodes

    def test_cache_reuses_then_refreshes_on_mutation(self):
        self._note_with_chunks([("a", [1.0, 0.0])])
        self._note_with_chunks([("b", [0.0, 1.0])])
        a = store.all_embedded_chunks(self.conn)
        b = store.all_embedded_chunks(self.conn)
        self.assertIs(a, b)                              # cache hit -> same object
        self.assertEqual(len(a), 2)
        # a new chunk changes the (count,max_id,sum_id) fingerprint -> cache refreshes
        self._note_with_chunks([("c", [0.5, 0.5])])
        c = store.all_embedded_chunks(self.conn)
        self.assertIsNot(c, a)
        self.assertEqual(len(c), 3)

    def test_rank_works_over_blob_backed_rows(self):
        self._note_with_chunks([("flight 14", [1.0, 0.0])])
        self._note_with_chunks([("weather", [0.0, 1.0])])
        rows = store.all_embedded_chunks(self.conn)
        ranked = knowledge.rank_chunks([1.0, 0.05], rows, 6, 6000)  # decodes via `vec`
        self.assertTrue(ranked)
        self.assertEqual(ranked[0]["text"], "flight 14")     # nearest first


if __name__ == "__main__":
    unittest.main()
