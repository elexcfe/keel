"""仅合成项目/假 Pi；不读取真实任务或调用模型。"""
import contextlib
from datetime import datetime, timedelta, timezone
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
import handoff
import run_scenario
import session_stats
import task_flow as flow
from test_repair import fixture, SRC


FAKE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
if '--version' in sys.argv:
    print('fake-offline'); raise SystemExit(0)
root = Path.cwd()
session = Path(sys.argv[sys.argv.index('--session-dir')+1])
sid = 'repair' if os.environ['MAKE_BUG_PURPOSE']=='repair' else 'reproduce'
u = {'input':1,'output':1,'cacheRead':0,'cacheWrite':0,'totalTokens':2,'cost':{'total':0.1}}
(session/'session.jsonl').write_text(json.dumps({'type':'session','version':3,'id':sid})+'\n'+json.dumps({'type':'message','id':'usage','message':{'role':'assistant','usage':u}})+'\n')
def scene():
    p = subprocess.run(['bash','run_scenario.sh'], capture_output=True,text=True)
    assert p.returncode in (0,3), p.stdout+p.stderr
    records=[json.loads(p.read_text()) for p in Path('result/runs').glob('*/record.json')]
    return max(records,key=lambda r:r['finished_at'])
if sid=='reproduce':
    base=scene()
    Path('result/REPRO.md').write_text('synthetic reproduction '+base['run_id'])
else:
    ref=json.loads(Path(os.environ['MAKE_BUG_REPAIR_REFERENCE']).read_text())
    Path('repo/app.txt').write_text('still broken')
    failed=scene()
    assert failed['repair']['status']=='checks_failed'
    Path('result/FIX.md').write_text('synthetic failed attempt '+failed['run_id'])
    Path('repo/app.txt').write_text('fixed')
    candidate=scene()
    assert candidate['repair']['status']=='checks_passed'
    Path('result/FIX.md').write_text('synthetic repair '+ref['baseline_run']+' '+candidate['run_id'])
    Path('result/environment.md').write_text('Synthetic: Python and Bash. No service or persistent data. Commands use task-relative paths. Run bash run_scenario.sh to check and replay.')
    Path('result/HANDOFF.json').write_text(json.dumps({'schema':1,'environment':{'guide':'result/environment.md','prepare':': # no install required','check':'test -f repo/app.txt','reset':': # no persistent state'},'demos':[{'run_id':v,'guide':guide,'artifacts':['logs/value.txt']} for v,guide in [(ref['baseline_run'],'result/REPRO.md'),(candidate['run_id'],'result/FIX.md')]]}))
'''


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='keel flow ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task = fixture(self.root)
        self.control = self.root / 'control'
        for path in [*SRC.glob('*.py'), SRC / 'command_timeout.mjs']:
            shutil.copy2(path, self.task / path.name)
        for original, name in [('repair.md','REPAIR.md'), ('reproduce.md','TASK.md'),
                               ('scenario.md','SCENARIO.md'), ('monitor.md','MONITOR.md'), ('environment.md','ENVIRONMENT.md')]:
            shutil.copy2(SRC.parent / 'resource/prompts' / original, self.task / name)
        (self.task / 'deps-config.json').write_text(json.dumps({'cache': str(self.root / 'cache'), 'project': 'fixture'}))
        (self.task / 'run_scenario.sh').write_text(run_scenario.LAUNCHER)
        self.fake = self.root / 'fake-pi'
        self.fake.write_text(FAKE)
        self.fake.chmod(0o755)
        self.state = flow.init(self.control, self.task, 180, 1, 60)

    def reload(self):
        self.state, self.task = flow.load(self.control)

    def execute(self, purpose):
        with patch.dict(os.environ, {'PI_BIN': str(self.fake), 'MAKE_BUG_SKIP_DEPS': '1'}), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            code = flow.execute(self.control, self.state, self.task, purpose)
        self.reload()
        self.assertEqual(code, 0)
        return flow.latest_run(self.control, self.state)

    def closed(self):
        self.execute('reproduce')
        baseline = max((json.loads(p.read_text()) for p in (self.task / 'result/runs').glob('*/record.json')), key=lambda r:r['finished_at'])
        flow.admit(self.control, self.state, self.task, baseline['run_id'], 'offline reviewer', ['synthetic observed'])
        self.execute('repair')
        return handoff.inspect(self.control, self.state, self.task)

    def test_offline_whole_flow_same_session_and_review(self):
        with self.assertRaisesRegex(ValueError, '准入'):
            flow.execute(self.control, self.state, self.task, 'repair')
        report = self.closed()
        self.assertEqual(report['errors'], [])
        self.assertEqual(flow.status(self.control, self.state, self.task)['stage'], 'awaiting_review')
        budget = flow.usage(self.control, self.state)
        self.assertAlmostEqual(budget['cost_usd'], .2)
        self.assertLess(budget['remaining_seconds'], 180)
        self.assertEqual(len(list((self.task / 'result/runs').glob('*/record.json'))), 3)
        before = {str(p): p.read_bytes() for p in (self.task / 'result').rglob('*') if p.is_file()}
        handoff.write(self.control, self.state, self.task)
        self.assertIn('fixed', (self.control / 'CHANGES.diff').read_text())
        self.assertEqual(before, {str(p): p.read_bytes() for p in (self.task / 'result').rglob('*') if p.is_file()})
        flow.begin_review(self.control, self.state, self.task)
        with self.assertRaisesRegex(ValueError, '已开始'):
            flow.begin_review(self.control, self.state, self.task)
        with contextlib.redirect_stdout(io.StringIO()):
            checked = flow.review_check(self.control, self.state, self.task)
        self.assertEqual(checked['repair']['status'], 'checks_passed')
        flow.finish_review(self.control, self.state, self.task, 'accepted', 'outer reviewer', ['inspected synthetic logs and diff'])
        self.assertEqual(flow.status(self.control, self.state, self.task)['stage'], 'accepted')
        with self.assertRaises(ValueError):
            flow.finish_review(self.control, self.state, self.task, 'accepted', 'other', ['duplicate'])
        (self.task / 'repo/app.txt').write_text('later edit')
        self.assertEqual(flow.status(self.control, self.state, self.task)['stage'], 'review_outdated')

    def test_missing_environment_media_run_and_report_are_incomplete(self):
        self.closed()
        file = self.task / 'result/HANDOFF.json'
        original = file.read_text()
        for mode in ('reset', 'run', 'media', 'guide'):
            with self.subTest(mode=mode):
                value = json.loads(original)
                if mode == 'reset': value['environment'].pop('reset')
                if mode == 'run': value['demos'][0]['run_id'] = 'run-wrong'
                if mode == 'media': value['demos'][0]['artifacts'] = ['videos/missing.webm']
                if mode == 'guide': value['environment']['guide'] = '../outside'
                file.write_text(json.dumps(value))
                self.assertTrue(handoff.inspect(self.control, self.state, self.task)['errors'])
        file.write_text(original)
        (self.task / 'result/FIX.md').unlink()
        self.assertTrue(handoff.inspect(self.control, self.state, self.task)['errors'])

    def test_read_only_status_unknown_cost_dedupe_and_interrupt(self):
        self.execute('reproduce')
        paths = list((self.control / 'attempts').glob('*/runs/*/sessions/**/*.jsonl'))
        self.assertTrue(paths)
        before = (self.control / 'task.json').read_bytes()
        for _ in range(2): flow.status(self.control, self.state, self.task)
        self.assertEqual(before, (self.control / 'task.json').read_bytes())
        shutil.copy2(paths[0], paths[0].with_name('duplicate.jsonl'))
        self.assertAlmostEqual(flow.usage(self.control, self.state)['cost_usd'], .1)
        for p in paths[0].parent.glob('*.jsonl'): p.write_text('broken JSON\n')
        self.assertIsNone(flow.usage(self.control, self.state)['cost_usd'])
        with self.assertRaisesRegex(ValueError, '未知'):
            flow.execute(self.control, self.state, self.task, 'reproduce')
        path = self.control / self.state['attempts'][0]
        value = flow.read(path); value.pop('elapsed_seconds'); session_stats.save(path, value)
        self.assertEqual(flow.status(self.control, self.state, self.task)['stage'], 'running_or_interrupted')
        flow.reconcile(self.control, self.state, self.task)
        self.assertEqual(flow.usage(self.control, self.state)['remaining_seconds'], 0)
        self.assertIsNone(flow.usage(self.control, self.state)['cost_usd'])

    def test_moved_bundle_links_and_real_scenario_rerun(self):
        self.closed()
        handoff.write(self.control, self.state, self.task)
        moved = self.root / 'moved bundle'
        moved.mkdir()
        shutil.move(str(self.control), moved / 'control')
        shutil.move(str(self.task), moved / 'task')
        self.control = moved / 'control'
        self.reload()
        report = handoff.inspect(self.control, self.state, self.task)
        self.assertEqual(report['errors'], [])
        for item in report['links']:
            self.assertTrue((self.control / item['path']).is_file())
        for command in report['commands'].values():
            subprocess.run(['bash', '-c', command], cwd=self.task, check=True)
        with contextlib.redirect_stdout(io.StringIO()):
            _, record = run_scenario.run_once(self.task, 5, reference=self.control / self.state['reference'])
        self.assertEqual(record['repair']['status'], 'checks_passed')
        self.assertTrue(handoff.inspect(self.control, self.state, self.task)['errors'])

    def test_review_budget_and_reference_integrity(self):
        self.closed()
        flow.begin_review(self.control, self.state, self.task)
        self.state['reviews'][-1]['started_at'] = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
        value = flow.finish_review(self.control, self.state, self.task, 'accepted', 'reviewer', ['evidence'])
        self.assertEqual(value['outcome'], 'timed_out')
        with self.assertRaisesRegex(ValueError, '耗尽'):
            flow.begin_review(self.control, self.state, self.task)
        (self.task / 'scenario/check.sh').write_text('true\n')
        self.assertTrue(handoff.inspect(self.control, self.state, self.task)['errors'])

    def test_duplicate_init_and_lock(self):
        with self.assertRaises(FileExistsError):
            flow.init(self.root / 'another-control', self.task, 180, 1, 60)
        with flow.locked(self.control):
            with self.assertRaisesRegex(ValueError, '正在操作'):
                with flow.locked(self.control): pass

    def test_cli_status_and_missing_delivery_never_call_model(self):
        with patch.object(flow.run_pi, 'run', side_effect=AssertionError('must not run')):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(flow.main(['--control', str(self.control), 'status']), 0)
                self.assertEqual(flow.main(['--control', str(self.control), 'handoff']), 0)
            flow.begin_review(self.control, self.state, self.task)
            with self.assertRaisesRegex(ValueError, '不完整'):
                flow.finish_review(self.control, self.state, self.task, 'accepted', 'outer', ['checked'])
            result = flow.finish_review(self.control, self.state, self.task, 'blocked', 'outer', ['missing Pi artifacts'])
            self.assertEqual(result['outcome'], 'blocked')

    def test_empty_later_attempt_cannot_hide_unknown_usage(self):
        self.execute('reproduce')
        directory = self.control / 'attempts/empty'
        (directory / 'runs').mkdir(parents=True)
        name = 'attempts/empty/attempt.json'
        session_stats.save(self.control / name, {'runs_path': 'attempts/empty/runs', 'elapsed_seconds': 2})
        self.state['attempts'].append(name)
        self.assertIsNone(flow.usage(self.control, self.state)['cost_usd'])
        with self.assertRaisesRegex(ValueError, '未知'):
            flow.execute(self.control, self.state, self.task, 'reproduce')
        session = next((self.control / 'attempts').glob('*/runs/*/sessions/**/*.jsonl'))
        target = directory / 'runs/copied/sessions'
        target.mkdir(parents=True)
        shutil.copy2(session, target / 'continued.jsonl')
        self.assertAlmostEqual(flow.usage(self.control, self.state)['cost_usd'], .1)

    def test_exhausted_budget_and_modified_admission_do_not_run(self):
        self.execute('reproduce')
        baseline = next((self.task / 'result/runs').glob('*/record.json')).parent.name
        flow.admit(self.control, self.state, self.task, baseline, 'reviewer', ['synthetic'])
        self.state['limits']['cost_usd'] = .1
        with self.assertRaisesRegex(ValueError, '耗尽'):
            flow.execute(self.control, self.state, self.task, 'repair')
        (self.task / 'scenario/run.sh').write_text('true\n')
        self.assertEqual(flow.status(self.control, self.state, self.task)['stage'], 'reference_outdated')
        with self.assertRaisesRegex(ValueError, '场景'):
            flow.execute(self.control, self.state, self.task, 'repair')

    def test_archive_tamper_and_report_update_invalidate_review(self):
        report = self.closed()
        flow.begin_review(self.control, self.state, self.task)
        file = self.task / 'result/environment.md'
        file.write_text(file.read_text() + '\nnew conditions')
        with self.assertRaisesRegex(ValueError, '变化'):
            flow.finish_review(self.control, self.state, self.task, 'accepted', 'outer', ['review'])
        flow.finish_review(self.control, self.state, self.task, 'rejected', 'outer', ['conditions changed'])
        archive = self.task / 'result/runs' / report['candidate_run'] / 'code/source.tar.gz'
        archive.write_bytes(b'broken archive')
        self.assertTrue(handoff.inspect(self.control, self.state, self.task)['errors'])


if __name__ == '__main__':
    unittest.main()
