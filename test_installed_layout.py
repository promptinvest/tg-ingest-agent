"""Behavioral checks of the artifact that ships, including a broken-layout control."""
import ast
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import verify_installed_layout as smoke

ROOT = Path(__file__).resolve().parent
BROKEN_DELIVER_ACTION = '''def deliver_action(agent, key):
    import tg_ingest_agent
    for row in agent.conn.execute("SELECT * FROM reply_outbox WHERE action_key=? ORDER BY id", (key,)).fetchall():
        deliver(agent, row, tg_ingest_agent.tg_call)
'''


class InstalledLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cara-layout-test-')
        self.addCleanup(self.temp.cleanup)
        self.installed = Path(self.temp.name) / 'installed'
        self.installed.mkdir()
        for name, path in smoke.source_files(ROOT).items():
            shutil.copyfile(path, self.installed / name)

    def test_actual_packaged_bytes_work_as_agent_and_script_without_source_alias(self):
        report = smoke.qualify(source_root=ROOT, installed_root=self.installed)
        self.assertEqual(report['entrypoints'], ['agent', '__main__'])
        self.assertTrue(report['source_match'])
        self.assertTrue(report['installed_bytes_checked'])
        self.assertTrue(report['source_named_module_absent'])
        self.assertEqual(report['scenarios'], list(smoke.SCENARIOS))
        self.assertEqual(report['actual_model_calls'], 0)
        self.assertEqual(report['actual_telegram_calls'], 0)
        self.assertEqual(report['network_attempts'], 0)
        self.assertEqual(report['process_attempts'], 0)
        for result in report['results']:
            self.assertTrue(result['isolated'])
            self.assertTrue(result['bytecode_disabled'])
            self.assertEqual(result['mock_model_calls'], 1)
            self.assertGreaterEqual(result['mock_telegram_calls'], 5)

    def test_original_import_failure_is_caught_in_real_installed_process(self):
        path = self.installed / 'action_delivery.py'
        text = path.read_text(encoding='utf-8')
        node = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == 'deliver_action')
        lines = text.splitlines(keepends=True)
        path.write_text(''.join(lines[:node.lineno - 1]) + BROKEN_DELIVER_ACTION + ''.join(lines[node.end_lineno:]), encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'ModuleNotFoundError.*tg_ingest_agent'):
            smoke.qualify(installed_root=self.installed)

    def test_source_named_alias_cannot_hide_an_installed_import_bug(self):
        shutil.copyfile(ROOT / 'tg_ingest_agent.py', self.installed / 'tg_ingest_agent.py')
        with self.assertRaisesRegex(RuntimeError, 'omit source-named'):
            smoke.qualify(installed_root=self.installed)

    def test_installed_byte_mismatch_is_refused_before_behavioral_execution(self):
        with (self.installed / 'action_delivery.py').open('a', encoding='utf-8') as stream:
            stream.write('\n# synthetic post-copy byte mismatch\n')
        with mock.patch.object(smoke.subprocess, 'run') as child:
            with self.assertRaisesRegex(RuntimeError, 'bytes differ'):
                smoke.qualify(source_root=ROOT, installed_root=self.installed)
            child.assert_not_called()

    def test_offline_policy_refuses_native_uris_reads_and_host_mutations(self):
        runtime = Path(self.temp.name).resolve()
        policy = dict(runtime=runtime, read_roots=[runtime], script=runtime/'probe.py')
        cases = [('sqlite3.connect', ('file:/var/lib/tg-ingest-agent/ingest.db?mode=ro',)),
                 ('open', (str(runtime.parent/'cara-fleet-config.json'), 'r', 0)),
                 ('open', (str(runtime.parent/'secrets.env.extra'), 'r', 0)),
                 ('os.mkdir', (str(runtime.parent/'host-directory'), 0o700, -1)),
                 ('os.remove', (str(runtime.parent/'host-file'), -1)),
                 ('os.rename', (str(runtime/'source'), str(runtime.parent/'host-file'), -1, -1)),
                 ('os.chmod', (str(runtime.parent/'host-file'), 0o600, -1)),
                 ('os.symlink', ('/etc/passwd', str(runtime/'link'), -1))]
        for event, args in cases:
            with self.subTest(event=event, args=args), self.assertRaises(RuntimeError):
                smoke.offline_path_policy(event, args, **policy)
        smoke.offline_path_policy('sqlite3.connect', (str(runtime/'synthetic.db'),), **policy)
        smoke.offline_path_policy('open', (str(runtime/'result.json'), 'w', smoke.os.O_WRONLY | smoke.os.O_CREAT), **policy)

    def test_gate_runs_before_native_mutation_and_first_business_start(self):
        text = (ROOT / 'install-tg-ingest-agent-pilot-remote.sh').read_text(encoding='utf-8')
        source_gate = 'python3 -I -B "$STAGE_DIR/verify_installed_layout.py" --source-root "$STAGE_DIR"'
        copied_gate = '--source-root "$STAGE_DIR" --installed-root "$APP_DIR"'
        self.assertLess(text.index(source_gate), text.index('mkdir -p "$BACKUP_ROOT"'))
        self.assertLess(text.index('install -m 0755 "$STAGE_DIR/tg_ingest_agent.py" "$APP_DIR/agent.py"'), text.index(copied_gate))
        self.assertLess(text.index(copied_gate), text.index('systemctl restart "$WORKER_SERVICE.service"'))
        self.assertLess(text.index(copied_gate), text.index('systemctl restart "$SERVICE.service"'))
        self.assertIn('verify_installed_layout.py', text.split('MODULES="', 1)[1].split('"', 1)[0].split())

    def test_deploy_and_native_runtime_verifier_use_the_gate(self):
        deploy = (ROOT / 'deploy.sh').read_text(encoding='utf-8')
        self.assertIn("python3 -I -B verify_installed_layout.py --source-root '$STAGE'", deploy)
        self.assertLess(deploy.index("python3 -I -B '$SRC/verify_installed_layout.py'"),
                        deploy.index("STAGE_DIR='$SRC' bash '$SRC/install-tg-ingest-agent-pilot-remote.sh'"))
        native = (ROOT / 'verify-cara-runtime.sh').read_text(encoding='utf-8')
        self.assertIn('/usr/bin/python3 -I -B /opt/tg-ingest-agent/verify_installed_layout.py', native)
        self.assertIn('--installed-root /opt/tg-ingest-agent', native)
        self.assertIn('systemctl is-active --quiet tg-ingest-agent', native)


if __name__ == '__main__':
    unittest.main(verbosity=2)