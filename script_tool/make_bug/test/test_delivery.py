from datetime import datetime, timedelta, timezone
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import delivery

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.task = Path(self.tmp.name) / 'task'; self.task.mkdir()
        self.run = Path(self.tmp.name) / 'run'; self.run.mkdir()
        self.base = datetime.now(timezone.utc) - timedelta(minutes=30)
        for name in delivery.REQUIRED + ('result/FIX.md',):
            path = self.task / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('content')

    def record(self, run_id, finished_at, **extra):
        path = self.task / 'result/runs' / run_id / 'record.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {'run_id': run_id, 'execution': {'status': 'completed'}, **extra}
        if finished_at is not None:
            value['finished_at'] = finished_at.isoformat() if isinstance(finished_at, datetime) else finished_at
        path.write_text(json.dumps(value))
        return path

    def report(self, text, updated_at, purpose='reproduce'):
        path = self.task / ('result/FIX.md' if purpose == 'repair' else 'result/REPRO.md')
        path.write_text(text)
        os.utime(path, (updated_at.timestamp(), updated_at.timestamp()))
        return path

    def checkpoint(self, purpose='reproduce', initial=None):
        return delivery.checkpoint(self.task, self.run, initial or {}, 'running', purpose)

    def test_report_updated_before_new_run_is_stale_then_recovers(self):
        before = delivery.inventory(self.task)
        self.record('run-first', self.base)
        self.report('第一轮 run-first 已完成。', self.base + timedelta(seconds=10))
        self.assertEqual(self.checkpoint(initial=before)['delivery'], 'files_present')
        self.record('run-second', self.base + timedelta(seconds=20), execution={'status': 'error'})
        value = self.checkpoint(initial=before)
        self.assertEqual(value['delivery'], 'incomplete')
        self.assertEqual(value['report_freshness']['status'], 'stale')
        self.assertTrue(any('过期' in warning for warning in value['warnings']))
        path = self.report('run-second 失败，下一步检查日志。', self.base + timedelta(seconds=25))
        before_bytes = path.read_bytes()
        value = self.checkpoint(initial=before)
        fresh = value['report_freshness']
        self.assertEqual(fresh['status'], 'current')
        self.assertEqual(fresh['latest_run']['run_id'], 'run-second')
        self.assertTrue(fresh['includes_latest_run'])
        self.assertTrue(fresh['updated_after_run'])
        self.assertAlmostEqual(fresh['update_delay_seconds'], 5, places=5)
        self.assertEqual(fresh['report']['sha256'], value['files']['result/REPRO.md']['sha256'])
        self.assertEqual(value['delivery'], 'files_present')
        self.assertEqual(path.read_bytes(), before_bytes)
        self.assertEqual((self.run / 'REPRO.partial.md').read_bytes(), before_bytes)

    def test_latest_run_uses_timezone_aware_finish_not_directory_order(self):
        self.record('run-z-old', self.base.astimezone(timezone(timedelta(hours=8))))
        self.record('run-a-latest', self.base + timedelta(seconds=10), execution={'status': 'blocked'})
        self.record('run-zz-active', None)
        self.report('run-a-latest 环境未通过。', self.base + timedelta(seconds=20))
        fresh = self.checkpoint()['report_freshness']
        self.assertEqual(fresh['status'], 'current')
        self.assertEqual(fresh['latest_run']['run_id'], 'run-a-latest')
        self.assertEqual(fresh['update_delay_seconds'], 10)

    def test_recent_report_must_include_exact_latest_run_and_follow_its_end(self):
        self.record('run-new', self.base)
        for text, seconds, includes, after in (
            ('只有旧结论', 5, False, True),
            ('引用了 run-newer，不是本轮', 5, False, True),
            ('run-new 尚未完成', -5, True, False),
        ):
            with self.subTest(text=text):
                self.report(text, self.base + timedelta(seconds=seconds))
                fresh = self.checkpoint()['report_freshness']
                self.assertEqual(fresh['status'], 'stale')
                self.assertEqual(fresh['includes_latest_run'], includes)
                self.assertEqual(fresh['updated_after_run'], after)
                self.assertIsNone(fresh['update_delay_seconds'])

    def test_bad_or_future_finish_times_cannot_be_current(self):
        self.record('run-valid', self.base)
        self.report('run-valid run-bad 已检查。', self.base + timedelta(seconds=10))
        for finish in ('bad', '', self.base.replace(tzinfo=None).isoformat(),
                       datetime.now(timezone.utc) + timedelta(hours=1)):
            with self.subTest(finish=finish):
                self.record('run-bad', finish)
                value = self.checkpoint()
                self.assertEqual(value['report_freshness']['status'], 'unknown')
                self.assertTrue(value['report_freshness']['record_errors'])
                self.assertEqual(value['delivery'], 'incomplete')

    def test_malformed_record_is_unknown_but_active_record_is_ignored(self):
        path = self.record('run-active', None)
        self.assertEqual(self.checkpoint()['report_freshness']['status'], 'no_completed_run')
        for content in ('{', '[]', '{"run_id":"run-active","finished_at":null}',
                        json.dumps({'run_id': 'run-other', 'finished_at': self.base.isoformat()})):
            with self.subTest(content=content):
                path.write_text(content)
                self.assertEqual(self.checkpoint()['report_freshness']['status'], 'unknown')

    def test_repeated_checkpoint_keeps_one_current_snapshot(self):
        self.record('run-new', self.base)
        self.report('本轮run-new完成，待验收。', self.base + timedelta(seconds=10))
        first = self.checkpoint()
        self.assertEqual(first['report_freshness']['status'], 'current')
        self.assertEqual(self.checkpoint(), first)
        self.assertEqual(sorted(p.name for p in self.run.iterdir()), ['DELIVERY.md', 'REPRO.partial.md', 'delivery.json'])

    def test_missing_report_and_future_report_time(self):
        self.record('run-new', self.base)
        path = self.report('run-new 完成。', datetime.now(timezone.utc) + timedelta(hours=1))
        self.assertEqual(self.checkpoint()['report_freshness']['status'], 'unknown')
        path.unlink()
        self.assertEqual(self.checkpoint()['report_freshness']['status'], 'missing')

    def test_repair_freshness_tracks_fix_instead_of_repro(self):
        self.record('run-repaired', self.base)
        self.report('run-repaired 已验证。', self.base + timedelta(seconds=5))
        self.report('仍在修改。', self.base - timedelta(seconds=5), purpose='repair')
        self.assertEqual(self.checkpoint('repair')['report_freshness']['status'], 'stale')
        self.report('run-repaired 已验证，视频待核对。', self.base + timedelta(seconds=15), purpose='repair')
        value = self.checkpoint('repair')
        self.assertEqual(value['report_freshness']['status'], 'current')
        self.assertEqual(value['report_freshness']['report']['path'], 'result/FIX.md')
        self.assertEqual(value['report_freshness']['update_delay_seconds'], 15)
        self.assertTrue((self.run / 'FIX.partial.md').exists())

    def test_missing_stale_broken_and_complete_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / 'task'; task.mkdir()
            run = Path(tmp) / 'run'; run.mkdir()
            before = delivery.inventory(task)
            value = delivery.checkpoint(task, run, before, 'timeout')
            self.assertEqual(len(value['missing']), 5)
            for name in delivery.REQUIRED:
                path = task / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('content')
            before = delivery.inventory(task)
            self.assertTrue(delivery.checkpoint(task, run, before, 'completed')['warnings'])
            (task / 'result/REPRO.md').write_text('[missing](missing.png)')
            self.assertIn('链接', delivery.checkpoint(task, run, before, 'interrupted')['warnings'][0])
            (task / 'result/REPRO.md').write_text('已运行；仍需人工验收。')
            value = delivery.checkpoint(task, run, before, 'completed')
            self.assertEqual(value['delivery'], 'files_present')
            self.assertEqual(value['review'], 'pending')
            self.assertTrue((run / 'REPRO.partial.md').exists())
