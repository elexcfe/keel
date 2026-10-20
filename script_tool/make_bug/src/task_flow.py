"""Keel 任务入口：关联既有执行记录，显式准入和验收，不自动调用下一阶段。"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
import uuid

import evidence
import repair
import run_pi
import session_stats


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('预算必须是有限正数。')
    return value


def local(root, name):
    return evidence.local_file(root, name)


def load(control):
    state = read(local(control, 'task.json'))
    if state.get('schema') != 1:
        raise ValueError('不支持的任务引用版本。')
    task = (control / state['task_path']).resolve()
    if read(local(task, '.keel-task.json'))['task_id'] != state['task_id']:
        raise ValueError('任务身份不匹配。')
    return state, task


@contextmanager
def locked(control):
    with (control / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('任务入口正在操作；请先查询状态，不要重复启动。') from exc
        yield


def init(control, task, seconds, cost, review_seconds):
    task = task.resolve()
    if control.resolve().is_relative_to(task):
        raise ValueError('控制目录必须在 Pi 任务外。')
    local(task, 'BUG.md')
    limits = {'execution_seconds': positive(seconds), 'cost_usd': positive(cost),
              'review_seconds': positive(review_seconds)}
    # 持久身份阻止重新 init 静默重置预算；随任务和控制目录一起迁移。
    identity = {'task_id': str(uuid.uuid4())}
    control.mkdir(parents=True, exist_ok=False)
    try:
        with (task / '.keel-task.json').open('x') as f:
            json.dump(identity, f)
    except OSError:
        control.rmdir()
        raise
    state = {'schema': 1, **identity, 'task_path': os.path.relpath(task, control),
             'created_at': now(), 'limits': limits, 'attempts': [], 'reference': None,
             'reviews': [], 'note': '预算仅覆盖本入口启动的执行；关联前及绕过入口的调用不计入，费用是会话估算软上限。'}
    session_stats.save(control / 'task.json', state)
    return state


def attempts(control, state):
    return [read(local(control, path)) for path in state['attempts']]


def usage(control, state):
    values = attempts(control, state)
    seconds = 0.0
    unknown_time = False
    unknown_cost = False
    folders = []
    for attempt in values:
        elapsed = attempt.get('elapsed_seconds')
        if not session_stats.number(elapsed):
            unknown_time = True
        else:
            seconds += elapsed
        root = control / attempt['runs_path']
        current = [p / 'sessions' for p in root.iterdir() if p.is_dir()]
        # 只凭新运行器的显式记录确认未启动。旧记录、损坏记录及已启动但
        # 缺会话的情况仍为未知；不能把“没有日志”直接当作零费用。
        if len(current) == 1 and 'ended_at' in attempt:
            try:
                run = read(local(control, str((current[0].parent / 'run.json').relative_to(control))))
                unstarted = (run.get('model_launch_attempted') is False
                             and run.get('ended_at') and run.get('status') in
                             ('error', 'failed', 'timeout', 'interrupted')
                             and not any(current[0].rglob('*.jsonl')))
                if unstarted:
                    continue
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        if session_stats.analyze(control, folders=current)['totals']['cost'] is None:
            unknown_cost = True
        folders.extend(current)
    metrics = session_stats.analyze(control, folders=folders) if folders else None
    cost = metrics['totals']['cost'] if metrics else 0.0
    if unknown_cost:
        cost = None
    review_used = sum(r.get('elapsed_seconds', 0) for r in state['reviews'])
    active_review = next((r for r in state['reviews'] if 'elapsed_seconds' not in r), None)
    if active_review:
        review_used += max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(active_review['started_at'])).total_seconds())
    return {'execution_seconds': None if unknown_time else seconds, 'cost_usd': cost,
            'known_cost_usd': metrics['known_totals']['cost'] if metrics else 0,
            'remaining_seconds': None if unknown_time else max(0, state['limits']['execution_seconds'] - seconds),
            'remaining_cost_usd': None if cost is None else max(0, state['limits']['cost_usd'] - cost),
            'review_seconds': review_used, 'remaining_review_seconds': max(0, state['limits']['review_seconds'] - review_used),
            'warnings': metrics['warnings'] if metrics else [], 'note': state['note']}


def latest_run(control, state):
    values = attempts(control, state)
    if not values:
        return None, None
    files = sorted((control / values[-1]['runs_path']).glob('*/run.json'))
    if len(files) != 1:
        return None, None
    return files[0], read(local(control, str(files[0].relative_to(control))))


def reference(control, state, task):
    if not state['reference']:
        raise ValueError('尚未显式准入复现。')
    return repair.load_reference(task, local(control, state['reference']))


def admit(control, state, task, run_id, reviewer, reasons):
    if state['reference']:
        raise ValueError('已有准入，不覆盖历史决定；场景改变需重新建任务并明确新授权。')
    value = repair.admit(task, run_id, reviewer, reasons)
    path = 'admission.json'
    session_stats.save(control / path, value)
    state['reference'] = path
    session_stats.save(control / 'task.json', state)
    return value


def execute(control, state, task, purpose):
    if any('elapsed_seconds' not in x for x in attempts(control, state)):
        raise ValueError('有未收尾执行；先查询并核对进程，使用 reconcile 登记中断，不能自动重试。')
    if any('elapsed_seconds' not in x for x in state['reviews']):
        raise ValueError('独立验收正在计时，请先记录结论。')
    if purpose == 'reproduce' and state['reference']:
        raise ValueError('已准入，不能重新复现覆盖条件。')
    if purpose == 'repair':
        baseline = reference(control, state, task)
        errors = repair.current_matches(task, baseline, source=True)
        if errors:
            raise ValueError('\n'.join(errors))
    budget = usage(control, state)
    if budget['remaining_seconds'] is None or budget['remaining_cost_usd'] is None:
        raise ValueError('执行用时或费用未知；保留原记录等待核实，不按零启动新调用。')
    if min(budget['remaining_seconds'], budget['remaining_cost_usd']) <= 0:
        raise ValueError('共享执行预算已耗尽。')
    key = uuid.uuid4().hex
    folder = control / 'attempts' / key
    (folder / 'runs').mkdir(parents=True)
    path = str((folder / 'attempt.json').relative_to(control))
    attempt = {'purpose': purpose, 'started_at': now(), 'runs_path': str((folder / 'runs').relative_to(control)),
               'reserved_seconds': budget['remaining_seconds']}
    session_stats.save(control / path, attempt)
    state['attempts'].append(path)
    session_stats.save(control / 'task.json', state)
    args = argparse.Namespace(task=task, workflow='single', purpose=purpose,
        reference=control / state['reference'] if purpose == 'repair' else None,
        runs_dir=folder / 'runs', isolate=None, cache_seed=None,
        timeout=budget['remaining_seconds'], wrap_up=600, prepare_timeout=900,
        cost_limit=budget['remaining_cost_usd'], pair_id=None)
    started = time.monotonic()
    try:
        # 直接复用现有进程树、会话监测与同会话检查；不另设短 bash 超时。
        run_pi.process_tree.enable()
        code = run_pi.run(args, [])
        attempt['exit_code'] = code
        return code
    finally:
        attempt.update(elapsed_seconds=time.monotonic() - started, ended_at=now())
        session_stats.save(control / path, attempt)


def reconcile(control, state, task):
    # 只有底层任务锁空闲才可登记中断，不杀未知进程；保守计入预留时长。
    with (task / '.pi-run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Pi 仍持有任务锁，不能登记中断。') from exc
        for path in state['attempts']:
            value = read(local(control, path))
            if 'elapsed_seconds' not in value:
                value.update(elapsed_seconds=value['reserved_seconds'], ended_at=now(),
                             reconciliation='中断后保守扣除本次预留时间；费用仍从会话读取。')
                session_stats.save(control / path, value)


def begin_review(control, state, task):
    import handoff
    if any('elapsed_seconds' not in x for x in state['reviews']):
        raise ValueError('验收已开始，重复操作不重置计时。')
    if usage(control, state)['remaining_review_seconds'] <= 0:
        raise ValueError('独立验收预算已耗尽。')
    report = handoff.inspect(control, state, task)
    state['reviews'].append({'started_at': now(), 'fingerprint': report['fingerprint'], 'outcome': 'pending'})
    session_stats.save(control / 'task.json', state)


def review_check(control, state, task):
    """在独立验收预算内重跑，独立记录不伪装成 Pi 新交付。"""
    import run_scenario
    if not state['reviews'] or 'elapsed_seconds' in state['reviews'][-1]:
        raise ValueError('请先 begin-review。')
    remaining = usage(control, state)['remaining_review_seconds']
    if remaining <= 0:
        raise ValueError('独立验收预算已耗尽。')
    reference(control, state, task)
    with (task / '.pi-run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Pi 仍在执行，不能独立重跑。') from exc
        run_pi.process_tree.enable()
        folder, record = run_scenario.run_once(task, min(300, remaining),
                                               reference=control / state['reference'])
    state['reviews'][-1].setdefault('checks', []).append({
        'run_id': record['run_id'], 'record_sha256': evidence.file_digest(folder / 'record.json')})
    session_stats.save(control / 'task.json', state)
    return {'run_id': record['run_id'], 'repair': record.get('repair'), 'execution': record['execution']}


def finish_review(control, state, task, outcome, reviewer, reasons):
    import handoff
    if not reviewer.strip() or not reasons or any(not x.strip() for x in reasons):
        raise ValueError('独立结论需要审核者和依据。')
    if not state['reviews'] or 'elapsed_seconds' in state['reviews'][-1]:
        raise ValueError('请先 begin-review；重复登记不能覆盖既有结论。')
    value = state['reviews'][-1]
    elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(value['started_at'])).total_seconds())
    report = handoff.inspect(control, state, task)
    if outcome == 'accepted' and (report['errors'] or report['fingerprint'] != value['fingerprint']):
        raise ValueError('验收期间材料变化或交付不完整，不能接受；可记录 rejected/blocked。')
    if usage(control, state)['remaining_review_seconds'] <= 0:
        outcome = 'timed_out'
    value.update(outcome=outcome, reviewer=reviewer, evidence=reasons, elapsed_seconds=elapsed, finished_at=now())
    session_stats.save(control / 'task.json', state)
    return value


def status(control, state, task, *, report=None):
    import handoff
    budget = usage(control, state)
    if report is None:
        report = handoff.inspect(control, state, task, budget=budget)
    run_path, run = latest_run(control, state)
    stage = 'awaiting_reproduction' if not run else ('awaiting_admission' if run.get('status') == 'completed' else 'incomplete')
    if state['reference']:
        stage = 'ready_for_repair'
        try:
            baseline = reference(control, state, task)
            if not run or run.get('purpose') != 'repair':
                if repair.current_matches(task, baseline, source=True):
                    stage = 'reference_outdated'
        except (OSError, ValueError, KeyError, TypeError):
            stage = 'reference_outdated'
    if run and run['purpose'] == 'repair':
        stage = 'awaiting_review' if not report['errors'] else 'incomplete'
    if any('elapsed_seconds' not in x for x in attempts(control, state)):
        stage = 'running_or_interrupted'
    if state['reviews']:
        review = state['reviews'][-1]
        stage = 'reviewing' if 'elapsed_seconds' not in review else review['outcome']
        if stage == 'accepted' and (report['errors'] or review['fingerprint'] != report['fingerprint']):
            stage = 'review_outdated'
    next_steps = {'awaiting_reproduction': '明确授权后执行 reproduce。', 'awaiting_admission': '审阅复现，使用 admit 登记准入。',
                  'ready_for_repair': '核对共享余额后显式执行 repair。', 'awaiting_review': 'begin-review 开始独立验收，再 review 登记结论。',
                  'incomplete': '将交付缺项交回 Pi；不自动重试或补写报告。', 'running_or_interrupted': '核对运行；确认退出后 reconcile，不自动续跑。',
                  'reviewing': '独立核对材料并及时 review 记录结论。', 'accepted': '独立验收已记录；由用户决定是否合入。'}
    return {'task_id': state['task_id'], 'stage': stage, 'budget': budget,
            'execution': ({'record': str(run_path.relative_to(control)), 'purpose': run.get('purpose'),
                           'status': run.get('status'), 'error': run.get('error'), 'exit_code': run.get('exit_code')}
                          if run else None),
            'next': next_steps.get(stage, '反馈待处理；保留历史决定，不自动续跑或扩预算。'),
            'delivery_errors': report['errors'], 'warnings': report['warnings']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', type=Path, required=True)
    subs = parser.add_subparsers(dest='command', required=True)
    p = subs.add_parser('init')
    p.add_argument('--task', type=Path, required=True)
    for name in ('execution-seconds', 'cost-usd', 'review-seconds'):
        p.add_argument('--' + name, type=positive, required=True)
    for name in ('status', 'reproduce', 'repair', 'handoff', 'begin-review', 'review-check', 'reconcile'):
        subs.add_parser(name)
    p = subs.add_parser('admit')
    p.add_argument('--run', required=True)
    for name in ('reviewer', 'evidence'):
        p.add_argument('--' + name, required=True, **({'action': 'append'} if name == 'evidence' else {}))
    p = subs.add_parser('review')
    p.add_argument('--outcome', choices=('accepted', 'rejected', 'blocked'), required=True)
    p.add_argument('--reviewer', required=True)
    p.add_argument('--evidence', action='append', required=True)
    args = parser.parse_args(argv)
    control = args.control.resolve()
    try:
        if args.command == 'init':
            value = init(control, args.task, args.execution_seconds, args.cost_usd, args.review_seconds)
        elif args.command == 'status':
            state, task = load(control)
            value = status(control, state, task)
        else:
            with locked(control):
                state, task = load(control)
                if args.command in ('reproduce', 'repair'):
                    return execute(control, state, task, args.command)
                if args.command == 'admit':
                    value = admit(control, state, task, args.run, args.reviewer, args.evidence)
                elif args.command == 'begin-review':
                    value = begin_review(control, state, task)
                elif args.command == 'review-check':
                    value = review_check(control, state, task)
                elif args.command == 'review':
                    value = finish_review(control, state, task, args.outcome, args.reviewer, args.evidence)
                elif args.command == 'reconcile':
                    value = reconcile(control, state, task)
                else:
                    import handoff
                    value = handoff.write(control, state, task)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('任务入口：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    def interrupted(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())
