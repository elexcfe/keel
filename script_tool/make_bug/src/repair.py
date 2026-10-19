"""修复参考准入与交付核查；不调用模型，不代写 Pi 报告，不自动作语义验收。"""
import argparse
import json
from pathlib import Path
import sys

import evidence


def read_record(task, run_id):
    if not isinstance(run_id, str) or Path(run_id).name != run_id or not run_id.startswith('run-'):
        raise ValueError('运行编号无效。')
    folder = task / 'result/runs' / run_id
    path = evidence.local_file(task, f'result/runs/{run_id}/record.json')
    record = json.loads(path.read_text())
    if not isinstance(record, dict) or record.get('run_id') != run_id:
        raise ValueError('记录中的运行编号与目录不一致。')
    for item in record.get('evidence', {}).get('artifacts', []):
        artifact = evidence.local_file(folder, item['path'])
        if artifact.stat().st_size != item['bytes'] or evidence.file_digest(artifact) != item['sha256']:
            raise ValueError('原始证据已变化：' + item['path'])
    return record


def problems(record):
    errors = list(evidence.validate_checks(record)['errors'])
    if record.get('execution', {}).get('status') != 'completed':
        errors.append('执行未完成：' + record.get('execution', {}).get('reason', '未知'))
    errors.extend(record.get('integrity_warnings', []))
    errors.extend(record.get('evidence', {}).get('warnings', []))
    if not record.get('problem_sha256'):
        errors.append('缺少原问题摘要。')
    return errors


def baseline_errors(record):
    errors = problems(record)
    contract = evidence.required_checks(record.get('scenario', {}).get('definition', {}))
    kinds = {v['kind'] for v in contract.values()}
    if kinds != {'target', 'regression'}:
        errors.append('准入需要目标检查和必要回归检查。')
    checks = {v.get('id'): v for v in record.get('checks', [])}
    if not any(checks.get(k, {}).get('status') == 'failed' for k, v in contract.items() if v['kind'] == 'target'):
        errors.append('参考运行没有确认的目标失败。')
    if any(checks.get(k, {}).get('status') != 'passed' for k, v in contract.items() if v['kind'] == 'regression'):
        errors.append('参考运行的必要回归必须通过，不能忽略已有失败。')
    return errors


def current_matches(task, record, *, source=False):
    errors = []
    if evidence.file_digest(evidence.local_file(task, 'BUG.md')) != record['problem_sha256']:
        errors.append('原问题发生变化。')
    if evidence.scenario(task)['digest'] != record['scenario']['digest']:
        errors.append('场景、断言或数据条件发生变化，需要重新确认参考运行。')
    if source and evidence.source_snapshot(task / 'repo')['digest'] != record['code']['digest']:
        errors.append('当前代码与验证版本不一致。')
    return errors


def admit(task, run_id, reviewer, review_evidence):
    """仅外层审核者显式调用；不能由 Pi 的自述或第一次失败自动触发。"""
    if not reviewer.strip() or not review_evidence or any(not x.strip() for x in review_evidence):
        raise ValueError('需要审核者和非空审阅依据。')
    record = read_record(task, run_id)
    errors = baseline_errors(record) + current_matches(task, record, source=True)
    if errors:
        raise ValueError('\n'.join(errors))
    folder = task / 'result/runs' / run_id
    return {'schema': 1, 'status': 'accepted_reproduction', 'reviewer': reviewer,
            'evidence': review_evidence, 'baseline_run': run_id,
            'files': {name: evidence.file_digest(evidence.local_file(folder, name))
                      for name in ('record.json', 'scenario.tar.gz', 'code/source.tar.gz')}}


def load_reference(task, path):
    reference = json.loads(path.read_text())
    if (not isinstance(reference, dict) or reference.get('schema') != 1 or reference.get('status') != 'accepted_reproduction'
            or not isinstance(reference.get('files'), dict)
            or not isinstance(reference.get('reviewer'), str) or not reference['reviewer'].strip()
            or not isinstance(reference.get('evidence'), list) or not reference['evidence']
            or any(not isinstance(x, str) or not x.strip() for x in reference['evidence'])):
        raise ValueError('需要外层明确验收的复现参考。')
    record = read_record(task, reference.get('baseline_run'))
    folder = task / 'result/runs' / record['run_id']
    for name in ('record.json', 'scenario.tar.gz', 'code/source.tar.gz'):
        if evidence.file_digest(evidence.local_file(folder, name)) != reference.get('files', {}).get(name):
            raise ValueError('准入参考材料已变化：' + name)
    errors = baseline_errors(record)
    if errors:
        raise ValueError('\n'.join(errors))
    return record


def compare(baseline, candidate):
    errors = problems(candidate)
    if candidate.get('problem_sha256') != baseline.get('problem_sha256'):
        errors.append('原问题与参考不一致。')
    if candidate.get('scenario', {}).get('digest') != baseline['scenario']['digest']:
        errors.append('场景与参考不一致。')
    failed = [c['id'] for c in candidate.get('checks', []) if c.get('id') and c.get('status') == 'failed']
    execution = candidate.get('execution', {}).get('status')
    status = ('blocked' if execution == 'blocked' else 'invalid') if errors else ('checks_failed' if failed else 'checks_passed')
    return {'status': status, 'baseline_run': baseline['run_id'], 'run_id': candidate['run_id'],
            'failed_checks': failed, 'errors': errors, 'review': 'pending'}


def finalize(task, baseline, initial_runs, initial_report=None):
    """只核查本次新运行，检查通过仍待外层语义验收。"""
    errors = []
    records = [read_record(task, p.parent.name) for p in (task / 'result/runs').glob('*/record.json')
               if p.parent.name not in initial_runs]
    if not records:
        return {'status': 'incomplete', 'errors': ['本次没有新的正式验证运行。'], 'review': 'pending'}
    latest = max(records, key=lambda r: (r['finished_at'], r['run_id']))
    result = compare(baseline, latest)
    errors.extend(result['errors'])
    if result['status'] != 'checks_passed':
        errors.append('最终运行未通过：' + result['status'])
    errors.extend(current_matches(task, latest, source=True))
    try:
        report = evidence.local_file(task, 'result/FIX.md')
        content = report.read_text()
        if not content.strip() or (initial_report and evidence.file_digest(report) == initial_report):
            errors.append('修复报告为空或与运行前一致。')
        if any(run_id not in content for run_id in (baseline['run_id'], latest['run_id'])):
            errors.append('修复报告未引用正确的前后运行编号。')
        result['report_sha256'] = evidence.file_digest(report)
    except (OSError, ValueError, UnicodeError) as exc:
        errors.append('缺少有效修复报告：' + str(exc))
    return {**result, 'status': 'incomplete' if errors else 'awaiting_review', 'errors': errors,
            'code_digest': latest['code']['digest'], 'reference_code_digest': baseline['code']['digest'],
            'human_intervention': 'unreviewed',
            'note': '只核查结构、证据引用和版本；原因、覆盖、画面、回归充分性及人工介入待独立验收。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', type=Path, required=True)
    parser.add_argument('--run', required=True, help='经独立审阅的失败参考运行编号')
    parser.add_argument('--reviewer', required=True)
    parser.add_argument('--evidence', action='append', required=True, help='审阅依据，可重复；不自动判断其语义')
    parser.add_argument('--output', type=Path, required=True, help='任务目录外的准入记录，拒绝覆盖')
    args = parser.parse_args()
    try:
        if args.output.resolve().is_relative_to(args.task.resolve()):
            raise ValueError('准入记录必须放在任务目录外。')
        value = admit(args.task.resolve(), args.run, args.reviewer, args.evidence)
        with args.output.open('x') as f:
            f.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        print(args.output)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('无法准入：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
