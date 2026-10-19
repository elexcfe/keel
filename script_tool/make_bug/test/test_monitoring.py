import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import make_bug
import session_stats as stats
from run_isolation import isolate, copy_tree

SRC = Path(make_bug.__file__).parent
PROMPTS = SRC.parent / 'resource/prompts'
FAKE = r'''#!/usr/bin/env python3
import json,os,sys,time,subprocess
from pathlib import Path
if '--version' in sys.argv:
 print('fake-pi-session-v3'); raise SystemExit(0)
session=Path(sys.argv[sys.argv.index('--session-dir')+1]); session.mkdir(exist_ok=True,parents=True)
phase=session.name
mode=os.environ.get('FAKE_MODE','success')
if mode=='missing': raise SystemExit(0)
f=session/'session.jsonl'
u={'input':10,'output':2,'cacheRead':3,'cacheWrite':0,'totalTokens':15,'cost':{'total':0.25}}
items=[{'type':'session','version':3,'id':phase},
 {'type':'message','id':'a','message':{'role':'assistant','provider':'fake','model':'fake','usage':u,'content':[{'type':'toolCall','id':'t','name':'bash','arguments':{'command':'true'}}]}},
 {'type':'message','id':'b','message':{'role':'toolResult','toolCallId':'t','toolName':'bash','isError':False,'content':[]}}]
f.write_text(''.join(json.dumps(x)+'\n' for x in items))
print(json.dumps({'type':'message_end','message':items[1]['message']}),flush=True)
if phase=='prepare':
 Path('environment').mkdir(exist_ok=True)
 Path('environment/handoff.json').write_text(json.dumps({'status':'ready','cwd':'repo','start_commands':[],'stop_commands':[],'environment_variables':[],'ports':[],'data_setup':[],'data_cleanup':[],'checks':[],'limitations':[]}))
 Path('environment/check.sh').write_text('exit 0\n')
if phase=='reproduce':
 Path('second-session.json').write_text(json.dumps({'args':sys.argv,'handoff':Path('environment/handoff.json').exists()}))
if mode in ('sleep','prepare-sleep') and (mode=='sleep' or phase=='prepare'):
 child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
 Path('child.pid').write_text(str(child.pid))
 time.sleep(30)
if mode=='fail': raise SystemExit(9)
'''


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, items, name='one.jsonl'):
        (self.root / name).write_text(''.join(json.dumps(i) + '\n' for i in items))

    def test_usage_dedupe_branches_compaction_and_unknown(self):
        usage = {'input': 10, 'output': 2, 'cacheRead': 3, 'cacheWrite': 0, 'totalTokens': 15, 'cost': {'total': .25}}
        header = {'type':'session','version':3,'id':'s'}
        message = {'type':'message','id':'m','message':{'role':'assistant','usage':usage}}
        self.write([header,message,message,{'type':'compaction','id':'c','usage':usage}, {'type':'usage','id':'u','usage':usage}, {'type':'branch_summary','id':'b','usage':usage}])
        self.write([header,message], 'copy.jsonl')
        metric = stats.analyze(self.root)
        self.assertEqual(metric['usage_records'], 4)
        self.assertEqual(metric['totals']['cost'], 1)
        self.assertEqual(metric['totals']['totalTokens'],60)
        with (self.root/'one.jsonl').open('a') as f:
            f.write('{broken')
        self.assertIsNone(stats.analyze(self.root)['totals']['cost'])
        self.assertEqual(stats.analyze(self.root)['known_totals']['cost'], 1)

    def test_missing_usage_is_unknown_not_zero(self):
        self.assertIsNone(stats.analyze(self.root)['totals']['cost'])
        self.write([{'type':'session','version':3,'id':'s'}, {'type':'message','id':'m','message':{'role':'assistant'}}])
        self.assertIsNone(stats.analyze(self.root)['totals']['cost'])
        self.assertEqual(stats.analyze(self.root)['missing_fields']['input'], 1)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task = self.root / 'source/task'
        self.task.mkdir(parents=True)
        for file in [*SRC.glob('*.py'), SRC/'command_timeout.mjs']:
            shutil.copy2(file, self.task/file.name)
        for original,name in [('reproduce.md','TASK.md'),('scenario.md','SCENARIO.md'),('environment.md','ENVIRONMENT.md'),('prepare.md','PREPARE.md'),('monitor.md','MONITOR.md')]:
            shutil.copy2(PROMPTS/original,self.task/name)
        (self.task/'BUG.md').write_text('fixture bug')
        (self.task/'run_pi.sh').write_text(make_bug.LAUNCHER)
        (self.task/'deps-config.json').write_text(json.dumps({'cache':str(self.root/'cache'),'project':'fixture'}))
        (self.task/'tools-config.json').write_text(json.dumps({'shared':str(self.root/'shared')}))
        repo=self.task/'repo'; repo.mkdir()
        (repo/'app.txt').write_text('baseline')
        subprocess.run(['git','init','-q',str(repo)],check=True)
        subprocess.run(['git','add','.'],cwd=repo,check=True)
        subprocess.run(['git','-c','user.name=test','-c','user.email=test@example.invalid','commit','-qm','fixture'],cwd=repo,check=True)
        self.fake=self.root/'fake-pi'; self.fake.write_text(FAKE); self.fake.chmod(0o755)
        self.runs=self.root/'runs'

    def command(self,*args):
        return ['bash',str(self.task/'run_pi.sh'),'--runs-dir',str(self.runs),*args]

    def execute(self,*args,mode='success'):
        return subprocess.run(self.command(*args),env={**os.environ,'PI_BIN':str(self.fake),'FAKE_MODE':mode,'MAKE_BUG_SKIP_DEPS':'1'},capture_output=True,text=True,timeout=20)

    def result(self):
        run=next(self.runs.iterdir())
        return run,json.loads((run/'run.json').read_text()),json.loads((run/'metrics.json').read_text())

    def test_single_default_monitoring_and_no_double_count_events(self):
        result=self.execute('--print')
        self.assertEqual(result.returncode,0,result.stderr)
        run,state,metric=self.result()
        self.assertEqual(state['monitoring'],'complete')
        self.assertEqual(metric['totals']['cost'],.25)
        self.assertEqual(metric['tool_calls'],1)
        self.assertEqual(json.loads((run/'review.json').read_text())['outcome'],'pending')
        self.assertEqual(stats.summarize(self.runs)['groups']['single']['accepted_reproductions'],0)
        self.assertTrue((run/'snapshots/initial/source.tar.gz').is_file())
        self.assertEqual((run/'command_timeout.mjs').read_bytes(), (SRC/'command_timeout.mjs').read_bytes())
        self.assertEqual(state['command_timeout']['max_seconds'],600)
        self.assertEqual(state['command_timeout']['default_seconds'],600)
        phase=json.loads((run/'events/single.json').read_text())
        self.assertIn(str(run/'command_timeout.mjs'),phase['command'])

    def test_split_handoff_and_aggregate_cost(self):
        result=self.execute('--workflow','split')
        self.assertEqual(result.returncode,0,result.stderr)
        run,state,metric=self.result()
        self.assertEqual(metric['totals']['cost'],.5)
        self.assertEqual(len(metric['session_ids']),2)
        self.assertEqual(state['handoff']['status'],'recorded')
        second=json.loads((self.task/'second-session.json').read_text())
        self.assertTrue(second['handoff'])
        self.assertNotIn('--resume',second['args'])
        self.assertNotIn('--continue',second['args'])
        self.assertTrue((run/'handoff/handoff.json').is_file())

    def test_missing_session_and_nonzero_exit(self):
        result=self.execute(mode='missing')
        self.assertEqual(result.returncode,0,result.stderr)
        _,state,metric=self.result()
        self.assertEqual(state['monitoring'],'incomplete')
        self.assertIsNone(metric['totals']['cost'])

    def test_timeout_kills_descendants(self):
        result=self.execute('--timeout','1.5',mode='sleep')
        self.assertEqual(result.returncode,124,result.stderr)
        _,state,_=self.result()
        self.assertEqual(state['status'],'timeout')
        run = next(self.runs.iterdir())
        delivery = json.loads((run/'delivery.json').read_text())
        self.assertEqual(delivery['execution'], 'timeout')
        self.assertIn('result/REPRO.md', delivery['missing'])
        self.assertTrue((run/'DELIVERY.md').is_file())
        pid=int((self.task/'child.pid').read_text())
        stat=Path(f'/proc/{pid}/stat')
        if stat.exists():
            self.assertEqual(stat.read_text().split()[2],'Z')

    def test_prepare_timeout_still_runs_second_phase(self):
        result=self.execute('--workflow','split','--timeout','5','--prepare-timeout','.5',mode='prepare-sleep')
        self.assertEqual(result.returncode,0,result.stderr)
        _,state,metric=self.result()
        self.assertEqual(next(p for p in state['phases'] if p['name']=='prepare')['status'],'timeout')
        self.assertEqual(metric['totals']['cost'],.5)

    def test_cost_limit_combines_phases(self):
        result=self.execute('--workflow','split','--cost-limit','.2',mode='sleep')
        self.assertEqual(result.returncode,125,result.stderr)
        _,state,_=self.result()
        self.assertEqual(state['status'],'cost_limit')

    def test_isolation_source_unchanged_and_cache_seed_copied(self):
        seed=self.root/'seed'; seed.mkdir(); (seed/'package').write_text('cache')
        isolated=self.root/'isolated'
        result=self.execute('--isolate',str(isolated),'--cache-seed',str(seed))
        self.assertEqual(result.returncode,0,result.stderr)
        _,state,_=self.result()
        self.assertEqual(state['isolation'],'directory-copy')
        config=json.loads((isolated/'task/deps-config.json').read_text())
        self.assertEqual(config['cache'],str(isolated/'cache'))
        (isolated/'cache/downloads/package').write_text('changed')
        self.assertEqual((seed/'package').read_text(),'cache')
        self.assertFalse((self.task/'dependencies.json').exists())
        self.assertFalse((self.root/'shared').exists())

    def test_isolation_rejects_dirty_repo_and_external_symlink(self):
        (self.task/'repo/app.txt').write_text('user change')
        with self.assertRaises(ValueError): isolate(self.task,self.root/'isolated')
        seed=self.root/'seed';seed.mkdir();(seed/'escape').symlink_to('/tmp')
        with self.assertRaises(ValueError):copy_tree(seed,self.root/'copy')

    def test_session_override_rejected(self):
        result=self.execute('--no-session')
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.runs.exists())

    def test_phase_with_missing_session_makes_total_unknown(self):
        self.assertEqual(self.execute('--workflow','split').returncode,0)
        run,_,_=self.result()
        for path in (run/'sessions/prepare').glob('*.jsonl'):
            path.unlink()
        metric=stats.refresh(run)
        self.assertIsNone(metric['totals']['cost'])
        self.assertEqual(metric['known_totals']['cost'],.25)

    def test_nonzero_exit_is_preserved_with_statistics(self):
        result=self.execute(mode='fail')
        self.assertEqual(result.returncode,9,result.stderr)
        _,state,metric=self.result()
        self.assertEqual(state['status'],'failed')
        self.assertEqual(metric['totals']['cost'],.25)

    def test_sigterm_keeps_partial_records(self):
        process=subprocess.Popen(self.command('--timeout','20'),env={**os.environ,'PI_BIN':str(self.fake),'FAKE_MODE':'sleep','MAKE_BUG_SKIP_DEPS':'1'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+5
            while not (self.task/'child.pid').exists() and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue((self.task/'child.pid').exists())
            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=5),130)
            _,state,metric=self.result()
            self.assertEqual(state['status'],'interrupted')
            self.assertEqual(metric['totals']['cost'],.25)
        finally:
            if process.poll() is None:
                process.kill();process.wait()

    def test_isolation_failure_has_a_record(self):
        (self.task/'repo/app.txt').write_text('existing user edit')
        result=self.execute('--isolate',str(self.root/'isolated'))
        self.assertNotEqual(result.returncode,0)
        _,state,metric=self.result()
        self.assertEqual(state['status'],'error')
        self.assertIsNone(metric['totals']['cost'])
        self.assertEqual((self.task/'repo/app.txt').read_text(),'existing user edit')

    def test_failed_handoff_does_not_hide_second_phase(self):
        self.fake.write_text(FAKE.replace("if phase=='prepare':", "if False:"))
        result=self.execute('--workflow','split')
        self.assertEqual(result.returncode,0,result.stderr)
        _,state,metric=self.result()
        self.assertEqual(state['handoff']['status'],'invalid')
        self.assertEqual(metric['totals']['cost'],.5)

    def test_bad_handoff_shape_is_recorded_not_crash(self):
        self.fake.write_text(FAKE.replace("if phase=='reproduce':", "if phase=='prepare': Path('environment/handoff.json').write_text('[]')\nif phase=='reproduce':"))
        result=self.execute('--workflow','split')
        self.assertEqual(result.returncode,0,result.stderr)
        _,state,_=self.result()
        self.assertEqual(state['handoff']['status'],'invalid')

    def test_task_lock_rejects_parallel_writer(self):
        import fcntl
        with (self.task/'.pi-run.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=self.execute()
        self.assertNotEqual(result.returncode,0)
        _,state,_=self.result()
        self.assertIn('已有 Pi',state['error'])

    def test_summary_includes_failures_and_requires_review(self):
        self.assertEqual(self.execute('--pair-id','p').returncode,0)
        self.assertEqual(self.execute('--workflow','split','--pair-id','p',mode='fail').returncode,9)
        report=stats.summarize(self.runs)
        self.assertEqual(report['groups']['split']['attempts'],1)
        self.assertEqual(report['pairs'][0]['status'],'pending_review')
        for path in self.runs.glob('*/review.json'):
            path.write_text(json.dumps({'outcome':'reproduced','reviewer':'tester','evidence':['run/record.json']}))
        report=stats.summarize(self.runs)
        self.assertEqual(report['groups']['single']['accepted_reproductions'],1)
        self.assertEqual(report['pairs'][0]['status'],'reviewed')

    def test_fast_prepare_over_cost_does_not_start_second_session(self):
        result=self.execute('--workflow','split','--cost-limit','.2')
        self.assertEqual(result.returncode,125,result.stderr)
        run,state,metric=self.result()
        self.assertFalse((run/'sessions/reproduce').exists())
        self.assertFalse((self.task/'second-session.json').exists())
        self.assertEqual(metric['known_totals']['cost'],.25)
