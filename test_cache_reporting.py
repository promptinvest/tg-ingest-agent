"""Cache reporting must preserve historical accounting and request behavior."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import call_budget
import llm
import review
import spend
import store
from testlib import make_config


class CacheReportingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'usage.db'
        self.conn = store.open_db(self.path)
        self.cfg = make_config(BUDGET_DAILY_USD='100', BUDGET_MONTHLY_USD='100')

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _row(self, cache=None, *, model='deepseek-v4-pro', kind='chat', cost=.01):
        row = store.usage_add(self.conn, 'converse', kind, model, 1000, 10, cost_usd=cost)
        if cache is not None:
            self.conn.execute('UPDATE llm_usage SET cache_usage_json=? WHERE id=?',
                              (json.dumps(cache), row))
            self.conn.commit()
        return row

    def _cache(self, total=1000, read=500, cost=.001, savings=.0005):
        return dict(version=1, status='known', input_tokens=total, output_tokens=10,
                    cached_read_tokens=read, cached_write_tokens=0,
                    provider_cost_usd=cost, savings_usd=savings)

    def test_gateway_payload_unchanged_and_budget_does_not_spend_predicted_savings(self):
        messages = [{'role': 'system', 'content': 'Fixed instructions'},
                    {'role': 'user', 'content': 'Hello'}]
        prepared = llm.prepare_chat(self.cfg, self.conn, 'converse', messages,
                                    model='deepseek-v4-pro')
        self.assertEqual(json.loads(prepared['request'].data), dict(
            model='deepseek-v4-pro', messages=messages, max_tokens=300,
            temperature=0, stream=False))
        result = llm.finish_chat(self.cfg, self.conn, prepared, data={
            'model': 'deepseek-v4-pro', 'choices': [{'message': {'content': 'Hi'}}],
            'usage': {'prompt_tokens': 1000, 'completion_tokens': 10,
                      'cache_read_input_tokens': 500,
                      'prompt_tokens_details': {'cached_tokens': 500}}})
        self.assertTrue(result)
        row = self.conn.execute('SELECT * FROM llm_usage').fetchone()
        self.assertEqual(row['kind'], 'chat')
        self.assertEqual(row['tokens_in'], 1000)
        self.assertAlmostEqual(row['cost_usd'], llm.chat_cost(
            'deepseek-v4-pro', 1000, 10, llm.pricing_table(self.cfg)))
        cache = json.loads(row['cache_usage_json'])
        self.assertEqual(cache['cached_read_tokens'], 500)
        self.assertGreater(cache['savings_usd'], 0)
        self.assertLess(cache['provider_cost_usd'], row['cost_usd'])

    def test_missing_usage_keeps_reserved_unknown_charge(self):
        prepared = llm.prepare_chat(self.cfg, self.conn, 'converse',
                                    [{'role': 'user', 'content': 'hello'}])
        before = store.usage_total(self.conn, 'day')
        llm.finish_chat(self.cfg, self.conn, prepared,
                        data={'choices': [{'message': {'content': 'hi'}}]})
        row = self.conn.execute('SELECT * FROM llm_usage').fetchone()
        self.assertEqual(row['kind'], 'unknown_chat')
        self.assertGreaterEqual(row['cost_usd'], before)
        self.assertIsNone(json.loads(row['cache_usage_json'])['savings_usd'])

    def test_no_choices_still_records_billed_usage(self):
        prepared = llm.prepare_chat(self.cfg, self.conn, 'converse', [],
                                    model='deepseek-v4-pro')
        with self.assertRaises(llm.LLMError):
            llm.finish_chat(self.cfg, self.conn, prepared, data={'usage': {
                'prompt_tokens': 1000, 'completion_tokens': 10,
                'cache_read_input_tokens': 500}})
        self.assertGreater(store.usage_total(self.conn, 'day'), 0)
        self.assertEqual(store.usage_cache_summary(self.conn, 'day')['measured_calls'], 1)

    def test_migration_preserves_all_existing_usage_and_nulls_old_cache(self):
        self._row(kind='unknown_chat', cost=.8)
        self._row(cost=.04)
        self.conn.execute('ALTER TABLE llm_usage DROP COLUMN cache_usage_json')
        self.conn.commit()
        before = [tuple(r) for r in self.conn.execute('SELECT * FROM llm_usage ORDER BY id')]
        self.conn.close()
        self.conn = store.open_db(self.path)
        columns = [r[1] for r in self.conn.execute('PRAGMA table_info(llm_usage)')]
        old = ','.join(columns[:-1])
        self.assertEqual(before, [tuple(r) for r in self.conn.execute(
            'SELECT ' + old + ' FROM llm_usage ORDER BY id')])
        self.assertEqual([None, None], [r[0] for r in self.conn.execute(
            'SELECT cache_usage_json FROM llm_usage')])
        store._migrate(self.conn)
        self.assertAlmostEqual(store.usage_total(self.conn, 'day'), .84)

    def test_migration_column_rolls_back_if_later_step_fails(self):
        self.conn.execute('ALTER TABLE llm_usage DROP COLUMN cache_usage_json')
        self.conn.commit()
        with mock.patch.object(store, '_migrate_note_outcomes', side_effect=sqlite3.OperationalError('full')):
            with self.assertRaises(sqlite3.OperationalError):
                store._migrate(self.conn)
        self.assertNotIn('cache_usage_json', [r[1] for r in self.conn.execute(
            'PRAGMA table_info(llm_usage)')])

    def test_settlement_writes_cache_and_charge_atomically(self):
        row = self._row(kind='unknown_chat', cost=.8)
        self.conn.execute("CREATE TRIGGER reject_cache BEFORE UPDATE OF cache_usage_json ON llm_usage "
                          "BEGIN SELECT RAISE(ABORT, 'full'); END")
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            call_budget.settle(self.conn, row, 'chat', 1000, 10, cost_usd=.01,
                               cache_usage=self._cache())
        self.conn.rollback()
        saved = self.conn.execute('SELECT * FROM llm_usage WHERE id=?', (row,)).fetchone()
        self.assertEqual(saved['kind'], 'unknown_chat')
        self.assertEqual(saved['cost_usd'], .8)
        self.assertIsNone(saved['cache_usage_json'])

    def test_summary_has_weighted_reuse_and_separate_price_coverage(self):
        self._row(self._cache(total=1000, read=100))
        self._row(self._cache(total=3000, read=2700, cost=None, savings=None), model='other')
        self._row()  # old row; not a cache miss
        self._row(kind='unknown_chat', cost=.8)
        self._row(self._cache(), kind='embed')
        summary = store.usage_cache_summary(self.conn, 'day')
        self.assertEqual((summary['calls'], summary['measured_calls'], summary['priced_calls']), (4, 2, 1))
        self.assertEqual((summary['input_tokens'], summary['cached_read_tokens']), (4000, 2800))
        self.assertAlmostEqual(summary['savings_usd'], .0005)
        for language in ('en', 'ru'):
            text = spend.format_spend(self.conn, 'day', self.cfg, language)
            self.assertIn('2/4', text)
            self.assertIn('70.0%', text)
            self.assertIn('1/4', text)
        self.assertIn('estimated', spend.format_spend(self.conn, 'day', self.cfg, 'en').lower())

    def test_negative_savings_are_visible_and_never_subtracted_from_budget(self):
        self._row(self._cache(savings=-.001, cost=.002))
        text = spend.format_spend(self.conn, 'day', self.cfg, 'en')
        self.assertIn('-0.001', text)
        self.assertEqual(store.usage_total(self.conn, 'day'), .01)

    def test_corrupt_metadata_and_legacy_rows_remain_unmeasured(self):
        for value in ([], {'version': 99, 'status': 'known'}, {'version': 1, 'status': 'known',
                       'input_tokens': True, 'cached_read_tokens': 1}):
            self._row(value)
        self._row()
        summary = store.usage_cache_summary(self.conn, 'day')
        self.assertEqual(summary['calls'], 4)
        self.assertEqual(summary['measured_calls'], 0)
        self.assertIn('0/4', spend.format_spend(self.conn, 'day', self.cfg, 'en'))

    def test_frozen_estimates_do_not_follow_later_config_prices(self):
        self._row(self._cache(cost=.001, savings=.0005))
        self.cfg.pricing_json = '{"deepseek-v4-pro":[99,99]}'
        summary = store.usage_cache_summary(self.conn, 'day')
        self.assertEqual(summary['provider_cost_usd'], .001)
        self.assertEqual(summary['savings_usd'], .0005)

    def test_review_uses_rolling_window_and_spend_uses_calendar_window(self):
        row = self._row(self._cache())
        self.conn.execute("UPDATE llm_usage SET ts='2026-10-25T12:00:00+00:00',"
                          " day='2026-10-25',month='2026-10' WHERE id=?", (row,))
        self.conn.commit()
        with mock.patch.object(store, '_now', return_value='2026-10-29T12:00:00+00:00'):
            self.assertEqual(store.usage_cache_summary(self.conn, 'week')['calls'], 0)
            self.assertEqual(store.usage_cache_summary(self.conn,
                since='2026-10-22T12:00:00+00:00')['calls'], 1)
        data = review.collect(self.conn, 'week')
        self.assertIn('cache_usage', data)
        self.assertIn('Prompt cache', review.markdown(self.conn, self.cfg, 'week'))

    def test_reconciled_open_model_rates_keep_commercial_overrides(self):
        cfg = make_config(PRICING_JSON='{"anthropic-claude-5.5-haiku":[0.5,2.5],"openai-gpt-6-1-sol":[4,15]}')
        table = llm.pricing_table(cfg)
        self.assertEqual(table['deepseek-v4-pro'], (1.74, 3.48))
        self.assertEqual(table['deepseek-4-flash'], (.14, .28))
        self.assertEqual(table['deepseek-3.2'], (.50, 1.60))
        self.assertEqual(table['kimi-k2.6'], (.95, 4.0))
        self.assertEqual(table['anthropic-claude-5.5-haiku'], (.5, 2.5))
        self.assertEqual(table['openai-gpt-6-1-sol'], (4., 15.))


if __name__ == '__main__':
    unittest.main()
