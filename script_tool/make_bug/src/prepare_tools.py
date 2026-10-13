#!/usr/bin/env python3
"""提供共享工具和任务工具目录；不在启动时隐式安装工具。"""
import json
import os
from pathlib import Path
import platform
import shlex
import sys

import dependency_inventory as dependencies


def shared_environment(shared):
    shared = shared.expanduser().resolve()
    system = f'{platform.system().lower()}-{platform.machine().lower()}'
    env = {
        'MAKE_BUG_SHARED_TOOLS': str(shared),
        'PLAYWRIGHT_BROWSERS_PATH': str(shared / 'browsers' / system / 'playwright'),
        'PLAYWRIGHT_SKIP_BROWSER_GC': '1',
        'PUPPETEER_CACHE_DIR': str(shared / 'browsers' / system / 'puppeteer'),
        'npm_config_cache': str(shared / 'downloads/npm'),
        'PIP_CACHE_DIR': str(shared / 'downloads/pip'),
    }
    for path in (shared / 'bin', shared / 'node', shared / 'downloads',
                 Path(env['PLAYWRIGHT_BROWSERS_PATH']), Path(env['PUPPETEER_CACHE_DIR']),
                 Path(env['npm_config_cache']), Path(env['PIP_CACHE_DIR'])):
        path.mkdir(parents=True, exist_ok=True)
    return env


def prepare(task):
    config = json.loads((task / 'tools-config.json').read_text())
    env = shared_environment(Path(config['shared']))
    local = task.resolve() / 'tools'
    for name in ('bin', 'node', 'python', 'tmp'):
        (local / name).mkdir(parents=True, exist_ok=True)
    env['MAKE_BUG_TASK_TOOLS'] = str(local)
    # 不通过 NODE_PATH 注入共享模块，避免替换目标项目锁定的依赖。
    env['PATH'] = os.pathsep.join([os.environ.get('PATH', ''), str(Path(env['MAKE_BUG_SHARED_TOOLS']) / 'bin'), str(local / 'bin')])
    inventory = {'shared': env['MAKE_BUG_SHARED_TOOLS'], 'task_tools': str(local),
                 'environment': {k: v for k, v in env.items() if k != 'PATH'},
                 'prepared_packages': [str(p.parent) for p in sorted(Path(config['shared']).expanduser().resolve().glob('node/*/*/installed.json'))],
                 'note': '目录存在不代表工具已安装；installed.json 只说明安装步骤完成，运行前仍需检查版本和启动情况。'}
    dependencies.scan(task)
    for marker in sorted(Path(env['MAKE_BUG_SHARED_TOOLS']).glob('node/*/*/installed.json')):
        details = json.loads(marker.read_text())
        entries = dependencies.tool_entries(marker.parent, env, 'unknown', 'existing; original installation unknown')
        known = {(e['name'], e['path']): e for e in details.get('dependencies', [])}
        for entry in entries:
            entry['status'] = 'detected'
            previous = known.get((entry['name'], entry['path']))
            if previous and previous['version'] == entry['version']:
                entry.update(previous)
        dependencies.update(task, entries)
    logs = task / 'result/logs'
    logs.mkdir(parents=True, exist_ok=True)
    (logs / 'tools.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + '\n')
    return env


def main():
    try:
        env = prepare(Path(__file__).resolve().parent)
        # stdout 只包含经 shell 引用的赋值，供启动器读取。
        for key, value in env.items():
            print(f'export {key}={shlex.quote(value)}')
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f'工具目录准备失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
