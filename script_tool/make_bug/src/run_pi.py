#!/usr/bin/env python3
"""统一 Pi 运行入口：single/split、可选目录隔离、持续会话监测。"""
import argparse
from datetime import datetime, timezone
import json
import fcntl
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import time
import uuid

import delivery
import process_tree
import repair
from evidence import source_snapshot, file_digest
from run_isolation import isolate
from session_stats import refresh, save

INITIAL = '先读取 dependencies.json（运行 python3 dependency_inventory.py show，优先查看验证依赖和服务），再检查 result/logs/dependency-preparation.json；按 ENVIRONMENT.md 处理环境状态，再执行 TASK.md 中的复现任务。'
BLOCKED_ARGS = {'--no-session', '--session', '--session-dir', '--session-id', '--continue', '-c', '--resume', '-r', '--fork', '--api-key', '--mode', '--system-prompt', '--append-system-prompt', '--extension', '-e'}


def execute(command, *, task, env, run, label, deadline, cost_limit=None, interactive=False):
    started = time.monotonic()
    result = {'name': label, 'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'running', 'command': [str(x) for x in command]}
    save(run / 'current.json', result)
    output = run / 'events' / (label + '.stdout.log')
    error = run / 'events' / (label + '.stderr.log')
    if started >= deadline:
        result.update(status='timeout', exit_code=124, elapsed_seconds=0)
        output.touch()
        error.touch()
        save(run / 'events' / (label + '.json'), result)
        save(run / 'current.json', result)
        return result
    process = None
    scope = process_tree.Scope()
    purpose = env.get('MAKE_BUG_PURPOSE', 'reproduce')
    initial_delivery = delivery.inventory(task, purpose)
    try:
        with output.open('wb') as out, error.open('wb') as err, selectors.DefaultSelector() as selector:
            process = subprocess.Popen(command, cwd=task, env=env, start_new_session=True,
                                       stdout=None if interactive else subprocess.PIPE,
                                       stderr=None if interactive else subprocess.PIPE)
            result['process'] = scope.register(process)
            save(run / 'current.json', result)
            if not interactive:
                selector.register(process.stdout, selectors.EVENT_READ, (out, sys.stdout))
                selector.register(process.stderr, selectors.EVENT_READ, (err, sys.stderr))
            next_metric = 0
            while process.poll() is None or selector.get_map():
                now = time.monotonic()
                if now >= deadline:
                    result['status'] = 'timeout'
                    scope.stop(process)
                    break
                if now >= next_metric:
                    scope.owned()
                    delivery.checkpoint(task, run, initial_delivery, result['status'], purpose)
                    metric = refresh(run)
                    save(run / 'live-metrics.json', metric)
                    save(run / 'current.json', {**result, 'elapsed_seconds': now - started})
                    if cost_limit is not None and metric['known_totals']['cost'] >= cost_limit:
                        result['status'] = 'cost_limit'
                        scope.stop(process)
                        break
                    next_metric = now + 2
                for key, _ in selector.select(timeout=.1):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    log, terminal = key.data
                    log.write(data)
                    log.flush()
                    if hasattr(terminal, 'buffer'):
                        terminal.buffer.write(data)
                        terminal.buffer.flush()
                    else:
                        terminal.write(data.decode(errors='replace'))
                        terminal.flush()
                if process.poll() is not None:
                    scope.stop(process)
            code = process.wait()
            result.setdefault('exit_code', 124 if result['status'] == 'timeout' else 125 if result['status'] == 'cost_limit' else code)
            if result['status'] == 'running':
                result['status'] = 'completed' if code == 0 else 'failed'
    except BaseException as exc:
        interrupted = isinstance(exc, (KeyboardInterrupt, InterruptedError))
        result['status'] = 'interrupted' if interrupted else 'error'
        result['exit_code'] = 130 if interrupted else 1
        if process:
            scope.stop(process)
        raise
    finally:
        if process:
            result['cleanup'] = scope.stop(process)
            if result['cleanup']['status'] != 'completed' and result['status'] == 'completed':
                result.update(status='error', exit_code=1, error='进程清理不完整')
            for stream in (process.stdout, process.stderr):
                if stream:
                    stream.close()
        result['elapsed_seconds'] = time.monotonic() - started
        result['ended_at'] = datetime.now(timezone.utc).isoformat()
        save(run / 'events' / (label + '.json'), result)
        save(run / 'current.json', result)
        delivery.checkpoint(task, run, initial_delivery, result['status'], purpose)
    return result


def snapshot(task, run, label):
    folder = run / 'snapshots' / label
    folder.mkdir(parents=True)
    try:
        save(folder / 'source.json', source_snapshot(task / 'repo', folder / 'source.tar.gz'))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        save(folder / 'error.json', {'error': str(exc)})
    for name in ('dependencies.json', 'deps-state.json', 'deps-config.json', 'tools-config.json'):
        if (task / name).is_file():
            shutil.copy2(task / name, folder / name)


def handoff(task, run):
    folder = run / 'handoff'
    folder.mkdir()
    errors = []
    path = task / 'environment/handoff.json'
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(task):
            raise ValueError('交接文件不在任务内')
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError('交接内容必须是 JSON 对象')
        for key in ('status', 'cwd', 'start_commands', 'stop_commands', 'environment_variables', 'ports', 'data_setup', 'data_cleanup', 'checks', 'limitations'):
            if key not in data:
                errors.append('缺少 ' + key)
        for key in ('start_commands', 'stop_commands', 'environment_variables', 'ports', 'data_setup', 'data_cleanup', 'checks', 'limitations'):
            if key in data and not isinstance(data[key], list):
                errors.append(key + ' 必须是列表')
        if not isinstance(data.get('cwd'), str):
            errors.append('cwd 必须是文字')
        if data.get('status') not in ('ready', 'blocked'):
            errors.append('status 无效')
        shutil.copy2(path, folder / 'handoff.json')
        check = task / 'environment/check.sh'
        if check.is_symlink() or not check.is_file() or not check.resolve().is_relative_to(task):
            errors.append('缺少任务内 environment/check.sh')
        else:
            shutil.copy2(check, folder / 'check.sh')
    except (OSError, ValueError, TypeError) as exc:
        errors.append(str(exc))
    result = {'status': 'invalid' if errors else 'recorded', 'errors': errors,
              'note': '只校验交接结构；未把 ready 声明当环境检查成功。'}
    save(folder / 'validation.json', result)
    return result


def run(args, pi_args):
    if any(a.split('=', 1)[0] in BLOCKED_ARGS for a in pi_args):
        raise ValueError('监测入口管理会话/提示和输出模式，不接受覆盖参数；凭据请通过 Pi 配置提供。')
    purpose = getattr(args, 'purpose', 'reproduce')
    reference = getattr(args, 'reference', None)
    if purpose == 'repair' and (args.workflow != 'single' or args.isolate or not reference):
        raise ValueError('修复需要 --reference、single 和既有已验收任务；不再次隔离或拆分会话。')
    if purpose != 'repair' and reference:
        raise ValueError('--reference 只用于 --purpose repair。')
    source = args.task.resolve()
    task = source
    if args.cache_seed and not args.isolate:
        raise ValueError('--cache-seed 只能配合 --isolate')
    root = args.runs_dir.resolve() if args.runs_dir else (args.isolate.resolve().parent if args.isolate else task.parent) / 'pi-runs'
    if root.is_relative_to(task) or (args.isolate and root.is_relative_to(args.isolate.resolve())):
        raise ValueError('监测目录必须放在任务和隔离副本之外。')
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:10])
    run_dir.mkdir(mode=0o700)
    for folder in ('sessions', 'prompts', 'events', 'snapshots'):
        (run_dir / folder).mkdir()
    started = time.monotonic()
    deadline = started + args.timeout
    initial_delivery = delivery.inventory(task, purpose)
    baseline = None
    initial_runs = set()
    reference_digest = None
    state = {'schema': 1, 'purpose': purpose, 'workflow': args.workflow, 'pair_id': args.pair_id, 'task': str(task),
             'source_task': str(source), 'isolation': 'directory-copy' if args.isolate else 'existing-task',
             'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'running', 'phases': [],
             'timeout_seconds': args.timeout, 'cost_limit': args.cost_limit,
             'review': 'pending', 'warnings': [], 'pi_args': pi_args}
    save(run_dir / 'run.json', state)
    save(run_dir / 'review.json', {'outcome': 'pending', 'reviewer': None, 'evidence': []})
    print(f'Pi 运行记录：{run_dir}', file=sys.stderr, flush=True)
    env = dict(os.environ)
    env['MAKE_BUG_PROCESS_GROUP'] = 'inherit'
    env['MAKE_BUG_PURPOSE'] = purpose
    env.pop('MAKE_BUG_REPAIR_REFERENCE', None)
    code = 1
    task_lock = None
    try:
        if args.isolate:
            task = isolate(source, args.isolate, cache_seed=args.cache_seed)
            state['task'] = str(task)
            state['isolation_elapsed_seconds'] = time.monotonic() - started
        task_lock = (task / '.pi-run.lock').open('a')
        try:
            fcntl.flock(task_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('该任务已有 Pi 在运行，请使用另一个隔离副本。') from exc
        if purpose == 'repair':
            baseline = repair.load_reference(task, reference.resolve())
            errors = repair.current_matches(task, baseline, source=True)
            if errors:
                raise ValueError('\n'.join(errors))
            frozen_reference = run_dir / 'repair-reference.json'
            shutil.copy2(reference, frozen_reference)
            reference_digest = file_digest(frozen_reference)
            env['MAKE_BUG_REPAIR_REFERENCE'] = str(frozen_reference)
            initial_runs = {p.parent.name for p in (task / 'result/runs').glob('*/record.json')}
            state['baseline_run'] = baseline['run_id']
        if args.isolate:
            private = task.parent / 'runtime'
            for name in ('tmp', 'xdg-cache', 'pnpm', 'yarn', 'npm', 'pip'):
                (private / name).mkdir(parents=True, exist_ok=True)
            env.update(TMPDIR=str(private / 'tmp'), XDG_CACHE_HOME=str(private / 'xdg-cache'),
                       npm_config_store_dir=str(private / 'pnpm'), YARN_CACHE_FOLDER=str(private / 'yarn'),
                       npm_config_cache=str(private / 'npm'), PIP_CACHE_DIR=str(private / 'pip'),
                       YARN_ENABLE_GLOBAL_CACHE='false', XDG_DATA_HOME=str(private / 'xdg-data'),
                       XDG_STATE_HOME=str(private / 'xdg-state'))
        # 建议端口不是预留端口；场景启动时必须确认并记录实际端口。
        env['MAKE_BUG_PORT'] = str(10000 + uuid.uuid4().int % 50000)
        pi = shutil.which(os.environ.get('PI_BIN', 'pi'))
        if not pi:
            raise ValueError('找不到 Pi，请设置 PI_BIN 或安装 Pi。')
        state['pi_binary'] = pi
        try:
            version = subprocess.run([pi, '--version'], cwd=task, capture_output=True, text=True, timeout=5)
            state['pi_version'] = version.stdout.strip() if version.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            state['pi_version'] = None
        if not state['pi_version']:
            state['warnings'].append('Pi 版本未能确认。')
        shutil.copy2(task / 'command_timeout.mjs', run_dir / 'command_timeout.mjs')
        state['command_timeout'] = {'default_seconds': 600, 'max_seconds': 600,
                                    'extension_sha256': file_digest(run_dir / 'command_timeout.mjs')}
        snapshot(task, run_dir, 'initial')
        pre = execute([sys.executable, str(task / 'prepare_tools.py')], task=task, env=env, run=run_dir,
                      label='prepare-tools', deadline=deadline)
        state['phases'].append(pre)
        if pre['exit_code']:
            code = pre['exit_code']
            state['status'] = pre['status']
            return code
        # 只读 JSON 环境，避免 eval 输出；prepare_tools 仍可独立供旧人工命令使用。
        tools = json.loads((task / 'result/logs/tools.json').read_text())
        env.update(tools['environment'])
        env['PATH'] = os.pathsep.join([env.get('PATH', ''), str(Path(tools['shared']) / 'bin'), str(Path(tools['task_tools']) / 'bin')])
        deps = {'status': 'skipped', 'exit_code': 0}
        if os.environ.get('MAKE_BUG_SKIP_DEPS') != '1':
            prepared = execute([sys.executable, '-u', str(task / 'prepare_deps.py')], task=task, env=env, run=run_dir,
                               label='prepare-deps', deadline=deadline)
            state['phases'].append(prepared)
            deps = {'status': 'ready' if prepared['exit_code'] == 0 else 'failed', 'exit_code': prepared['exit_code']}
            (task / 'result/logs/dependency-preparation.log').write_bytes((run_dir / 'events/prepare-deps.stdout.log').read_bytes() + (run_dir / 'events/prepare-deps.stderr.log').read_bytes())
            if prepared['status'] in ('timeout', 'cost_limit'):
                state['status'], code = prepared['status'], prepared['exit_code']
                return code
        save(task / 'result/logs/dependency-preparation.json', deps)
        env['MAKE_BUG_INSTALLER'] = 'pi'
        save(run_dir / 'environment.json', {k: env[k] for k in ('MAKE_BUG_PORT', 'MAKE_BUG_INSTALLER', 'MAKE_BUG_SHARED_TOOLS', 'MAKE_BUG_TASK_TOOLS', 'PLAYWRIGHT_BROWSERS_PATH', 'TMPDIR', 'XDG_CACHE_HOME') if k in env})
        if baseline is not None:
            errors = repair.current_matches(task, baseline, source=True)
            if errors:
                raise ValueError('\n'.join(errors))
        stages = ('repair',) if purpose == 'repair' else (('single',) if args.workflow == 'single' else ('prepare', 'reproduce'))
        for stage in stages:
            if args.cost_limit is not None and refresh(run_dir)['known_totals']['cost'] >= args.cost_limit:
                state['status'], code = 'cost_limit', 125
                break
            if time.monotonic() >= deadline:
                state['status'], code = 'timeout', 124
                break
            sessions = run_dir / 'sessions' / stage
            sessions.mkdir()
            prompts = ['PREPARE.md', 'ENVIRONMENT.md', 'MONITOR.md'] if stage == 'prepare' else ['TASK.md', 'SCENARIO.md', 'ENVIRONMENT.md', 'MONITOR.md']
            if stage == 'repair':
                prompts = ['REPAIR.md', 'ENVIRONMENT.md', 'MONITOR.md']
            prompt_dir = run_dir / 'prompts' / stage
            prompt_dir.mkdir()
            command = [pi, '--no-extensions', '--no-skills', '--no-prompt-templates', '--no-context-files', '--no-mcp',
                       '--extension', str(run_dir / 'command_timeout.mjs'),
                       '--tools', 'read,bash,edit,write,grep,find,ls', '--session-dir', str(sessions)]
            for name in prompts:
                shutil.copy2(task / name, prompt_dir / name)
                command += ['--append-system-prompt', str(task / name)]
            shutil.copy2(task / 'BUG.md', prompt_dir / 'BUG.md')
            initial = '按 PREPARE.md 只准备环境并交接，不复现目标 Bug。' if stage == 'prepare' else INITIAL
            if stage == 'repair':
                initial = '按 REPAIR.md 完成修复；在本会话内调用 bash run_scenario.sh 检查、纠错并更新 FIX.md。'
            if stage == 'reproduce':
                initial += ' 这是新会话，先读取 environment/handoff.json；缺失或无效时自行补齐并记录环境返工。'
            (prompt_dir / 'initial.txt').write_text(initial)
            save(prompt_dir / 'sha256.json', {p.name: file_digest(p) for p in prompt_dir.iterdir() if p.is_file()})
            interactive = args.workflow == 'single' and not any(a in ('--print', '-p') for a in pi_args) and sys.stdin.isatty()
            if not interactive:
                command += ['--mode', 'json', '--print']
            command += pi_args + ['@' + str(task / 'BUG.md'), initial]
            stage_deadline = min(deadline, time.monotonic() + args.prepare_timeout) if stage == 'prepare' else deadline
            remaining = max(0, stage_deadline - time.monotonic())
            reserve = min(args.wrap_up, remaining / 6)
            env['MAKE_BUG_DEADLINE_MS'] = str((time.time() + remaining) * 1000)
            env['MAKE_BUG_WRAP_UP_MS'] = str(reserve * 1000)
            state['wrap_up_seconds'] = reserve
            phase = execute(command, task=task, env=env, run=run_dir, label=stage,
                            deadline=stage_deadline,
                            cost_limit=args.cost_limit, interactive=interactive)
            state['phases'].append(phase)
            state['elapsed_seconds'] = time.monotonic() - started
            save(run_dir / 'run.json', state)
            cost_reached = args.cost_limit is not None and refresh(run_dir)['known_totals']['cost'] >= args.cost_limit
            if stage == 'prepare':
                state['handoff'] = handoff(task, run_dir)
                snapshot(task, run_dir, 'handoff')
                if phase['status'] == 'cost_limit' or cost_reached:
                    state['status'], code = 'cost_limit', 125
                    break
            else:
                state['status'], code = ('cost_limit', 125) if cost_reached else (phase['status'], phase['exit_code'])
        return code
    except (KeyboardInterrupt, InterruptedError):
        state['status'], code = 'interrupted', 130
        return code
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        state['status'], state['error'] = 'error', str(exc)
        print(f'运行失败：{exc}', file=sys.stderr)
        return 1
    finally:
        state['elapsed_seconds'] = time.monotonic() - started
        state['exit_code'] = code
        state['ended_at'] = datetime.now(timezone.utc).isoformat()
        save(run_dir / 'run.json', state)
        state['delivery'] = delivery.checkpoint(task, run_dir, initial_delivery, state['status'], purpose)
        if purpose == 'repair':
            try:
                if baseline is None or reference_digest != file_digest(run_dir / 'repair-reference.json'):
                    raise ValueError('修复参考未能加载或发生变化。')
                repair.load_reference(task, run_dir / 'repair-reference.json')
                closure = repair.finalize(task, baseline, initial_runs,
                                          initial_delivery.get('result/FIX.md', {}).get('sha256'))
                if state['status'] != 'completed' or state['delivery']['delivery'] != 'files_present':
                    closure['status'] = 'incomplete'
                    closure['errors'].append('Pi 未正常完成或交付文件检查未通过。')
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                closure = {'status': 'incomplete', 'errors': [str(exc)], 'review': 'pending'}
            save(run_dir / 'repair-result.json', closure)
            state['repair'] = closure
        snapshot(task, run_dir, 'final')
        state['elapsed_seconds'] = time.monotonic() - started
        state['ended_at'] = datetime.now(timezone.utc).isoformat()
        save(run_dir / 'run.json', state)
        metrics = refresh(run_dir)
        expected = ('repair',) if purpose == 'repair' else (('single',) if args.workflow == 'single' else ('prepare', 'reproduce'))
        state['monitoring'] = 'complete' if all(metrics['phases'].get(p, {}).get('totals', {}).get(key) is not None for p in expected for key in metrics['totals']) and not metrics['warnings'] else 'incomplete'
        save(run_dir / 'run.json', state)
        (run_dir / 'REPORT.md').write_text(f"# Pi 运行记录\n\n目的：{purpose}；方式：{args.workflow}；执行：{state['status']}；监测：{state['monitoring']}。\n\n耗时：{state['elapsed_seconds']:.2f}s；估算费用：{metrics['totals']['cost']}（null/None 表示未知）。\n\nBug 验收待完成，进程成功不代表复现或修复通过。修复核查见 repair-result.json（修复模式）。查看 DELIVERY.md、delivery.json、run.json、metrics.json 和 sessions/ 原始记录。\n")
        if task_lock:
            task_lock.close()
        print(f'运行结束，记录：{run_dir}', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--workflow', choices=('single', 'split'), default='single')
    parser.add_argument('--purpose', choices=('reproduce', 'repair'), default='reproduce')
    parser.add_argument('--reference', type=Path, help='外层已验收复现的准入记录；repair 必需')
    parser.add_argument('--runs-dir', type=Path)
    parser.add_argument('--isolate', type=Path, help='新建隔离副本，要求模板仓库干净')
    parser.add_argument('--cache-seed', type=Path, help='复制下载缓存种子，不复制已安装依赖')
    parser.add_argument('--timeout', type=float, default=3600)
    parser.add_argument('--wrap-up', type=float, default=600, help='收尾预留秒数，上限为阶段剩余时间的六分之一；收尾禁用 bash，使用 read/edit/write 交付')
    parser.add_argument('--prepare-timeout', type=float, default=900)
    parser.add_argument('--cost-limit', type=float, help='已记录估算费用软上限；可能有延迟/缺失')
    parser.add_argument('--pair-id', help='实验配对标识，不改变执行行为')
    args, rest = parser.parse_known_args()
    if rest[:1] == ['--']:
        rest = rest[1:]
    for value in (args.timeout, args.prepare_timeout, args.cost_limit, args.wrap_up):
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error('预算必须为有限正数')
    def interrupted(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise KeyboardInterrupt(f'signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        process_tree.enable()
        return run(args, rest)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f'无法启动：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
