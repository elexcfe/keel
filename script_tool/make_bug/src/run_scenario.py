#!/usr/bin/env python3
"""运行场景并保存行为证据；不建立失败基线，不代替人或 AI 评审。"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile

import evidence
import process_tree
import prepare_tools

LAUNCHER = '''#!/usr/bin/env bash
set -euo pipefail
task_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$task_dir/run_scenario.py" "$@"
'''


def now():
    return datetime.now(timezone.utc).isoformat()


def execute(task, script, env, log, timeout):
    # env.sh 每次由新 shell 加载；check.sh 只检查，run.sh 才执行测试。
    command = ['bash', '-c', 'set -euo pipefail; source "$MAKE_BUG_TASK/scenario/env.sh"; exec bash "$1"', 'scenario', str(script)]
    scope = process_tree.Scope()
    with log.open('w') as stream:
        process = subprocess.Popen(command, cwd=task, env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        evidence.save(log.with_suffix('.process.json'), scope.register(process))
        try:
            return process.wait(timeout=timeout)
        finally:
            evidence.save(log.with_suffix('.cleanup.json'), scope.stop(process))


def run_once(task, timeout=300):
    root = task / 'result/runs'
    root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=f'run-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-', dir=root))
    for folder in ('logs', 'images', 'videos', 'code'):
        (run / folder).mkdir()
    record = {'schema': 1, 'run_id': run.name, 'started_at': now(),
              'runtime': {'platform': platform.platform(), 'python': platform.python_version()},
              'execution': {'status': 'error', 'reason': '执行尚未完成', 'phase': 'capture'},
              'observations': [], 'checks': [], 'limitations': [],
              'integrity_warnings': [], 'review': {'status': 'pending'}}
    print(f'开始场景运行：{run}', flush=True)
    repo = task / 'repo'
    phase = 'capture'
    try:
        if (task / 'BUG.md').is_file():
            problem = evidence.local_file(task, 'BUG.md').read_bytes()
            (run / 'BUG.md').write_bytes(problem)
            record['problem_sha256'] = evidence.digest(problem)
        record['scenario'] = evidence.scenario(task, run / 'scenario.tar.gz')
        record['code'] = evidence.source_snapshot(repo, run / 'code/source.tar.gz')
        record['code']['head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
        record['code']['git_status'] = subprocess.check_output(['git', 'status', '--short'], cwd=repo, text=True)
        evidence.save(run / 'code/index.json', record['code'])
        with (run / 'code/working-tree.diff').open('w') as log:
            subprocess.run(['git', 'diff', 'HEAD', '--binary'], cwd=repo, stdout=log, check=True)
        env = {**os.environ, **prepare_tools.prepare(task), 'MAKE_BUG_TASK': str(task),
               'MAKE_BUG_RUN_DIR': str(run), 'MAKE_BUG_OBSERVATIONS': str(run / 'observations.json')}
        phase = 'check'
        rc = execute(task, task / 'scenario/check.sh', env, run / 'logs/check.log', timeout)
        if rc:
            record['execution'] = {'status': 'blocked', 'reason': '环境检查未通过，见 logs/check.log', 'phase': phase, 'exit_code': rc}
        else:
            phase = 'run'
            rc = execute(task, task / 'scenario/run.sh', env, run / 'logs/run.log', timeout)
            record['execution'] = {'status': 'completed' if rc == 0 else 'error',
                                   'reason': '场景执行完成；检查结果另行记录' if rc == 0 else '场景执行出错，见 logs/run.log',
                                   'phase': phase, 'exit_code': rc}
    except KeyboardInterrupt:
        record['execution'] = {'status': 'interrupted', 'reason': '用户中断', 'phase': phase}
    except subprocess.TimeoutExpired:
        record['execution'] = {'status': 'blocked' if phase == 'check' else 'error', 'reason': f'{phase} 阶段超时', 'phase': phase}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        record['execution'] = {'status': 'error', 'reason': str(exc), 'phase': phase}
    # 错误或中断仍保留部分观察和原始材料；不以缺失观察覆盖原始执行错误。
    observation_data = None
    try:
        observation_data = evidence.read_observations(run / 'observations.json')
        for key in ('observations', 'checks', 'limitations'):
            record[key] = observation_data[key]
    except (OSError, ValueError, TypeError) as exc:
        record['integrity_warnings'].append(f'观察记录不可用：{exc}')
        if record['execution']['status'] == 'completed':
            record['execution'] = {'status': 'error', 'reason': '场景未按约定交付有效 observations.json', 'phase': 'collect'}
    for key, current in (('code', lambda: evidence.source_snapshot(repo)),
                         ('scenario', lambda: evidence.scenario(task))):
        if key not in record:
            continue
        try:
            if current()['digest'] != record[key]['digest']:
                record['integrity_warnings'].append(f'运行期间 {key} 发生变化，本轮不可直接用于前后对照。')
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            record['integrity_warnings'].append(f'无法复核 {key}：{exc}')
    record['cleanup'] = {}
    for cleanup in (run / 'logs').glob('*.cleanup.json'):
        try:
            value = json.loads(cleanup.read_text())
            record['cleanup'][cleanup.name] = value
            if value['status'] != 'completed':
                record['integrity_warnings'].append('进程清理不完整：' + cleanup.name)
                if record['execution']['status'] == 'completed':
                    record['execution'] = {'status': 'error', 'reason': '进程清理不完整', 'phase': 'cleanup'}
        except (OSError, ValueError, KeyError) as exc:
            record['integrity_warnings'].append('无法读取清理记录：' + str(exc))
    record['evidence'] = evidence.collect_evidence(run, observation_data)
    record['finished_at'] = now()
    evidence.save(run / 'record.json', record)
    evidence.markdown(run, record)
    print(f'{record["execution"]["status"]}：{record["execution"]["reason"]}\n报告：{run / "REPORT.md"}', flush=True)
    return run, record


def main(task=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat', type=int, default=1, choices=range(1, 11), metavar='1..10')
    parser.add_argument('--timeout', type=float, default=300, help='环境检查及场景执行各自的超时秒数')
    args = parser.parse_args()
    if not 0 < args.timeout < float('inf'):
        parser.error('--timeout 必须为有限正数')
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    task = (task or Path(__file__).resolve().parent).resolve()
    with (task / '.scenario.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('同一任务已有场景运行，请等待完成。', file=sys.stderr)
            return 2
        for _ in range(args.repeat):
            _, record = run_once(task, args.timeout)
            status = record['execution']['status']
            if status != 'completed':
                return {'blocked': 2, 'error': 1, 'interrupted': 130}[status]
    return 0


if __name__ == '__main__':
    sys.exit(main())
