"""协议与隔离测试均使用合成材料；不代表真实评审准确性。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import evidence
import review_replay as replay


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'input'
        self.root.mkdir()
        self.counter = 0
        self.run = 'run-fixture-1'
        self.record = {
            'schema': 1, 'run_id': self.run, 'problem_sha256': '',
            'execution': {'status': 'completed'},
            'scenario': {'files': {}, 'definition': {'schema': 2, 'required_checks': [
                {'id': 'a', 'kind': 'target', 'expected': '8400 becomes 5600'},
                {'id': 'b', 'kind': 'regression', 'expected': 'navigation works'}]}},
            'code': {'files': {}},
            'checks': [{'id': 'a', 'status': 'failed', 'evidence': ['obs.txt']},
                       {'id': 'b', 'status': 'passed', 'evidence': ['obs.txt']}],
            'evidence': {'artifacts': []}, 'review': {'hidden_answer': 'SECRET_DO_NOT_SEND'}}
        self.manifest = {'schema': 1, 'case_id': 'case-001', 'run_id': self.run,
                         'origin': 'synthetic', 'issue_url': 'https://example.invalid/issues/1',
                         'frozen_at': '2026-01-01T00:00:00+00:00', 'materials': []}
        self.add('issue', 'Expected 8400 -> 5600\nObserved 8000\n')
        self.add('scenario', 'assert value != previous\n', snapshot_path='test.py')
        self.add('source', 'value = 8000\n', snapshot_path='app.py')
        self.add('report', f'run {self.run}\nExpected exactly 5600; reproduction claimed.\n')
        self.add('evidence', 'observed 8000\n', evidence_path='obs.txt')
        (self.root / 'formal-state.json').write_text('{"accepted":false}')
        (self.base / 'labels.json').write_text('SECRET_DO_NOT_SEND')
        self.sync()

    def add(self, role, content, **extra):
        name = f'm{len(self.manifest["materials"])+1}'
        data = content.encode() if isinstance(content, str) else content
        (self.root / name).write_bytes(data)
        sha = evidence.digest(data)
        item = {'id': name, 'path': name, 'role': role, 'format': 'text', 'sha256': sha, **extra}
        self.manifest['materials'].append(item)
        if role == 'issue':
            self.record['problem_sha256'] = sha
        elif role in ('source', 'scenario'):
            self.record['code' if role == 'source' else 'scenario']['files'][extra['snapshot_path']] = {'kind': 'file', 'sha256': sha}
        elif role == 'evidence':
            self.record['evidence']['artifacts'].append({'path': extra['evidence_path'], 'sha256': sha, 'bytes': len(data)})
        return item

    def sync(self):
        data = replay.encoded(self.record)
        (self.root / 'record.json').write_bytes(data)
        self.manifest['record'] = {'path': 'record.json', 'sha256': evidence.digest(data)}
        (self.root / 'manifest.json').write_bytes(replay.encoded(self.manifest))

    def invoke(self, response=None, **kwargs):
        self.counter += 1
        out = self.base / f'out-{self.counter}'
        if response is not None:
            path = self.base / 'response.json'
            path.write_text(response if isinstance(response, str) else json.dumps(response))
        else:
            path = None
        result = replay.replay(self.root, 'manifest.json', out, path, **kwargs)
        return result, out

    def response(self):
        result, out = self.invoke()
        request = json.loads((out / 'request.json').read_text())
        ref = {'material_id': 'm1', 'start_line': 1, 'end_line': 1, 'quote': 'Expected 8400 -> 5600'}
        return {'schema': 1, 'mode': 'synthetic', 'status': 'completed',
                'request_sha256': result['request_sha256'], 'case_id': request['case_id'], 'run_id': self.run,
                'decision': 'suggest_pass', 'concerns': [{'text': 'Check exact value', 'certainty': 'fact', 'citations': [ref]}],
                'findings': [], 'gaps': []}

    def test_prepares_only_allowlisted_content_without_mutation_or_answer(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        result, out = self.invoke()
        self.assertEqual(result['status'], 'prepared')
        self.assertEqual(result['mechanical']['status'], 'complete')
        request = (out / 'request.json').read_text()
        self.assertNotIn('SECRET_DO_NOT_SEND', request)
        self.assertNotIn(str(self.root), request)
        self.assertNotIn('formal-state', request)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        self.assertFalse(result['model_called'])
        self.assertIsNone(result['usage']['cost'])
        self.assertIsNone(result['recommendation'])

    def test_fixture_pass_remains_advisory_and_usage_unknown(self):
        response = self.response()
        result, _ = self.invoke(response)
        self.assertEqual(result['status'], 'validated')
        self.assertEqual(result['recommendation'], 'suggest_pass')
        self.assertFalse(result['formal_state_changed'])
        self.assertEqual(result['usage'], {'tokens': None, 'cost': None})
        self.assertEqual((self.root / 'formal-state.json').read_text(), '{"accepted":false}')

    def test_cited_assertion_mismatch_is_protocol_valid_not_semantically_scored(self):
        response = self.response()
        response.update(decision='suggest_return', findings=[{
            'text': 'Assertion only checks change, not exact expected value', 'certainty': 'fact',
            'citations': [{'material_id': 'm2', 'start_line': 1, 'end_line': 1, 'quote': 'assert value != previous'}]}])
        result, _ = self.invoke(response)
        self.assertEqual(result['recommendation'], 'suggest_return')
        self.assertNotIn('accuracy', result)

    def test_bad_citations_and_wrong_input_never_pass(self):
        valid = self.response()
        variants = []
        for key, value in [('material_id', 'labels'), ('start_line', 0), ('end_line', 50), ('quote', 'invented')]:
            value_response = copy.deepcopy(valid)
            value_response['concerns'][0]['citations'][0][key] = value
            variants.append(value_response)
        for key, value in [('request_sha256', 'x'), ('case_id', 'case-002'), ('run_id', 'run-other'),
                           ('decision', 'accepted'), ('mode', 'real'), ('concerns', [])]:
            value_response = copy.deepcopy(valid)
            value_response[key] = value
            variants.append(value_response)
        variants.extend(['not json', '[]', '{}'])
        for variant in variants:
            with self.subTest(variant=variant):
                result, _ = self.invoke(variant)
                self.assertEqual(result['status'], 'protocol_error')
                self.assertIsNone(result['recommendation'])

    def test_simulated_timeouts_and_budget_failures_are_not_undetermined(self):
        for status in ('timeout', 'budget_exhausted', 'error'):
            response = self.response()
            response.update(status=status, error='synthetic failure')
            result, _ = self.invoke(response)
            self.assertEqual(result['status'], 'adapter_failure')
            self.assertEqual(result['review']['reason'], status)
            self.assertIsNone(result['recommendation'])

    def test_undetermined_requires_a_gap(self):
        response = self.response()
        response['decision'] = 'undetermined'
        result, _ = self.invoke(response)
        self.assertEqual(result['status'], 'protocol_error')
        response['gaps'] = ['Need independent behavior evidence']
        result, _ = self.invoke(response)
        self.assertEqual(result['recommendation'], 'undetermined')

    def test_mechanical_errors_cannot_be_overridden(self):
        for change in ('report', 'checks', 'execution', 'omitted_evidence', 'missing_role'):
            with self.subTest(change=change):
                record, manifest = copy.deepcopy(self.record), copy.deepcopy(self.manifest)
                if change == 'report':
                    (self.root / 'm4').write_text('run-other')
                    self.manifest['materials'][3]['sha256'] = evidence.digest(b'run-other')
                elif change == 'checks':
                    self.record['checks'][0]['status'] = 'inconclusive'
                elif change == 'execution':
                    self.record['execution']['status'] = 'error'
                elif change == 'omitted_evidence':
                    self.manifest['materials'] = self.manifest['materials'][:-1]
                else:
                    self.manifest['materials'] = [x for x in self.manifest['materials'] if x['role'] != 'source']
                self.sync()
                response = self.response()
                result, _ = self.invoke(response)
                self.assertEqual(result['status'], 'mechanically_blocked')
                self.assertIsNone(result['recommendation'])
                self.record, self.manifest = record, manifest
                (self.root / 'm4').write_text(f'run {self.run}\nExpected exactly 5600; reproduction claimed.\n')
                self.sync()

    def test_missing_corrupt_and_mismatched_source_fail_closed(self):
        for change in ('missing', 'hash', 'snapshot', 'issue', 'record'):
            with self.subTest(change=change):
                record, manifest = copy.deepcopy(self.record), copy.deepcopy(self.manifest)
                if change == 'missing': self.manifest['materials'][0]['path'] = 'missing'
                elif change == 'hash': self.manifest['materials'][0]['sha256'] = '0'*64
                elif change == 'snapshot': self.record['code']['files']['app.py']['sha256'] = '0'*64
                elif change == 'issue': self.record['problem_sha256'] = '0'*64
                else: self.record['run_id'] = 'run-other'
                self.sync()
                result, out = self.invoke()
                self.assertEqual(result['status'], 'input_error')
                self.assertFalse((out / 'request.json').exists())
                self.record, self.manifest = record, manifest
                self.sync()

    def test_paths_symlinks_special_files_and_forbidden_role_rejected(self):
        import os
        (self.root / 'link').symlink_to(self.base / 'labels.json')
        os.mkfifo(self.root / 'pipe')
        for name in ('../labels.json', str(self.base / 'labels.json'), 'link', 'pipe'):
            self.manifest['materials'][0]['path'] = name
            self.sync()
            result, _ = self.invoke()
            self.assertEqual(result['status'], 'input_error')
        self.manifest['materials'][0]['path'] = 'm1'
        self.manifest['materials'][0]['role'] = 'label'
        self.sync()
        result, _ = self.invoke()
        self.assertEqual(result['status'], 'input_error')

    def test_binary_evidence_is_not_claimed_viewed_or_citable(self):
        item = self.add('evidence', b'\x89PNG\x00', evidence_path='image.png')
        item['format'] = 'binary'
        self.sync()
        response = self.response()
        result, _ = self.invoke(response)
        self.assertEqual(result['status'], 'mechanically_blocked')
        response['concerns'][0]['citations'][0]['material_id'] = item['id']
        result, _ = self.invoke(response)
        self.assertEqual(result['status'], 'protocol_error')

    def test_output_cannot_overwrite_input_or_previous_results(self):
        for path in (self.root / 'new-result', self.root / 'm1'):
            with self.assertRaises(ValueError):
                replay.replay(self.root, 'manifest.json', path)
        result, out = self.invoke()
        with self.assertRaises(ValueError):
            replay.replay(self.root, 'manifest.json', out)

    def test_limits_and_actual_elapsed_deadline(self):
        for kwargs in ({'max_bytes': 10}, {'seconds': 0}, {'seconds': float('nan')}):
            result, _ = self.invoke(**kwargs)
            self.assertEqual(result['status'], 'input_error')
        reader = replay.Reader(self.root)
        reader.deadline = 0
        with self.assertRaises(TimeoutError): reader.read('m1')
        with patch.object(replay.Reader, 'check_time', side_effect=TimeoutError('expired')):
            result, _ = self.invoke()
        self.assertEqual(result['status'], 'budget_exhausted')

    def test_response_reads_share_byte_budget_and_missing_response_is_protocol_error(self):
        response = self.response()
        consumed = sum((self.root / name).stat().st_size
                       for name in ('manifest.json', 'record.json', 'm1', 'm2', 'm3', 'm4', 'm5'))
        result, _ = self.invoke(response, max_bytes=consumed)
        self.assertEqual(result['status'], 'protocol_error')
        self.assertIn('预算', result['error'])
        result = replay.replay(self.root, 'manifest.json', self.base/'missing-response',
                               self.base/'does-not-exist.json')
        self.assertEqual(result['status'], 'protocol_error')

    def test_legacy_schema_stays_incomplete(self):
        self.record['scenario']['definition'] = {'schema': 1}
        self.sync()
        response = self.response()
        result, _ = self.invoke(response)
        self.assertEqual(result['status'], 'mechanically_blocked')
        self.assertIsNone(result['recommendation'])
        self.assertIn('schema 2', ' '.join(result['mechanical']['errors']))

    def test_only_issue_citations_can_establish_original_concerns(self):
        response = self.response()
        response['concerns'][0]['citations'] = [
            {'material_id': 'm3', 'start_line': 1, 'end_line': 1, 'quote': 'value = 8000'}]
        result, _ = self.invoke(response)
        self.assertEqual(result['status'], 'protocol_error')

    def test_cli_reads_fixture_and_returns_machine_readable_record(self):
        command = [sys.executable, replay.__file__, '--root', str(self.root), '--manifest', 'manifest.json', '--out', str(self.base/'cli')]
        proc = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['status'], 'prepared')
        self.assertEqual(subprocess.run(command, capture_output=True, timeout=10).returncode, 2)


if __name__ == '__main__':
    unittest.main()
