#!/usr/bin/env python3
"""预装指定版本的共享 Playwright + Chromium，不更改任务项目依赖。"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess

from prepare_tools import shared_environment
import dependency_inventory as inventory
from dependency_inventory import tool_entries


def setup(shared, version):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('请指定精确 Playwright 版本（例如 1.55.0），不接受 latest 或版本范围。')
    shared = shared.expanduser().resolve()
    env = {**os.environ, **shared_environment(shared)}
    env['npm_config_cache'] = str(shared / 'downloads/npm')
    node_version = subprocess.check_output(['node', '--version'], text=True).strip()
    identity = f'{version}-{platform.system().lower()}-{platform.machine().lower()}-{node_version}'
    destination = shared / 'node/playwright' / identity
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (destination.parent / f'{identity}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        destination.mkdir(exist_ok=True)
        marker = destination / 'installed.json'
        cli = destination / 'node_modules/playwright/cli.js'
        reused = marker.exists() and cli.exists()
        previous = json.loads(marker.read_text()) if reused else {}
        method = shlex.join(['npm', 'install', '--prefix', str(destination), '--save-exact',
                             '--ignore-scripts', '--no-audit', '--no-fund', f'playwright@{version}'])
        before = tool_entries(destination, env, 'unknown', 'existing; original installation unknown')
        old = {(e['name'], e['path']): e for e in [*before, *previous.get('dependencies', [])]}
        try:
            if not reused:
                (destination / 'package.json').write_text(json.dumps({'private': True, 'name': 'make-bug-browser-tools'}))
                subprocess.run(['npm', 'install', '--prefix', str(destination), '--save-exact',
                                '--ignore-scripts', '--no-audit', '--no-fund', f'playwright@{version}'], env=env, check=True)
            # 官方安装器对已命中的浏览器 revision 不重复下载。
            subprocess.run(['node', str(cli), 'install', 'chromium'], env=env, check=True)
        except BaseException:
            entries = [{**e, 'status': 'unverified'} for e in tool_entries(destination, env, inventory.actor('setup_tools.py'), method)]
            for entry in entries:
                known = old.get((entry['name'], entry['path']))
                if reused and known and known['version'] == entry['version']:
                    entry.update(known)
                    entry['status'] = 'unverified'
            publish(shared, entries)
            if marker.exists():
                marker.write_text(json.dumps({**previous, 'dependencies': entries, 'status': 'failed'}, indent=2) + '\n')
            raise
        entries = tool_entries(destination, env, inventory.actor('setup_tools.py'), method)
        for entry in entries:
            known = old.get((entry['name'], entry['path']))
            is_browser = entry['name'] in ('chromium', 'chromium-headless-shell', 'ffmpeg')
            if (reused or is_browser) and known and known['version'] == entry['version']:
                entry.update(known)
                entry['status'] = 'installed'
        publish(shared, entries)
        marker.write_text(json.dumps({'dependencies': entries, 'playwright': version, 'node': node_version,
                                     'browser_cache': env['PLAYWRIGHT_BROWSERS_PATH'],
                                     'status': 'installed-not-runtime-validated'}, indent=2) + '\n')
    print(f'共享 Playwright：{destination}\n浏览器缓存：{env["PLAYWRIGHT_BROWSERS_PATH"]}')
    return destination


def publish(shared, entries):
    inventory.update(shared, entries)
    task = Path(__file__).resolve().parent
    if (task / 'tools-config.json').exists():
        inventory.update(task, entries)


def default_shared():
    # 任务副本优先使用自身配置，直接运行源码时保留工具根目录默认值。
    config = Path(__file__).resolve().with_name('tools-config.json')
    if config.exists():
        return Path(json.loads(config.read_text())['shared'])
    return Path(__file__).resolve().parents[1] / 'tools/shared'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shared-tools-dir', type=Path, default=None)
    parser.add_argument('--playwright-version', required=True, help='精确版本号，按目标项目需求选择')
    args = parser.parse_args()
    try:
        setup(args.shared_tools_dir if args.shared_tools_dir is not None else default_shared(), args.playwright_version)
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f'共享工具准备失败：{exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
