#!/usr/bin/env python3
"""从 Pi JSONL 会话重算统计；不以进程退出码判断复现成功。"""
import argparse
import json
import math
import os
import tempfile
from pathlib import Path

FIELDS = ('input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens')


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def analyze(folder, *, folders=None):
    warnings, records, tools, models = [], [], {}, set()
    seen, sessions, entries = {}, set(), []
    roots = [Path(p).resolve() for p in folders] if folders is not None else [Path(folder).resolve()]
    files = sorted({p for root in roots for p in root.rglob('*.jsonl')})
    for file in files:
        sid = None
        if file.is_symlink() or not any(file.resolve().is_relative_to(root) for root in roots):
            warnings.append(f'{file.name}: 不读取会话目录外链接')
            continue
        try:
            lines = file.read_text().splitlines()
        except (OSError, UnicodeError) as exc:
            warnings.append(f'{file.name}: {exc}')
            continue
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError('条目不是对象')
            except ValueError:
                warnings.append(f'{file.name}:{line_no}: 无效或截断 JSON')
                continue
            if item.get('type') == 'session':
                sid = item.get('id')
                if not isinstance(sid, str) or not sid:
                    sid = None
                elif item.get('version') != 3:
                    warnings.append(f'{file.name}: 未验证的会话版本 {item.get("version")}')
                if sid:
                    sessions.add(sid)
                continue
            if not sid:
                warnings.append(f'{file.name}:{line_no}: 缺少有效会话头')
                continue
            identity = (sid, item.get('id'))
            if not isinstance(identity[1], str):
                warnings.append(f'{file.name}:{line_no}: 缺少 entry id')
                continue
            if identity in seen:
                if seen[identity] != item:
                    warnings.append(f'{file.name}:{line_no}: 同一 entry id 内容冲突')
                continue
            seen[identity] = item
            entries.append((sid, item))
    for sid, item in entries:
        kind, message = item.get('type'), item.get('message', {})
        if not isinstance(message, dict):
            warnings.append(f'{sid}/{item["id"]}: message 格式错误')
            continue
        role = message.get('role')
        if kind == 'message' and role == 'assistant':
            models.add((str(message.get('provider', 'unknown')), str(message.get('model', 'unknown'))))
            records.append(message.get('usage'))
            content = message.get('content', [])
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get('type') == 'toolCall':
                    key = (sid, block.get('id'))
                    tools[key] = {'session_id': sid, 'entry_id': item['id'], 'tool_call_id': block.get('id'),
                                  'name': block.get('name'), 'result': 'missing', 'category': 'unknown'}
        elif kind in ('usage', 'compaction', 'branch_summary'):
            records.append(item.get('usage'))
    for sid, item in entries:
        message = item.get('message', {})
        if isinstance(message, dict) and message.get('role') == 'toolResult':
            key = (sid, message.get('toolCallId'))
            if key in tools:
                tools[key]['result'] = 'error' if message.get('isError') else 'returned'
                tools[key]['result_entry_id'] = item['id']
    if not sessions:
        warnings.append('未采集到有效 Pi 会话，不能把用量记为零。')
    if sessions and not records:
        warnings.append('会话中没有模型用量记录。')
    known = {key: 0 for key in (*FIELDS, 'cost')}
    missing = {key: 0 for key in known}
    for record in records:
        record = record if isinstance(record, dict) else {}
        for key in known:
            cost = record.get('cost')
            value = cost.get('total') if key == 'cost' and isinstance(cost, dict) else record.get(key) if key != 'cost' else None
            if number(value):
                known[key] += value
            else:
                missing[key] += 1
    input_sizes = [sum(record[k] for k in ('input', 'cacheRead', 'cacheWrite')) for record in records
                   if isinstance(record, dict) and all(number(record.get(k)) for k in ('input', 'cacheRead', 'cacheWrite'))]
    complete = bool(sessions and records) and not warnings
    totals = {key: value if complete and missing[key] == 0 else None for key, value in known.items()}
    return {'schema': 1, 'session_ids': sorted(sessions), 'session_files': len(files),
            'usage_records': len(records), 'request_input_tokens_peak': max(input_sizes) if complete and len(input_sizes) == len(records) else None, 'totals': totals, 'known_totals': known,
            'missing_fields': missing, 'warnings': warnings, 'models': sorted(models),
            'tool_calls': len(tools), 'tool_results_missing': sum(t['result'] == 'missing' for t in tools.values()),
            'compactions': sum(i.get('type') == 'compaction' for _, i in entries), 'tools': list(tools.values()),
            'note': '用量仅来自会话，含所有分支和压缩调用；费用是 Pi 报告的估算，非账单。'}


def save(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.' + path.name + '-', delete=False) as temporary:
        try:
            json.dump(data, temporary, ensure_ascii=False, indent=2)
            temporary.write('\n')
            temporary.close()
            os.replace(temporary.name, path)
        finally:
            Path(temporary.name).unlink(missing_ok=True)


def refresh(run):
    run = Path(run)
    metrics = analyze(run / 'sessions')
    state = json.loads((run / 'run.json').read_text())
    metrics['elapsed_seconds'] = state.get('elapsed_seconds')
    metrics['execution_status'] = state.get('status')
    metrics['phases'] = {p.name: analyze(p) for p in sorted((run / 'sessions').glob('*')) if p.is_dir()}
    phase_sessions = set()
    for phase, values in metrics['phases'].items():
        overlap = phase_sessions.intersection(values['session_ids'])
        phase_sessions.update(values['session_ids'])
        if overlap:
            metrics['warnings'].append('不同阶段复用了同一会话：' + ', '.join(sorted(overlap)))
            metrics['totals'] = {key: None for key in metrics['totals']}
        if values['request_input_tokens_peak'] is None:
            metrics['request_input_tokens_peak'] = None
        for key in metrics['totals']:
            if values['totals'][key] is None:
                metrics['totals'][key] = None
        metrics['warnings'].extend(f'{phase}: {warning}' for warning in values['warnings'])
    save(run / 'tools.json', metrics.pop('tools'))
    # 每阶段 tools 供审计；顶层明细只在 tools.json 保存。
    save(run / 'metrics.json', metrics)
    return metrics


def summarize(root):
    rows = []
    for path in sorted(Path(root).rglob('run.json')):
        run = json.loads(path.read_text())
        if run.get('schema') != 1 or 'workflow' not in run:
            continue
        metrics = refresh(path.parent)
        review_path = path.with_name('review.json')
        review = json.loads(review_path.read_text()) if review_path.exists() else {}
        valid_review = (review.get('outcome') in ('reproduced', 'not_reproduced', 'blocked')
                        and isinstance(review.get('reviewer'), str) and bool(review['reviewer'].strip())
                        and isinstance(review.get('evidence'), list) and bool(review['evidence'])
                        and all(isinstance(e, str) and e.strip() for e in review['evidence']))
        accepted = run.get('purpose', 'reproduce') == 'reproduce' and valid_review and review['outcome'] == 'reproduced'
        rows.append({'run': str(path.parent), 'workflow': run['workflow'], 'purpose': run.get('purpose', 'reproduce'), 'pair_id': run.get('pair_id'),
                     'status': run['status'], 'elapsed_seconds': metrics['elapsed_seconds'],
                     'cost': metrics['totals']['cost'], 'known_cost': metrics['known_totals']['cost'],
                     'accepted_reproduction': accepted, 'review_outcome': review['outcome'] if valid_review else 'pending'})
    groups = {}
    for workflow in ('single', 'split'):
        selected = [r for r in rows if r['workflow'] == workflow and r['purpose'] == 'reproduce']
        complete = bool(selected) and all(r['cost'] is not None for r in selected)
        successes = sum(r['accepted_reproduction'] for r in selected)
        cost = sum(r['cost'] for r in selected) if complete else None
        groups[workflow] = {'attempts': len(selected), 'accepted_reproductions': successes,
                            'total_cost': cost, 'cost_per_accepted_reproduction': cost / successes if cost is not None and successes else None,
                            'reviews_pending': sum(r['review_outcome'] == 'pending' for r in selected)}
    pairs = []
    for pair_id in sorted({r['pair_id'] for r in rows if r['pair_id'] and r['purpose'] == 'reproduce'}):
        members = [r for r in rows if r['pair_id'] == pair_id and r['purpose'] == 'reproduce']
        if len(members) != 2 or {r['workflow'] for r in members} != {'single', 'split'}:
            pairs.append({'pair_id': pair_id, 'status': 'incomplete_or_duplicate'})
            continue
        a = next(r for r in members if r['workflow'] == 'single')
        b = next(r for r in members if r['workflow'] == 'split')
        pairs.append({'pair_id': pair_id, 'status': 'pending_review' if any(r['review_outcome'] == 'pending' for r in members) else 'reviewed',
                      'single_accepted': a['accepted_reproduction'], 'split_accepted': b['accepted_reproduction'],
                      'split_minus_single_cost': b['cost'] - a['cost'] if a['cost'] is not None and b['cost'] is not None else None})
    return {'schema': 1, 'runs': rows, 'groups': groups, 'pairs': pairs,
            'note': '描述性汇总，不自动得出 A/B 因果结论；未验收不能当作复现成功；修复任务保留明细，但不计入复现分组及配对统计。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('analyze', 'summarize'))
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    print(json.dumps(refresh(args.path) if args.command == 'analyze' else summarize(args.path), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
