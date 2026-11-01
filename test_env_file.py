"""Synthetic environment files retain each operator entry point's write contract."""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import apply_do_key
import apply_token
import bootstrap_chat_id
import env_file


ENTRY_POINTS = (apply_token, apply_do_key, bootstrap_chat_id)


class EnvFileCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "synthetic.env"

    def test_replacements_append_and_literal_values_keep_original_text(self):
        cases = (
            ("K=old\nOTHER=keep\n", "K", r"a\1b", "K=a\\1b\nOTHER=keep\n"),
            ("OTHER=keep\n\n", "K", 7, "OTHER=keep\nK=7\n"),
            ("", "K", "value", "\nK=value\n"),
            ("A.B=old\nAxB=keep\n", "A.B", "new", "A.B=new\nAxB=keep\n"),
            ("# K=comment\nK=old", "K", "new", "# K=comment\nK=new"),
        )
        for entry in (env_file, *ENTRY_POINTS):
            for content, key, value, expected in cases:
                with self.subTest(entry=entry.__name__, content=content):
                    self.assertEqual(entry.set_env_value(content, key, value), expected)
            with self.subTest(entry=entry.__name__, duplicates=True):
                with self.assertRaisesRegex(ValueError, "K is assigned 2 times; fix the file by hand"):
                    entry.set_env_value("K=one\nK=two\n", "K", "new")
        self.assertEqual(bootstrap_chat_id.write_owner("ALLOWED_CHAT_IDS=old\n", "7"),
                         "ALLOWED_CHAT_IDS=7\n")

    def test_success_returns_path_and_keeps_private_original_backup(self):
        original = "K=old\nOTHER=keep\n"
        backup = self.path.with_name(self.path.name + ".bak")
        for entry in (env_file, *ENTRY_POINTS):
            with self.subTest(entry=entry.__name__):
                self.path.write_text(original, encoding="utf-8")
                self.path.chmod(0o644)
                backup.write_text("earlier backup\n", encoding="utf-8")
                backup.chmod(0o644)
                returned = entry.write_env_atomically(str(self.path), "K=new\nOTHER=keep\n")
                self.assertEqual(returned, self.path)
                self.assertEqual(self.path.read_text(encoding="utf-8"), "K=new\nOTHER=keep\n")
                self.assertEqual(backup.read_text(encoding="utf-8"), original)
                self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
                self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())

    def test_a_new_file_needs_no_backup(self):
        returned = env_file.write_env_atomically(self.path, "K=first\n")
        self.assertEqual(returned, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "K=first\n")
        self.assertFalse(self.path.with_name(self.path.name + ".bak").exists())

    def test_stale_temp_refusal_leaves_original_and_stale_bytes_untouched(self):
        stale = self.path.with_name(self.path.name + ".tmp")
        for entry in (env_file, *ENTRY_POINTS):
            with self.subTest(entry=entry.__name__):
                self.path.write_text("K=original\n", encoding="utf-8")
                stale.write_text("interrupted write\n", encoding="utf-8")
                with self.assertRaises(SystemExit) as caught:
                    entry.write_env_atomically(self.path, "K=replacement\n")
                self.assertIn(str(stale), str(caught.exception))
                self.assertIn("rm -f", str(caught.exception))
                self.assertEqual(self.path.read_text(encoding="utf-8"), "K=original\n")
                self.assertEqual(stale.read_text(encoding="utf-8"), "interrupted write\n")
                stale.unlink()

    def test_existing_global_os_failure_mocks_still_preserve_original(self):
        for entry in (env_file, *ENTRY_POINTS):
            for operation in ("fsync", "replace"):
                with self.subTest(entry=entry.__name__, operation=operation):
                    self.path.write_text("K=original\n", encoding="utf-8")
                    # Existing regression tests patch the global os module. Shared
                    # helpers must retain that boundary and propagate the failure.
                    with mock.patch("os." + operation, side_effect=OSError("synthetic failure")):
                        with self.assertRaisesRegex(OSError, "synthetic failure"):
                            entry.write_env_atomically(self.path, "K=replacement\n")
                    self.assertEqual(self.path.read_text(encoding="utf-8"), "K=original\n")
                    self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())

    def test_only_bootstrap_syncs_directory_and_tolerates_directory_fsync_denial(self):
        real_fsync = os.fsync
        for deny_directory in (False, True):
            for entry in ENTRY_POINTS:
                self.path.write_text("K=original\n", encoding="utf-8")
                kinds = []

                def sync(fd):
                    is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                    kinds.append("directory" if is_directory else "file")
                    if is_directory and deny_directory:
                        raise OSError("directory fsync unsupported")
                    return real_fsync(fd)

                with self.subTest(entry=entry.__name__, deny_directory=deny_directory):
                    with mock.patch("os.fsync", side_effect=sync):
                        self.assertEqual(entry.write_env_atomically(self.path, "K=new\n"), self.path)
                    expected = ["file", "file"]
                    if entry is bootstrap_chat_id:
                        expected.append("directory")
                    self.assertEqual(kinds, expected)
                    self.assertEqual(self.path.read_text(encoding="utf-8"), "K=new\n")


if __name__ == "__main__":
    unittest.main()
