"""通用合成项目；不使用或修改真实 Pi 任务产物。"""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import evidence
import run_scenario

SRC = Path(run_scenario.__file__).parent


def fixture(root):
    task = root / 'task'
    (task / 'repo').mkdir(parents=True)
    (task / 'scenario').mkdir()
    (task / 'BUG.md').write_text('合成缺陷：值应为 fixed，正常行为应保留。')
    (task / 'repo/app.txt').write_text('broken')
    subprocess.run(['git', 'init', '-q', str(task / 'repo')], check=True)
    subprocess.run(['git', 'add', '.'], cwd=task / 'repo', check=True)
    subprocess.run(['git', '-c', 'user.name=test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'fixture'], cwd=task / 'repo', check=True)
    (task / 'tools-config.json').write_text(json.dumps({'shared': str(root / 'shared')}))
    (task / 'scenario/manifest.json').write_text(json.dumps({
        'schema': 2, 'title': 'synthetic', 'data': 'app.txt; no persistent state',
        'steps': ['read app.txt'], 'coverage': ['target', 'normal behavior'],
        'files': ['scenario/probe.py'], 'required_checks': [
            {'id': 'target', 'kind': 'target', 'expected': 'value equals fixed'},
            {'id': 'normal', 'kind': 'regression', 'expected': 'value is not regression'}]}))
    (task / 'scenario/env.sh').write_text(':\n')
    (task / 'scenario/check.sh').write_text('test -f "$MAKE_BUG_TASK/repo/app.txt"\n')
    (task / 'scenario/run.sh').write_text('python3 "$MAKE_BUG_TASK/scenario/probe.py"\n')
    (task / 'scenario/probe.py').write_text('''import json, os
from pathlib import Path
task=Path(os.environ['MAKE_BUG_TASK']); run=Path(os.environ['MAKE_BUG_RUN_DIR'])
value=(task/'repo/app.txt').read_text()
(run/'logs/value.txt').write_text(value)
checks=[{'id': key, 'name': key, 'status': 'passed' if ok else 'failed', 'evidence': ['logs/value.txt']} for key,ok in [('target',value in ('fixed','regression')),('normal',value!='regression')]]
Path(os.environ['MAKE_BUG_OBSERVATIONS']).write_text(json.dumps({'schema':2,'observations':[{'name':'value','value':value,'evidence':['logs/value.txt']}],'checks':checks,'limitations':[]}))
''')
    return task


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = fixture(self.root)

    def run_scene(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_scenario.run_once(self.task, 5, **kwargs)

    def test_schema_two_records_complete_failed_target(self):
        _, record = self.run_scene()
        self.assertEqual(record['execution']['status'], 'completed')
        self.assertEqual(record['validation']['status'], 'complete')
        self.assertEqual(record['checks'][0]['status'], 'failed')

    def reference(self):
        import repair
        _, baseline = self.run_scene()
        path = self.root / 'reference.json'
        evidence.save(path, repair.admit(self.task, baseline['run_id'], 'offline reviewer', ['synthetic fixture inspected']))
        return path, baseline

    def test_missing_duplicate_unknown_and_unexecuted_checks_rejected(self):
        _, record = self.run_scene()
        import copy
        for change in ('missing', 'duplicate', 'unknown', 'inconclusive', 'no_evidence'):
            with self.subTest(change=change):
                candidate = copy.deepcopy(record)
                if change == 'missing': candidate['checks'].pop()
                if change == 'duplicate': candidate['checks'].append(candidate['checks'][0])
                if change == 'unknown': candidate['checks'][0]['id'] = 'other'
                if change == 'inconclusive': candidate['checks'][0]['status'] = 'inconclusive'
                if change == 'no_evidence': candidate['checks'][0]['evidence'] = []
                self.assertEqual(evidence.validate_checks(candidate)['status'], 'incomplete')

    def test_reference_requires_failed_target_passing_regression_and_current_source(self):
        import repair
        for value in ('fixed', 'regression'):
            (self.task / 'repo/app.txt').write_text(value)
            _, record = self.run_scene()
            with self.assertRaises(ValueError):
                repair.admit(self.task, record['run_id'], 'reviewer', ['review'])
        (self.task / 'repo/app.txt').write_text('broken')
        _, record = self.run_scene()
        (self.task / 'repo/app.txt').write_text('other')
        with self.assertRaisesRegex(ValueError, '代码'):
            repair.admit(self.task, record['run_id'], 'reviewer', ['review'])

    def test_old_schema_runs_but_cannot_be_admitted(self):
        import repair
        manifest = self.task / 'scenario/manifest.json'
        data = json.loads(manifest.read_text()); data['schema'] = 1
        manifest.write_text(json.dumps(data))
        _, record = self.run_scene()
        self.assertEqual(record['execution']['status'], 'completed')
        with self.assertRaisesRegex(ValueError, 'schema 2'):
            repair.admit(self.task, record['run_id'], 'reviewer', ['review'])

    def test_repeated_checks_failure_regression_failure_and_success(self):
        reference, baseline = self.reference()
        for value, expected in [('broken', 'checks_failed'), ('regression', 'checks_failed'), ('fixed', 'checks_passed')]:
            (self.task / 'repo/app.txt').write_text(value)
            _, record = self.run_scene(reference=reference)
            self.assertEqual(record['repair']['status'], expected, record)
            self.assertEqual(record['repair']['baseline_run'], baseline['run_id'])
        self.assertEqual(len(list((self.task / 'result/runs').iterdir())), 4)

    def test_changed_scene_blocked_before_execution(self):
        reference, _ = self.reference()
        script = self.task / 'scenario/run.sh'
        script.write_text('touch should-not-execute\n' + script.read_text())
        _, record = self.run_scene(reference=reference)
        self.assertEqual(record['repair']['status'], 'invalid')
        self.assertFalse((self.task / 'should-not-execute').exists())

    def test_environment_failure_is_not_target_failure(self):
        reference, _ = self.reference()
        (self.task / 'repo/app.txt').unlink()
        _, record = self.run_scene(reference=reference)
        self.assertEqual(record['repair']['status'], 'blocked')
        self.assertEqual(record['checks'], [])

    def test_baseline_evidence_tamper_rejected(self):
        import repair
        reference, baseline = self.reference()
        (self.task / 'result/runs' / baseline['run_id'] / 'logs/value.txt').write_text('tampered')
        with self.assertRaisesRegex(ValueError, '证据已变化'):
            repair.load_reference(self.task, reference)

    def test_final_delivery_requires_report_correct_runs_and_current_code(self):
        import repair
        reference, baseline = self.reference()
        before = {baseline['run_id']}
        self.assertEqual(repair.finalize(self.task, baseline, before)['status'], 'incomplete')
        (self.task / 'repo/app.txt').write_text('fixed')
        _, latest = self.run_scene(reference=reference)
        self.assertEqual(repair.finalize(self.task, baseline, before)['status'], 'incomplete')
        report = self.task / 'result/FIX.md'
        report.write_text('wrong run')
        self.assertEqual(repair.finalize(self.task, baseline, before)['status'], 'incomplete')
        report.write_text(baseline['run_id'] + '\n' + latest['run_id'])
        self.assertEqual(repair.finalize(self.task, baseline, before)['status'], 'awaiting_review')
        self.assertEqual(repair.finalize(self.task, baseline, before, evidence.file_digest(report))['status'], 'incomplete')
        (self.task / 'repo/app.txt').write_text('later edit')
        self.assertEqual(repair.finalize(self.task, baseline, before)['status'], 'incomplete')
        _, failed = self.run_scene(reference=reference)
        (self.task / 'repo/app.txt').write_text('fixed')
        self.assertEqual(repair.finalize(self.task, baseline, before)['run_id'], failed['run_id'])
        self.assertEqual(repair.finalize(self.task, baseline, before)['status'], 'incomplete')

    def test_total_deadline_shared_across_check_and_run(self):
        (self.task / 'scenario/check.sh').write_text('sleep 0.2\n')
        script = self.task / 'scenario/run.sh'
        script.write_text('sleep 0.2\n' + script.read_text())
        with contextlib.redirect_stdout(io.StringIO()):
            _, record = run_scenario.run_once(self.task, .3)
        self.assertEqual(record['execution']['status'], 'error')
        self.assertIn('超时', record['execution']['reason'])

    def test_parent_deadline_reserves_report_time(self):
        import time
        (self.task / 'scenario/check.sh').write_text('sleep 0.2\n')
        with patch.dict(os.environ, {'MAKE_BUG_DEADLINE_MS': str((time.time() + 5) * 1000), 'MAKE_BUG_WRAP_UP_MS': '4950'}):
            _, record = self.run_scene()
        self.assertEqual(record['execution']['status'], 'blocked')

    def test_scenario_leaves_time_to_save_before_parent_bash_boundary(self):
        observed = []
        execute = run_scenario.execute
        def capture(*args):
            observed.append(args[-1])
            return execute(*args)
        with patch.dict(os.environ, {'MAKE_BUG_DEADLINE_MS': '1040000', 'MAKE_BUG_WRAP_UP_MS': '0'}), \
                patch.object(run_scenario.time, 'time', return_value=1000), \
                patch.object(run_scenario, 'execute', side_effect=capture):
            _, record = self.run_scene()
        # 父预算剩 40 秒，先留 30 秒；run_scene 本身 5 秒，因此此处只用 5 秒。
        self.assertEqual(record['execution']['status'], 'completed')
        self.assertTrue(all(0 < value <= 5 for value in observed))
        with patch.dict(os.environ, {'MAKE_BUG_DEADLINE_MS': '1040000', 'MAKE_BUG_WRAP_UP_MS': '0'}), \
                patch.object(run_scenario.time, 'time', return_value=1000), \
                patch.object(run_scenario, 'execute', side_effect=capture), \
                contextlib.redirect_stdout(io.StringIO()):
            observed.clear()
            _, record = run_scenario.run_once(self.task, 300)
        self.assertEqual(record['execution']['status'], 'completed')
        self.assertTrue(all(8 < value <= 10 for value in observed), observed)

    def test_scenario_does_not_start_inside_finalization_window(self):
        with patch.dict(os.environ, {'MAKE_BUG_DEADLINE_MS': '1020000', 'MAKE_BUG_WRAP_UP_MS': '0'}), \
                patch.object(run_scenario.time, 'time', return_value=1000), \
                patch.object(run_scenario, 'execute') as execute:
            folder, record = self.run_scene()
        execute.assert_not_called()
        self.assertEqual(record['execution']['status'], 'blocked')
        self.assertTrue((folder / 'record.json').exists())

    def install_runner(self):
        for path in [*SRC.glob('*.py'), SRC / 'command_timeout.mjs']:
            shutil.copy2(path, self.task / path.name)
        prompts = SRC.parent / 'resource/prompts'
        for original, name in [('repair.md','REPAIR.md'),('reproduce.md','TASK.md'),('monitor.md','MONITOR.md'),('environment.md','ENVIRONMENT.md')]:
            shutil.copy2(prompts / original, self.task / name)
        (self.task / 'result/REPRO.md').write_text('合成复现报告')
        (self.task / 'deps-config.json').write_text(json.dumps({'cache': str(self.root / 'cache'), 'project': 'fixture'}))
        (self.task / 'run_scenario.sh').write_text(run_scenario.LAUNCHER)
        fake = self.root / 'fake-pi'
        fake.write_text(FAKE_REPAIR)
        fake.chmod(0o755)
        return fake

    def execute_repair(self, mode='complete', extra=()):
        reference, baseline = self.reference()
        fake = self.install_runner()
        runs = self.root / 'pi-runs'
        result = subprocess.run([sys.executable, str(self.task / 'run_pi.py'), '--purpose', 'repair',
                                 '--reference', str(reference), '--runs-dir', str(runs), '--timeout', '60',
                                 '--wrap-up', '1', *extra],
                                env={**os.environ, 'PI_BIN': str(fake), 'FAKE_REPAIR_MODE': mode, 'MAKE_BUG_SKIP_DEPS': '1'},
                                capture_output=True, text=True, timeout=30)
        return result, next(runs.iterdir()) if runs.exists() else None

    def test_one_pi_process_corrects_failure_and_delivers_report(self):
        result, run = self.execute_repair()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        state = json.loads((run / 'run.json').read_text())
        self.assertEqual(state['repair']['status'], 'awaiting_review', state['repair'])
        self.assertEqual(len((self.task / 'pi-invocations').read_text().splitlines()), 1)
        self.assertEqual([p.name for p in (run / 'sessions').iterdir()], ['repair'])
        self.assertEqual(len(list((self.task / 'result/runs').iterdir())), 3)
        self.assertTrue((run / 'FIX.partial.md').exists())
        import session_stats
        evidence.save(run / 'review.json', {'outcome': 'reproduced', 'reviewer': 'wrong-kind', 'evidence': ['fixture']})
        summary = session_stats.summarize(run.parent)
        self.assertEqual(summary['groups']['single']['attempts'], 0)
        self.assertFalse(summary['runs'][0]['accepted_reproduction'])
        evidence.save(run / 'review.json', {'outcome': 'pending'})
        self.assertEqual(json.loads((run / 'review.json').read_text())['outcome'], 'pending')
        self.assertEqual(json.loads((run / 'repair-result.json').read_text())['human_intervention'], 'unreviewed')

    def test_invalid_reference_and_split_do_not_start_pi(self):
        result, _ = self.execute_repair(extra=('--workflow', 'split'))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.task / 'pi-invocations').exists())

    def test_malformed_reference_rejected(self):
        import repair
        reference, _ = self.reference()
        for value in ([], {}, {'schema': 1, 'status': 'accepted_reproduction'}):
            reference.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                repair.load_reference(self.task, reference)

    def test_same_session_failure_modes_never_deliver_success(self):
        for mode in ('no_report', 'late_edit', 'fail_exit', 'tamper_reference'):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as tmp:
                    original_task, original_root = self.task, self.root
                    try:
                        self.root = Path(tmp); self.task = fixture(self.root)
                        result, run = self.execute_repair(mode)
                        self.assertEqual(json.loads((run / 'repair-result.json').read_text())['status'], 'incomplete')
                    finally:
                        self.task, self.root = original_task, original_root


FAKE_REPAIR = r'''#!/usr/bin/env python3
import json,os,subprocess,sys
from pathlib import Path
if '--version' in sys.argv:
    print('fake-offline'); raise SystemExit(0)
with Path('pi-invocations').open('a') as f: f.write(str(os.getpid())+'\n')
prompts=[sys.argv[i+1] for i,v in enumerate(sys.argv) if v=='--append-system-prompt']
assert any(Path(p).name=='REPAIR.md' for p in prompts)
assert all(Path(p).name not in ('TASK.md','SCENARIO.md','PREPARE.md') for p in prompts)
session=Path(sys.argv[sys.argv.index('--session-dir')+1])
u={'input':1,'output':1,'cacheRead':0,'cacheWrite':0,'totalTokens':2,'cost':{'total':0}}
(session/'session.jsonl').write_text(json.dumps({'type':'message','id':'a','message':{'role':'assistant','usage':u}})+'\n')
reference=json.loads(Path(os.environ['MAKE_BUG_REPAIR_REFERENCE']).read_text())
mode=os.environ.get('FAKE_REPAIR_MODE')
Path('repo/app.txt').write_text('still broken')
first=subprocess.run(['bash','run_scenario.sh'],capture_output=True,text=True)
assert first.returncode==3, first.stdout+first.stderr
assert 'checks_failed' in first.stdout
Path('repo/app.txt').write_text('fixed')
second=subprocess.run(['bash','run_scenario.sh'],capture_output=True,text=True)
assert second.returncode==0, second.stdout+second.stderr
records=[json.loads(p.read_text()) for p in Path('result/runs').glob('*/record.json')]
latest=max(records,key=lambda r:r['finished_at'])
assert latest['repair']['status']=='checks_passed'
if mode!='no_report': Path('result/FIX.md').write_text('合成报告：'+reference['baseline_run']+'\n'+latest['run_id'])
if mode=='late_edit': Path('repo/app.txt').write_text('changed after test')
if mode=='tamper_reference': Path(os.environ['MAKE_BUG_REPAIR_REFERENCE']).write_text('{}')
if mode=='fail_exit': raise SystemExit(9)
'''
