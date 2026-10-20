import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import evidence
import run_scenario as runner


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='scenario space ')
        self.addCleanup(self.temp.cleanup)
        self.task = Path(self.temp.name)
        self.repo = self.task / 'repo'
        self.repo.mkdir()
        (self.task / 'scenario').mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-c', 'user.name=test', '-c', 'user.email=test@localhost',
                        'commit', '--allow-empty', '-qm', 'fixture'], cwd=self.repo, check=True)
        (self.repo / 'new.txt').write_text('untracked code')
        (self.repo / '.gitignore').write_text('ignored/\n')
        (self.repo / 'ignored').mkdir()
        (self.repo / 'ignored/secret').write_text('not source')
        (self.task / 'tools-config.json').write_text(json.dumps({'shared': str(self.task / 'shared')}))
        (self.task / 'scenario/manifest.json').write_text(json.dumps({
            'schema': 1, 'title': 'example', 'data': 'fixed fixture', 'steps': ['change input'],
            'coverage': ['value'], 'files': ['scenario/test.txt']}))
        (self.task / 'scenario/test.txt').write_text('assertion v1')
        (self.task / 'scenario/env.sh').write_text('export FIXTURE_ENV=ready\n')
        (self.task / 'scenario/check.sh').write_text('test "$FIXTURE_ENV" = ready\n')
        self.write_run()

    def write_run(self, check='failed', evidence_path='logs/events.json'):
        facts = {'schema': 1, 'observations': [{'name': 'position', 'value': {'before': 10, 'after': 0}, 'evidence': [evidence_path]}],
                 'checks': [{'name': 'position preserved', 'status': check, 'evidence': ['logs/events.json']}], 'limitations': []}
        script = 'printf \'%s\' \'{"before":10,"after":0}\' > "$MAKE_BUG_RUN_DIR/logs/events.json"\n'
        script += 'cat > "$MAKE_BUG_OBSERVATIONS" <<\'JSON\'\n' + json.dumps(facts) + '\nJSON\nexit 0\n'
        (self.task / 'scenario/run.sh').write_text(script)

    def run_once(self, timeout=5):
        with contextlib.redirect_stdout(io.StringIO()):
            return runner.run_once(self.task, timeout)

    def test_failed_and_passed_checks_both_complete_without_baseline(self):
        first, a = self.run_once()
        self.write_run('passed')
        second, b = self.run_once()
        self.assertNotEqual(first, second)
        self.assertEqual(a['execution']['status'], 'completed')
        self.assertEqual(b['execution']['status'], 'completed')
        self.assertEqual(a['checks'][0]['status'], 'failed')
        self.assertEqual(b['review'], {'status': 'pending'})
        self.assertNotEqual(a['scenario']['digest'], b['scenario']['digest'])
        self.assertEqual(json.loads((first / 'record.json').read_text()), a)
        self.assertFalse((self.task / 'scenario/baseline.json').exists())

    def test_snapshot_restores_added_changed_deleted_files_modes_and_symlinks(self):
        (self.repo / 'tracked').write_text('old')
        (self.repo / 'deleted').write_text('gone')
        subprocess.run(['git', 'add', 'tracked', 'deleted'], cwd=self.repo, check=True)
        subprocess.run(['git', '-c', 'user.name=test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'tracked'], cwd=self.repo, check=True)
        (self.repo / 'tracked').write_text('new')
        (self.repo / 'tracked').chmod(0o755)
        (self.repo / 'deleted').unlink()
        (self.repo / 'link').symlink_to('tracked')
        run, record = self.run_once()
        with tarfile.open(run / 'code/source.tar.gz') as archive:
            self.assertNotIn('ignored/secret', archive.getnames())
            self.assertNotIn('deleted', archive.getnames())
            self.assertEqual(archive.extractfile('new.txt').read(), b'untracked code')
            self.assertEqual(archive.extractfile('tracked').read(), b'new')
            self.assertEqual(archive.getmember('tracked').mode, 0o755)
            self.assertEqual(archive.getmember('link').linkname, 'tracked')
        self.assertEqual(record['code']['files']['deleted']['kind'], 'missing')
        restored = self.task / 'restored'
        restored.mkdir()
        with tarfile.open(run / 'code/source.tar.gz') as archive:
            archive.extractall(restored, filter='data')
        self.assertEqual(evidence.snapshot(restored, record['code']['files'])['digest'], record['code']['digest'])

    def test_environment_blocked_preserves_logs_and_skips_run(self):
        (self.task / 'scenario/check.sh').write_text('echo missing-browser\nexit 1\n')
        (self.task / 'scenario/run.sh').write_text('touch ran\n')
        run, record = self.run_once()
        self.assertEqual(record['execution']['status'], 'blocked')
        self.assertFalse((self.task / 'ran').exists())
        self.assertIn('missing-browser', (run / 'logs/check.log').read_text())
        self.assertTrue((run / 'REPORT.md').exists())

    def test_each_finished_run_requests_partial_report_without_writing_it(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                if blocked:
                    (self.task / 'scenario/check.sh').write_text('exit 1\n')
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    _, record = runner.run_once(self.task, 5)
                self.assertIn('先更新', output.getvalue())
                summary = json.loads(output.getvalue().splitlines()[-1])
                self.assertEqual(summary['report_update']['run_id'], record['run_id'])
                self.assertEqual(summary['report_update']['report'], 'result/REPRO.md')
                self.assertIn('尚未确认', summary['report_update']['instruction'])
                self.assertFalse((self.task / 'result/REPRO.md').exists())

    def test_error_retains_partial_observations(self):
        p = self.task / 'scenario/run.sh'
        p.write_text(p.read_text().replace('exit 0', 'exit 9'))
        _, record = self.run_once()
        self.assertEqual(record['execution']['status'], 'error')
        self.assertIsNotNone(record['observations'])

    def test_missing_evidence_is_warning_not_fake_success(self):
        self.write_run(evidence_path='videos/missing.webm')
        _, record = self.run_once()
        self.assertEqual(record['execution']['status'], 'completed')
        self.assertTrue(any('missing.webm' in w for w in record['evidence']['warnings']))
        self.assertEqual(record['review']['status'], 'pending')

    def test_missing_and_malformed_observations_are_delivery_errors(self):
        for script in ('exit 0', 'echo "[]" > "$MAKE_BUG_OBSERVATIONS"'):
            (self.task / 'scenario/run.sh').write_text(script)
            _, record = self.run_once()
            self.assertEqual(record['execution']['status'], 'error')
            self.assertEqual(record['execution']['phase'], 'collect')

    def test_invalid_manifest_leaves_report(self):
        for data in ([], {'schema': 1}, {'schema': 0}):
            (self.task / 'scenario/manifest.json').write_text(json.dumps(data))
            run, record = self.run_once()
            self.assertEqual(record['execution']['status'], 'error')
            self.assertTrue((run / 'record.json').is_file())

    def test_manifest_path_escape_rejected(self):
        p = self.task / 'scenario/manifest.json'
        value = json.loads(p.read_text())
        value['files'] = ['../outside']
        p.write_text(json.dumps(value))
        _, record = self.run_once()
        self.assertEqual(record['execution']['status'], 'error')

    def test_source_change_records_warning_and_keeps_original_snapshot(self):
        p = self.task / 'scenario/run.sh'
        p.write_text('echo changed > "$MAKE_BUG_TASK/repo/new.txt"\n' + p.read_text())
        run, record = self.run_once()
        self.assertEqual(record['execution']['status'], 'completed')
        self.assertTrue(any('code' in w for w in record['integrity_warnings']))
        with tarfile.open(run / 'code/source.tar.gz') as archive:
            self.assertEqual(archive.extractfile('new.txt').read(), b'untracked code')

    def test_timeout_cleans_child_and_keeps_partial_material(self):
        (self.task / 'scenario/run.sh').write_text('echo partial > "$MAKE_BUG_RUN_DIR/logs/partial.log"\nsleep 30 &\necho $! > child.pid\nwait\n')
        run, record = self.run_once(0.2)
        self.assertEqual(record['execution']['status'], 'error')
        self.assertIn('超时', record['execution']['reason'])
        self.assertTrue((run / 'logs/partial.log').exists())
        pid = int((self.task / 'child.pid').read_text())
        path = Path(f'/proc/{pid}/stat')
        if path.exists():
            self.assertEqual(path.read_text().split()[2], 'Z')

    def test_interrupt_has_distinct_status(self):
        with patch.object(runner, 'execute', side_effect=KeyboardInterrupt):
            run, record = self.run_once()
        self.assertEqual(record['execution']['status'], 'interrupted')
        self.assertTrue((run / 'REPORT.md').exists())

    def test_evidence_symlink_outside_not_indexed(self):
        p = self.task / 'scenario/run.sh'
        p.write_text('ln -s /etc/passwd "$MAKE_BUG_RUN_DIR/logs/external"\n' + p.read_text())
        _, record = self.run_once()
        self.assertNotIn('logs/external', [x['path'] for x in record['evidence']['artifacts']])
        self.assertTrue(record['evidence']['warnings'])

    def test_repeat_continues_after_failed_check_and_stops_on_execution_failure(self):
        statuses = ['completed', 'completed', 'blocked']
        with patch.object(sys, 'argv', ['run_scenario.py', '--repeat', '3']), patch.object(runner, '_run_once', side_effect=[
            (None, {'execution': {'status': s}}) for s in statuses]) as run:
            self.assertEqual(runner.main(self.task), 2)
            self.assertEqual(run.call_count, 3)

    def test_repeat_shares_one_command_budget(self):
        with patch.object(sys, 'argv', ['run_scenario.py', '--repeat', '3', '--timeout', '300']), \
                patch.object(runner.time, 'monotonic', side_effect=[100, 100, 220, 350]), \
                patch.object(runner, '_run_once', return_value=(None, {'execution': {'status': 'completed'}})) as run:
            self.assertEqual(runner.main(self.task), 0)
            self.assertEqual([call.args[1] for call in run.call_args_list], [300, 180, 50])

    def test_old_parameters_rejected(self):
        for flag in ('--baseline', '--prepare'):
            with patch.object(sys, 'argv', ['run_scenario.py', flag]), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                runner.main(self.task)
            self.assertEqual(exc.exception.code, 2)

    def test_scenario_change_during_run_keeps_archive_and_warns(self):
        p = self.task / 'scenario/run.sh'
        p.write_text('echo new > "$MAKE_BUG_TASK/scenario/test.txt"\n' + p.read_text())
        run, record = self.run_once()
        self.assertTrue(any('scenario' in w for w in record['integrity_warnings']))
        with tarfile.open(run / 'scenario.tar.gz') as archive:
            self.assertEqual(archive.extractfile('scenario/test.txt').read(), b'assertion v1')

    def test_runner_never_installs_and_captures_problem(self):
        (self.task / 'prepare_deps.py').write_text('raise RuntimeError("must not install")')
        (self.task / 'BUG.md').write_text('original problem')
        run, record = self.run_once()
        self.assertEqual(record['execution']['status'], 'completed')
        self.assertEqual((run / 'BUG.md').read_text(), 'original problem')
        self.assertIn('problem_sha256', record)

    def test_empty_evidence_warns(self):
        p = self.task / 'scenario/run.sh'
        p.write_text(p.read_text().replace('exit 0', ': > "$MAKE_BUG_RUN_DIR/logs/events.json"\nexit 0'))
        _, record = self.run_once()
        self.assertTrue(any('为空' in w for w in record['evidence']['warnings']))

    def test_unreadable_evidence_does_not_discard_report(self):
        with patch.object(evidence, 'file_digest', side_effect=OSError('unreadable')):
            run, record = self.run_once()
        self.assertTrue((run / 'REPORT.md').is_file())
        self.assertTrue(any('无法读取' in w for w in record['evidence']['warnings']))

    def test_report_does_not_render_observation_as_html(self):
        p = self.task / 'scenario/run.sh'
        p.write_text(p.read_text().replace('position preserved', '<script>alert(1)</script>'))
        run, _ = self.run_once()
        self.assertNotIn('<script>', (run / 'REPORT.md').read_text())

    def test_lock_prevents_second_run(self):
        with (self.task / '.scenario.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(sys, 'argv', ['run_scenario.py']), patch.object(runner, '_run_once') as run, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(runner.main(self.task), 2)
                run.assert_not_called()

    def test_detached_child_cleaned_without_killing_borrowed_service(self):
        import time
        borrowed = subprocess.Popen(['sleep', '30'])
        try:
            (self.task / 'scenario/run.sh').write_text('setsid sleep 30 > /dev/null 2>&1 &\necho $! > child.pid\nsleep 0.1\nexit 9\n')
            _, record = self.run_once()
            pid = int((self.task / 'child.pid').read_text())
            path = Path(f'/proc/{pid}/stat')
            alive = path.exists() and path.read_text().split()[2] != 'Z'
            self.assertFalse(alive, '脱离进程组的本任务子进程仍在运行')
            self.assertIsNone(borrowed.poll(), '借用服务被误杀')
        finally:
            borrowed.terminate(); borrowed.wait()
            if (self.task / 'child.pid').exists():
                try: os.kill(int((self.task / 'child.pid').read_text()), 15)
                except ProcessLookupError: pass


if __name__ == "__main__":
    unittest.main()
