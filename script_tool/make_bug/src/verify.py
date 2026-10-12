#!/usr/bin/env python3
"""执行已固化的复现包；不调用模型，不根据普通非零退出推断目标 Bug。"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

import prepare_deps
import prepare_tools
from progress import run_progress

LAUNCHER = '''#!/usr/bin/env bash
set -euo pipefail
task_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$task_dir/verify.py" "$@"
'''


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def local_file(task, name):
    path = (task / name).resolve()
    if not path.is_relative_to(task) or not path.is_file():
        raise ValueError(f'缺少任务内文件：{name}')
    return path


def package_hash(task, manifest):
    if not isinstance(manifest['files'], list) or not manifest['files'] or any(not isinstance(name, str) for name in manifest['files']):
        raise ValueError('manifest.files 必须列出测试、测试配置及辅助脚本。')
    names = set(manifest['files']) | {'repro/manifest.json', 'repro/run.sh', 'repro/check.sh', 'repro/env.sh'}
    return {name: hashlib.sha256(local_file(task, name).read_bytes()).hexdigest() for name in sorted(names)}


def dependency_hash(task, env):
    repo = task / 'repo'
    selected = prepare_deps.detect(repo)
    if not selected:
        return 'not-node'
    manager, declared = selected
    version = prepare_deps.output([manager, '--version'], repo, env)
    if declared and version != declared.split('@', 1)[1].split('+', 1)[0]:
        raise ValueError('包管理器版本与项目声明不匹配。')
    config = json.loads((task / 'deps-config.json').read_text())
    configured = dict(env)
    configured['MAKE_BUG_DEPENDENCY_PROFILE'] = config.get('profile', 'full')
    return prepare_deps.reuse_fingerprint(repo, manager, version, configured, config.get('dependency_inputs'))


def code_hash(repo):
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=repo).decode().split('\0')
    digest = hashlib.sha256()
    for name in sorted(filter(None, names)):
        path = repo / name
        digest.update(name.encode() + b'\0')
        digest.update(os.readlink(path).encode() if path.is_symlink() else path.read_bytes() if path.is_file() else b'<missing>')
    return digest.hexdigest()


def execute(task, script, env, log, timeout):
    # env.sh 每次由新 shell 加载；check.sh 只检查，run.sh 才执行测试。
    command = ['bash', '-c', 'set -euo pipefail; source "$MAKE_BUG_TASK/repro/env.sh"; exec bash "$1"', 'verify', str(script)]
    with log.open('w') as stream:
        process = subprocess.Popen(command, cwd=task, env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        finally:
            # 只清理本次进程组，不按端口或进程名杀其他服务。
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def run_once(task, args):
    label = 'baseline' if args.baseline else 'verify'
    root = task / 'result/runs'
    root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=f'{label}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-', dir=root))
    (run / 'logs').mkdir()
    (run / 'images').mkdir()
    print(f'开始 {label}，结果目录：{run}', flush=True)
    summary = {'status': 'blocked', 'started_at': datetime.now(timezone.utc).isoformat(), 'mode': label}
    try:
        manifest = json.loads(local_file(task, 'repro/manifest.json').read_text())
        if not isinstance(manifest, dict):
            raise ValueError('manifest 必须是 JSON 对象。')
        coverage = manifest.get('coverage')
        if manifest.get('schema') != 1 or not isinstance(coverage, list) or not coverage or any(not isinstance(item, str) or not item.strip() for item in coverage):
            raise ValueError('manifest 需要 schema: 1 和非空 coverage。')
        hashes = package_hash(task, manifest)
        summary['test_files'] = hashes
        summary['coverage'] = manifest['coverage']
        repo = task / 'repo'
        summary['commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
        summary['git_status'] = subprocess.check_output(['git', 'status', '--short'], cwd=repo, text=True)
        with (run / 'logs/code.diff').open('w') as log:
            subprocess.run(['git', 'diff', 'HEAD', '--binary'], cwd=repo, stdout=log, check=True)
        # 同时标识未跟踪代码，避免只记录 HEAD 而遗漏实际测试内容。
        summary['code_hash'] = code_hash(repo)
        env = dict(os.environ)
        env.update(prepare_tools.prepare(task))
        env.update(MAKE_BUG_TASK=str(task), MAKE_BUG_RUN_DIR=str(run), MAKE_BUG_OUTCOME=str(run / 'outcome.json'))
        seal = task / 'repro/baseline.json'
        prior = json.loads(seal.read_text()) if seal.exists() else None
        if prior and prior['test_files'] != hashes:
            raise ValueError('复现测试或配置已变化；不能与原基线比较。请另建复现包并重新建立基线。')
        if not args.baseline and not prior:
            raise ValueError('尚无目标失败基线，请先运行 bash verify.sh --baseline。')
        current_dependencies = dependency_hash(task, env)
        environment_state = task / 'repro/environment-state.json'
        accepted_dependencies = json.loads(environment_state.read_text()).get('dependencies') if environment_state.exists() else prior.get('dependencies') if prior else None
        if prior and accepted_dependencies != current_dependencies and not args.prepare:
            raise ValueError('依赖输入或运行环境已变化；检查后使用 --prepare 准备环境。')
        if args.prepare:
            with (run / 'logs/prepare.log').open('w') as log:
                prepared = run_progress([sys.executable, '-u', str(task / 'prepare_deps.py'), '--retry'], cwd=task, env=env, log=log, label='准备验证依赖')
                if prepared.returncode:
                    raise ValueError(f'依赖准备失败（{prepared.returncode}），见 logs/prepare.log。')
        rc = execute(task, task / 'repro/check.sh', env, run / 'logs/check.log', args.timeout)
        if rc:
            raise ValueError(f'环境检查失败（{rc}），见 logs/check.log；未执行目标测试。')
        if args.prepare:
            current_dependencies = dependency_hash(task, env)
        rc = execute(task, task / 'repro/run.sh', env, run / 'logs/test.log', args.timeout)
        result = json.loads((run / 'outcome.json').read_text())
        if not isinstance(result, dict):
            raise ValueError('outcome 必须是 JSON 对象。')
        status = result.get('status')
        expected = {'passed': 0, 'target_failed': 1, 'blocked': 2}
        if status not in expected or rc != expected[status] or not isinstance(result.get('reason'), str) or not result['reason'].strip():
            raise ValueError('测试结果缺失、无判定依据或与退出码不一致。')
        if code_hash(repo) != summary['code_hash']:
            raise ValueError('执行期间源码发生变化，本次结果不可比较。')
        if package_hash(task, manifest) != hashes:
            raise ValueError('执行期间测试文件发生变化，本次结果不可比较。')
        if status in ('passed', 'target_failed'):
            save(environment_state, {'dependencies': current_dependencies})
        summary.update(status=status, reason=result['reason'], exit_code=rc, dependencies=current_dependencies)
        if args.baseline:
            if status == 'target_failed':
                if not prior:
                    save(seal, {'test_files': hashes, 'dependencies': current_dependencies, 'run': str(run.relative_to(task))})
            elif status == 'passed':
                summary.update(status='blocked', reason='基线没有触发目标断言，不能建立 Bug 复现基线。')
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError, prepare_deps.DependencyError) as exc:
        summary.update(status='blocked', reason=str(exc))
    except KeyboardInterrupt:
        summary.update(status='blocked', reason='用户中断执行。')
    summary['finished_at'] = datetime.now(timezone.utc).isoformat()
    save(run / 'summary.json', summary)
    print(f"{summary['status']}: {summary.get('reason', '')}\n结果：{run}")
    return {'passed': 0, 'target_failed': 1, 'blocked': 2}[summary['status']]


def main(task=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', action='store_true', help='在问题代码上建立目标失败基线')
    parser.add_argument('--prepare', action='store_true', help='明确允许运行依赖安装；npm ci 会重建 node_modules')
    parser.add_argument('--repeat', type=int, default=1, choices=range(1, 11), metavar='1..10')
    parser.add_argument('--timeout', type=int, default=300, help='每个检查/测试步骤的最长秒数')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout 必须大于 0')
    task = (task or Path(__file__).resolve().parent).resolve()
    with (task / '.verify.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('同一任务已有验证运行，请等待完成。', file=sys.stderr)
            return 2
        results = []
        for _ in range(args.repeat):
            results.append(run_once(task, args))
            args.prepare = False  # 重复验证只准备一次环境
            if results[-1] == 2:
                break  # 环境/脚本阻塞时不机械重复

    return max(results)


if __name__ == '__main__':
    sys.exit(main())
