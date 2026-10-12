import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import verify


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='verify space ')
        self.addCleanup(self.tmp.cleanup)
        self.task = Path(self.tmp.name)
        for name in ('repo', 'repro'):
            (self.task / name).mkdir()
        subprocess.run(['git', 'init', '-q', str(self.task / 'repo')], check=True)
        subprocess.run(['git', '-c', 'user.name=test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-qm', 'fixture'], cwd=self.task / 'repo', check=True)
        (self.task / 'tools-config.json').write_text(json.dumps({'shared': str(self.task / 'shared')}))
        (self.task / 'repro/manifest.json').write_text(json.dumps({'schema': 1, 'coverage': ['fixture'], 'files': ['repo/test.sh']}))
        (self.task / 'repro/env.sh').write_text('export FIXTURE_ENV=ready\n')
        (self.task / 'repro/check.sh').write_text('test "$FIXTURE_ENV" = ready\n')
        (self.task / 'repo/test.sh').write_text('# fixed assertion\n')
        self.script('target_failed', 1)

    def script(self, status, code):
        (self.task / 'repro/run.sh').write_text(f'''printf '%s' '{{"status":"{status}","reason":"fixture assertion"}}' > "$MAKE_BUG_OUTCOME"
exit {code}
''')

    def run_verify(self, baseline=False, timeout=5):
        with contextlib.redirect_stdout(io.StringIO()):
            return verify.run_once(self.task, SimpleNamespace(baseline=baseline, prepare=False, timeout=timeout))

    def test_no_baseline_is_blocked_and_no_install(self):
        with patch.object(verify.subprocess, 'run', wraps=subprocess.run) as run:
            self.assertEqual(self.run_verify(), 2)
            self.assertFalse(any('prepare_deps.py' in str(c) for c in run.call_args_list))

    def test_baseline_and_fixed_code_same_test_and_separate_results(self):
        (self.task / 'repro/run.sh').write_text('''if test -f "$MAKE_BUG_TASK/repo/fixed"; then
printf '%s' '{"status":"passed","reason":"fixed"}' > "$MAKE_BUG_OUTCOME"; exit 0
else
printf '%s' '{"status":"target_failed","reason":"assertion"}' > "$MAKE_BUG_OUTCOME"; exit 1
fi
''')
        self.assertEqual(self.run_verify(True), 1)
        (self.task / 'repo/fixed').touch()
        self.assertEqual(self.run_verify(), 0)
        runs = list((self.task / 'result/runs').glob('*/summary.json'))
        self.assertEqual(len(runs), 2)
        self.assertEqual({json.loads(p.read_text())['status'] for p in runs}, {'passed', 'target_failed'})

    def test_assertion_changes_blocked(self):
        self.assertEqual(self.run_verify(True), 1)
        (self.task / 'repo/test.sh').write_text('changed assertion')
        self.assertEqual(self.run_verify(), 2)

    def test_nonzero_or_missing_outcome_not_target_bug(self):
        (self.task / 'repro/run.sh').write_text('exit 1\n')
        self.assertEqual(self.run_verify(True), 2)
        self.assertFalse((self.task / 'repro/baseline.json').exists())
        self.script('target_failed', 0)
        self.assertEqual(self.run_verify(True), 2)

    def test_check_failure_and_timeout_are_blocked(self):
        (self.task / 'repro/check.sh').write_text('exit 3\n')
        self.assertEqual(self.run_verify(True), 2)
        (self.task / 'repro/check.sh').write_text('sleep 30\n')
        self.assertEqual(self.run_verify(True, timeout=0.1), 2)

    def test_dependencies_change_requires_prepare(self):
        with patch.object(verify, 'dependency_hash', return_value='old'):
            self.assertEqual(self.run_verify(True), 1)
        with patch.object(verify, 'dependency_hash', return_value='new'):
            self.assertEqual(self.run_verify(), 2)

    def test_runtime_source_edit_is_blocked(self):
        p = self.task / 'repro/run.sh'
        p.write_text('echo changed > "$MAKE_BUG_TASK/repo/app.js"\n' + p.read_text())
        self.assertEqual(self.run_verify(True), 2)
        self.assertFalse((self.task / 'repro/baseline.json').exists())

    def test_malformed_manifest_records_blocked_result(self):
        (self.task / 'repro/manifest.json').write_text('[]')
        self.assertEqual(self.run_verify(True), 2)
        result = next((self.task / 'result/runs').glob('*/summary.json'))
        self.assertEqual(json.loads(result.read_text())['status'], 'blocked')

    def test_missing_manifest_and_path_escape_are_blocked(self):
        manifest = self.task / 'repro/manifest.json'
        manifest.write_text(json.dumps({'schema': 1, 'coverage': ['fixture'], 'files': ['../outside']}))
        self.assertEqual(self.run_verify(True), 2)
        manifest.unlink()
        self.assertEqual(self.run_verify(True), 2)

    def test_timeout_cleans_spawned_children(self):
        (self.task / 'repro/check.sh').write_text('sleep 30 &\necho $! > "$MAKE_BUG_TASK/child.pid"\nwait\n')
        self.assertEqual(self.run_verify(True, timeout=0.2), 2)
        pid = int((self.task / 'child.pid').read_text())
        stat = Path(f'/proc/{pid}/stat')
        if stat.exists():
            self.assertEqual(stat.read_text().split()[2], 'Z')

    def test_prepared_environment_is_remembered_without_replacing_baseline(self):
        with patch.object(verify, 'dependency_hash', return_value='old'):
            self.assertEqual(self.run_verify(True), 1)
        (self.task / 'prepare_deps.py').write_text('print("prepared fixture")')
        args = SimpleNamespace(baseline=False, prepare=True, timeout=5)
        with patch.object(verify, 'dependency_hash', return_value='new'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(verify.run_once(self.task, args), 1)
            self.assertEqual(self.run_verify(), 1)
        self.assertEqual(json.loads((self.task / 'repro/baseline.json').read_text())['dependencies'], 'old')

    def test_repeat_stops_on_blocked_and_prepare_runs_once(self):
        with patch.object(sys, 'argv', ['verify.py', '--baseline', '--repeat', '3', '--prepare']), patch.object(verify, 'run_once') as run:
            observed = []
            def result(task, args):
                observed.append(args.prepare)
                return 1 if len(observed) == 1 else 2
            run.side_effect = result
            self.assertEqual(verify.main(self.task), 2)
            self.assertEqual(observed, [True, False])

    def test_pass_cannot_establish_bug_baseline(self):
        self.script('passed', 0)
        self.assertEqual(self.run_verify(True), 2)
        self.assertFalse((self.task / 'repro/baseline.json').exists())


if __name__ == '__main__':
    unittest.main()
