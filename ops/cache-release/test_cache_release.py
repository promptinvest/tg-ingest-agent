"""Offline release contracts; all databases and receipts are synthetic."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import cache_release as release


class SchemaPreservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE llm_usage(id INTEGER PRIMARY KEY, kind TEXT,
                    model TEXT, tokens_in INTEGER, cost_usd REAL);
                CREATE INDEX usage_kind ON llm_usage(kind);
                INSERT INTO llm_usage VALUES(1, 'unknown_chat', 'model', 0, 0.5);
                INSERT INTO llm_usage VALUES(2, 'chat', 'model', 200, 0.1);
                CREATE TABLE preferences(key TEXT PRIMARY KEY, value TEXT);
                INSERT INTO preferences VALUES('budget_daily_usd', '2');
            """)
            for number in range(54):
                conn.execute(f'CREATE TABLE preserved_{number}(value BLOB)')
                conn.execute(f'INSERT INTO preserved_{number} VALUES (?)',
                             (sqlite3.Binary(bytes([number])),))
        self.before = release.database_snapshot(self.db)

    def migrate(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute('ALTER TABLE llm_usage ADD COLUMN cache_usage_json TEXT')

    def after(self):
        return release.database_snapshot(self.db, original=self.before)

    def test_named_nullable_column_preserves_every_original_value(self):
        self.migrate()
        result = release.verify_migration(self.before, self.after())
        self.assertEqual(result['table_count'], 56)
        self.assertEqual(result['changed_original_rows'], 0)
        self.assertEqual(result['historical_cache_values'], 0)
        self.assertEqual(result['unknown_rows'], 1)

    def test_original_unknown_charge_changed_is_rejected(self):
        self.migrate()
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE llm_usage SET cost_usd=0 WHERE id=1')
        with self.assertRaisesRegex(ValueError, 'original rows'):
            release.verify_migration(self.before, self.after())

    def test_backfill_is_rejected(self):
        self.migrate()
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE llm_usage SET cache_usage_json='{}' WHERE id=2")
        with self.assertRaisesRegex(ValueError, 'backfill'):
            release.verify_migration(self.before, self.after())

    def test_extra_table_is_rejected(self):
        self.migrate()
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE unrelated(id INTEGER)')
        with self.assertRaisesRegex(ValueError, '56|tables'):
            release.verify_migration(self.before, self.after())

    def test_extra_column_is_rejected(self):
        self.migrate()
        with sqlite3.connect(self.db) as conn:
            conn.execute('ALTER TABLE llm_usage ADD COLUMN hidden_change TEXT')
        with self.assertRaisesRegex(ValueError, 'column'):
            release.verify_migration(self.before, self.after())

    def test_nonnullable_column_is_rejected(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("ALTER TABLE llm_usage ADD COLUMN cache_usage_json TEXT NOT NULL DEFAULT ''")
        with self.assertRaisesRegex(ValueError, 'nullable|column'):
            release.verify_migration(self.before, self.after())

    def test_index_removal_is_rejected(self):
        self.migrate()
        with sqlite3.connect(self.db) as conn:
            conn.execute('DROP INDEX usage_kind')
        with self.assertRaisesRegex(ValueError, 'schema'):
            release.verify_migration(self.before, self.after())

    def test_unrelated_setting_change_is_rejected(self):
        self.migrate()
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE preferences SET value='20'")
        with self.assertRaisesRegex(ValueError, 'original rows'):
            release.verify_migration(self.before, self.after())

    def test_missing_migration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'column'):
            release.verify_migration(self.before, self.after())

    def test_snapshot_is_read_only_and_contains_no_row_values(self):
        raw = self.db.read_bytes()
        result = release.database_snapshot(self.db)
        self.assertEqual(raw, self.db.read_bytes())
        self.assertNotIn('budget_daily_usd', json.dumps(result))
        self.assertNotIn('unknown_chat', json.dumps(result))

    def test_wal_or_journal_is_not_silently_ignored(self):
        for suffix in ('-wal', '-journal'):
            pending = Path(str(self.db) + suffix)
            pending.write_bytes(b'pending')
            with self.assertRaisesRegex(ValueError, 'standalone'):
                release.database_snapshot(self.db)
            pending.unlink()


class PostTrialTests(unittest.TestCase):
    def prior(self):
        return {'trial_end': release.TRIAL_END, 'epochs': [{'source': 'old'}],
                'transition_gaps': [{'seconds': 1}], 'recorded_paused_intervals': []}

    def timers(self):
        return {name: {'active': False, 'enabled': False, 'unit_sha256': 'a' * 64}
                for name in release.OBSERVERS}

    def terminal(self):
        return {'final': True, 'absolute_end': release.TRIAL_END,
                'at': '2026-10-28T13:36:19Z', 'receipt_sha256': 'b' * 64}

    def test_expired_trial_is_preserved_without_new_epoch_or_timer(self):
        prior, timers = self.prior(), self.timers()
        original = copy.deepcopy((prior, timers))
        receipt = release.post_trial_contract(prior, timers, '2026-10-29T10:00:00+00:00', self.terminal())
        self.assertEqual((prior, timers), original)
        self.assertEqual(receipt['trial_end'], release.TRIAL_END)
        self.assertEqual(receipt['prior_continuation_sha256'], release.digest(prior))
        self.assertEqual(receipt['new_trial_epochs'], 0)
        self.assertEqual(receipt['observer_actions'], 0)

    def test_active_observer_is_not_silently_stopped(self):
        timers = self.timers()
        timers[release.OBSERVERS[-1]]['active'] = True
        with self.assertRaisesRegex(ValueError, 'observer'):
            release.post_trial_contract(self.prior(), timers, '2026-10-29T10:00:00Z', self.terminal())

    def test_missing_timer_evidence_is_rejected(self):
        timers = self.timers()
        timers.pop(release.OBSERVERS[0])
        with self.assertRaisesRegex(ValueError, 'observer'):
            release.post_trial_contract(self.prior(), timers, '2026-10-29T10:00:00Z', self.terminal())

    def test_changed_end_is_rejected(self):
        prior = self.prior()
        prior['trial_end'] = '2026-10-30T10:00:00Z'
        with self.assertRaisesRegex(ValueError, 'deadline'):
            release.post_trial_contract(prior, self.timers(), '2026-10-29T10:00:00Z', self.terminal())

    def test_before_original_end_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'completed'):
            release.post_trial_contract(self.prior(), self.timers(), '2026-10-28T10:00:00Z', self.terminal())

    def test_missing_final_receipt_is_not_implied_by_elapsed_time(self):
        with self.assertRaisesRegex(ValueError, 'terminal'):
            release.post_trial_contract(self.prior(), self.timers(), '2026-10-29T10:00:00Z')

    def test_enabled_but_stopped_final_timer_is_preserved(self):
        timers = self.timers()
        timers[release.OBSERVERS[-1]]['enabled'] = True
        receipt = release.post_trial_contract(self.prior(), timers, '2026-10-29T10:00:00Z', self.terminal())
        self.assertTrue(receipt['observer_states'][release.OBSERVERS[-1]]['enabled'])


class SourceDeltaTests(unittest.TestCase):
    def test_only_listed_runtime_changes_are_allowed(self):
        old = {'llm.py': b'old', 'router.py': b'router', 'store.py': b'store',
               'CARA.md': b'old', 'SOLUTION.md': b'old'}
        new = {**old, 'llm.py': b'new', 'cache_usage.py': b'new',
               'CARA.md': b'new', 'SOLUTION.md': b'new',
               'test_cache_usage.py': b'test'}
        result = release.verify_source_delta(old, new)
        self.assertEqual(result['runtime_added'], ['cache_usage.py'])
        new['router.py'] = b'changed prompt'
        with self.assertRaisesRegex(ValueError, 'runtime'):
            release.verify_source_delta(old, new)

    def test_deletion_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'removed'):
            release.verify_source_delta({'llm.py': b'old'}, {'cache_usage.py': b'new'})

    def test_unknown_runtime_addition_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'runtime'):
            release.verify_source_delta({}, {'new_agent.py': b'new'})

    def test_installer_must_preserve_original_list_and_add_module_once(self):
        name = 'install-tg-ingest-agent-pilot-remote.sh'
        old = {name: b'MODULES="llm.py store.py"\n', 'CARA.md': b'old', 'SOLUTION.md': b'old'}
        new = {**old, name: b'MODULES="llm.py cache_usage.py store.py"\n',
               'cache_usage.py': b'module', 'CARA.md': b'new', 'SOLUTION.md': b'new'}
        release.verify_source_delta(old, new)
        for bad in (b'MODULES="llm.py store.py"\n',
                    b'MODULES="llm.py cache_usage.py"\n',
                    b'MODULES="llm.py cache_usage.py store.py cache_usage.py"\n'):
            new[name] = bad
            with self.assertRaisesRegex(ValueError, 'installer'):
                release.verify_source_delta(old, new)


class GateReceiptTests(unittest.TestCase):
    def receipts(self):
        common = {'status': 'passed', 'source_commit': 'c' * 40, 'archive_sha256': 'd' * 64,
                  'model_calls': 0, 'messages_sent': 0}
        return {
            'linux_full': {**common, 'normal_passed': True, 'shuffled_passed': True,
                           'network_disabled': True, 'runs_as_root': True, 'original_tests_preserved': True},
            'ci': {**common, 'exact_head_passed': True},
            'installed_layout': {**common, 'installed_filename': 'agent.py',
                                 'source_entrypoint_absent': True, 'both_entry_modes_passed': True},
            'cache_accounting': {**common, 'malformed_missing_aliases_and_provider_semantics_passed': True,
                                 'unknown_reserves_and_history_preserved': True},
            'source_review': {**common, 'prompts_payloads_profiles_unchanged': True,
                              'budget_limits_and_commercial_overrides_unchanged': True},
        }

    def test_complete_exact_source_offline_gates_pass(self):
        self.assertEqual(set(release.verify_gate_receipts(self.receipts(), 'c' * 40, 'd' * 64)),
                         set(self.receipts()))

    def test_old_source_ci_cannot_qualify_new_candidate(self):
        receipts = self.receipts()
        receipts['ci']['source_commit'] = 'e' * 40
        with self.assertRaisesRegex(ValueError, 'source binding'):
            release.verify_gate_receipts(receipts, 'c' * 40, 'd' * 64)

    def test_missing_gate_is_not_waived(self):
        receipts = self.receipts()
        receipts.pop('installed_layout')
        with self.assertRaisesRegex(ValueError, 'complete'):
            release.verify_gate_receipts(receipts, 'c' * 40, 'd' * 64)

    def test_failed_or_unrun_gate_is_not_accepted(self):
        for change in ({'status': 'pending'}, {'model_calls': 1}, {'messages_sent': 1},
                       {'model_calls': False}, {'normal_passed': False}):
            receipts = self.receipts()
            receipts['linux_full'].update(change)
            with self.assertRaises(ValueError):
                release.verify_gate_receipts(receipts, 'c' * 40, 'd' * 64)

    def test_missing_accounting_failure_coverage_is_rejected(self):
        receipts = self.receipts()
        receipts['cache_accounting']['unknown_reserves_and_history_preserved'] = False
        with self.assertRaisesRegex(ValueError, 'failure coverage'):
            release.verify_gate_receipts(receipts, 'c' * 40, 'd' * 64)

    def test_prompt_or_budget_change_is_not_hidden_by_passing_tests(self):
        receipts = self.receipts()
        receipts['source_review']['prompts_payloads_profiles_unchanged'] = False
        with self.assertRaisesRegex(ValueError, 'scope review'):
            release.verify_gate_receipts(receipts, 'c' * 40, 'd' * 64)


if __name__ == '__main__':
    unittest.main()
