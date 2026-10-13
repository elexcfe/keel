#!/usr/bin/env python3
"""任务依赖清单：盘点实际安装内容，或登记 Pi 补装的工具。"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import shlex
import tempfile


CATEGORIES = ('project', 'services', 'verification', 'unclassified')
VERIFICATION_PACKAGES = {'playwright', 'playwright-core', '@playwright/test',
                         'puppeteer', 'puppeteer-core', 'cypress', 'vitest', 'jest',
                         'chromium', 'chromium-headless-shell'}


def category(entry):
    if entry.get('category') in CATEGORIES:
        return entry['category']
    if entry['name'] in VERIFICATION_PACKAGES:
        return 'verification'
    if entry.get('group') == 'project' or entry['name'] in ('node', 'npm', 'yarn', 'pnpm'):
        return 'project'
    return 'unclassified'


def overview(data, selected=None):
    """默认隐藏项目包明细，按用途呈现；不修改清单或执行安装。"""
    groups = {name: [] for name in CATEGORIES}
    for entry in data['dependencies']:
        groups[category(entry)].append(entry)
    result = {'counts': {name: len(items) for name, items in groups.items()},
              'project_preparation': data.get('preparations', {}).get('project', {}),
              'runtimes': [e for e in groups['project'] if e['name'] in ('node', 'npm', 'yarn', 'pnpm')],
              'note': '未登记不代表不需要；安装存在不代表服务或工具可运行。'}
    visible = CATEGORIES if selected == 'all' else (selected,) if selected else ('verification', 'services', 'unclassified')
    result['groups'] = {name: groups[name] for name in visible}
    return result


def actor(script):
    return os.environ.get('MAKE_BUG_INSTALLER', script)


def update(task, entries=(), *, group=None, state=None, preserve=False, classification=None):
    """同名不同路径分别记录；完整盘点只替换自己的分组，不清空其他工具。"""
    task = Path(task).resolve()
    task.mkdir(parents=True, exist_ok=True)
    target = task / 'dependencies.json'
    with (task / '.dependencies.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(target.read_text()) if target.exists() else {'schema': 1, 'dependencies': [], 'preparations': {}}
        if data.get('schema') != 1 or not isinstance(data.get('dependencies'), list):
            raise ValueError('dependencies.json 格式无效，保留原文件，请先修正。')
        old = {(e['name'], e['path']): e for e in data['dependencies']}
        if classification:
            name, path, selected = classification
            if (name, path) not in old:
                raise ValueError('清单中没有该名称和路径，请先登记。')
            old[name, path].update(category=selected, category_source='explicit')
        merged = {k: v for k, v in old.items() if group is None or v.get('group') != group}
        now = datetime.now(timezone.utc).isoformat()
        for entry in entries:
            entry = dict(entry)
            for field in ('name', 'version', 'install_method', 'installer', 'path', 'status'):
                if field not in entry:
                    raise ValueError(f'依赖缺少字段：{field}')
            key = entry['name'], entry['path']
            previous = old.get(key)
            if preserve and previous and previous['version'] == entry['version']:
                entry = {**entry, **previous}
            if previous and previous.get('group') and not entry.get('group'):
                entry['group'] = previous['group']
            if previous and previous.get('category_source') == 'explicit' and entry.get('category_source') != 'explicit':
                entry['category'] = previous['category']
                entry['category_source'] = 'explicit'
            if group:
                entry['group'] = group
            entry.setdefault('recorded_at', now)
            merged[key] = entry
        for entry in merged.values():
            entry['category'] = category(entry)
            entry.setdefault('category_source', 'inferred')
        data['dependencies'] = sorted(merged.values(), key=lambda e: (e['name'], e['path']))
        if state is not None:
            data.setdefault('preparations', {})[group or 'manual'] = {**state, 'recorded_at': now}
        data['summary'] = overview(data)['counts']
        data['updated_at'] = now
        with tempfile.NamedTemporaryFile(mode='w', dir=task, prefix='.dependencies-', delete=False) as temp:
            try:
                json.dump(data, temp, ensure_ascii=False, indent=2)
                temp.write('\n')
                temp.close()
                os.replace(temp.name, target)
            finally:
                Path(temp.name).unlink(missing_ok=True)
    return data


def node_packages(repo):
    """读取安装目录中的 package.json，支持 workspace、嵌套包及 pnpm 链接。"""
    packages, warnings, seen = [], [], set()
    roots = []
    for directory, dirs, _ in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in ('.git', '.yarn')]
        if 'node_modules' in dirs:
            roots.append(Path(directory) / 'node_modules')
            dirs.remove('node_modules')
    while roots:
        root = roots.pop()
        real = root.resolve()
        if real in seen or not root.is_dir():
            continue
        seen.add(real)
        for child in root.iterdir():
            if child.name == '.pnpm':
                roots.extend(child.glob('*/node_modules'))
                continue
            if child.name.startswith('.') or not child.is_dir():
                continue
            candidates = child.iterdir() if child.name.startswith('@') else [child]
            for package in candidates:
                manifest = package / 'package.json'
                if not manifest.is_file():
                    continue
                try:
                    info = json.loads(manifest.read_text())
                    if info.get('name'):
                        packages.append({'name': info['name'], 'version': info.get('version'), 'path': str(package.resolve())})
                    roots.append(package / 'node_modules')
                except (OSError, ValueError) as exc:
                    warnings.append(f'{manifest}: {exc}')
    if (repo / '.pnp.cjs').exists():
        script = r"""const p=require('pnpapi'),fs=require('fs'); console.log(JSON.stringify([...p.getAllLocators()].filter(x=>x.reference).map(x=>{const d=p.getPackageInformation(x).packageLocation;const j=JSON.parse(fs.readFileSync(d+'package.json','utf8'));return {name:j.name,version:j.version||null,path:d.replace(/\/$/,'')};})));"""
        try:
            packages.extend(json.loads(subprocess.check_output(['node', '-r', str(repo / '.pnp.cjs'), '-e', script], cwd=repo, text=True, stderr=subprocess.PIPE)))
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            warnings.append(f'PnP 盘点失败：{exc}')
    return list({(p['name'], p['path']): p for p in packages}.values()), warnings


def scan(task, *, method='existing; original installation unknown', installer='unknown', status='detected', preserve=True):
    task = Path(task).resolve()
    packages, warnings = node_packages(task / 'repo')
    entries = [{**p, 'install_method': method, 'installer': installer, 'status': status} for p in packages]
    return update(task, entries, group='project', preserve=preserve,
                  state={'status': status, 'warnings': warnings, 'package_count': len(entries),
                         'lockfiles': [name for name in ('package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml') if (task / 'repo' / name).exists()],
                         'note': '仅盘点 Node 安装包；其他运行时和工具需单独登记。安装存在不代表项目可运行。'})


def project_state(task, status):
    # 失败或安装中不能让旧清单继续表示已安装成功。
    data = update(task)
    entries = [{**e, 'status': 'unverified'} for e in data['dependencies'] if e.get('group') == 'project']
    update(task, entries, state={'status': status}, group='project')


def tool_entries(destination, env, installer, method):
    packages, _ = node_packages(destination)
    entries = [{**p, "installer": installer, "install_method": method, "status": "installed", "category": "verification"} for p in packages]
    browsers = destination / "node_modules/playwright-core/browsers.json"
    if browsers.exists():
        for browser in json.loads(browsers.read_text())["browsers"]:
            if browser["name"] not in ("chromium", "chromium-headless-shell", "ffmpeg"):
                continue
            cache = Path(env["PLAYWRIGHT_BROWSERS_PATH"])
            for installed in cache.glob(browser["name"].replace("-", "_") + "-*"):
                if not (installed / "INSTALLATION_COMPLETE").exists():
                    continue
                revision = installed.name.rsplit("-", 1)[-1]
                revisions = {str(browser["revision"]), *map(str, browser.get("revisionOverrides", {}).values())}
                if revision not in revisions:
                    continue
                entries.append({"name": browser["name"], "version": browser.get("browserVersion"),
                                "category": "verification", "revision": revision, "path": str(installed), "installer": installer,
                                "install_method": method if installer == "unknown" else shlex.join(["node", str(destination / "node_modules/playwright/cli.js"), "install", "chromium"]),
                                "status": "installed", "note": "版本来自驱动安装元数据；未验证启动。"})
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', type=Path, default=Path(__file__).resolve().parent)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('scan', help='只盘点，不安装；保留已知来源，未知安装者记 unknown')
    show = commands.add_parser('show', help='默认显示验证依赖、服务和待分类项，项目包只显示数量')
    show.add_argument('--category', choices=(*CATEGORIES, 'all'))
    classify = commands.add_parser('classify', help='调整已有依赖用途，不改安装来源')
    classify.add_argument('--name', required=True)
    classify.add_argument('--path', required=True)
    classify.add_argument('--category', choices=CATEGORIES, required=True)
    record = commands.add_parser('record', help='补装后登记实际版本；不会替你安装或验证')
    for flag in ('name', 'version', 'method', 'path'):
        record.add_argument('--' + flag, required=True)
    record.add_argument('--category', choices=CATEGORIES, required=True, help='按实际用途选择，不能确定时选 unclassified')
    record.add_argument('--installer', default=actor('unknown'))
    record.add_argument('--status', choices=('installed', 'detected', 'failed', 'unverified'), default='installed')
    args = parser.parse_args()
    if args.command == 'scan':
        scan(args.task)
    elif args.command == 'show':
        data = json.loads((args.task / 'dependencies.json').read_text())
        print(json.dumps(overview(data, args.category), ensure_ascii=False, indent=2))
        return
    elif args.command == 'classify':
        update(args.task, classification=(args.name, str(Path(args.path).expanduser().resolve()), args.category))
    else:
        update(args.task, [{'category': args.category, 'category_source': 'explicit', 'name': args.name, 'version': args.version, 'install_method': args.method,
                            'installer': args.installer, 'path': str(Path(args.path).expanduser().resolve()), 'status': args.status}])
    print(args.task.resolve() / 'dependencies.json')


if __name__ == '__main__':
    main()
