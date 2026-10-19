#!/usr/bin/env python3
"""升级已有任务的运行脚本，保留项目、任务要求、配置和缓存。"""
import argparse
from pathlib import Path
import shutil
import tempfile

from dependency_inventory import update as initialize_inventory
from make_bug import LAUNCHER, TOOL_ROOT
from run_scenario import LAUNCHER as SCENARIO_LAUNCHER


def update(task):
    task = task.resolve()
    for name in ('repo', 'TASK.md', 'BUG.md', 'deps-config.json', 'tools-config.json'):
        if not (task / name).exists():
            raise ValueError(f'不是完整任务目录，缺少 {name}：{task}')
    initialize_inventory(task)
    replacements = {
        name: (TOOL_ROOT / 'src' / name).read_bytes()
        for name in ('repair.py', 'command_timeout.mjs', 'process_tree.py', 'delivery.py', 'run_pi.py', 'run_isolation.py', 'session_stats.py', 'dependency_inventory.py', 'prepare_deps.py', 'prepare_tools.py', 'setup_tools.py', 'run_scenario.py', 'evidence.py', 'progress.py')
    }
    (task / 'scenario').mkdir(exist_ok=True)
    replacements['run_scenario.sh'] = SCENARIO_LAUNCHER.encode()
    replacements['SCENARIO.md'] = (TOOL_ROOT / 'resource/prompts/scenario.md').read_bytes()
    replacements['run_pi.sh'] = LAUNCHER.encode()
    replacements['ENVIRONMENT.md'] = (TOOL_ROOT / 'resource/prompts/environment.md').read_bytes()
    replacements['PREPARE.md'] = (TOOL_ROOT / 'resource/prompts/prepare.md').read_bytes()
    replacements['REPAIR.md'] = (TOOL_ROOT / 'resource/prompts/repair.md').read_bytes()
    replacements['MONITOR.md'] = (TOOL_ROOT / 'resource/prompts/monitor.md').read_bytes()
    obsolete = ('verify.sh', 'verify.py', 'VERIFICATION.md')
    if not any((task / name).exists() for name in obsolete) and all((task / name).is_file() and (task / name).read_bytes() == data
           for name, data in replacements.items()):
        return None
    backups = task / 'runner-backups'
    backups.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='before-update-', dir=backups))
    for name in (*replacements, *obsolete):
        if (task / name).exists():
            shutil.copy2(task / name, backup / name)
    for name, data in replacements.items():
        (task / name).write_bytes(data)
    for name in obsolete:
        (task / name).unlink(missing_ok=True)
    (task / 'run_pi.sh').chmod(0o755)
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', type=Path)
    args = parser.parse_args()
    try:
        backup = update(args.task)
        print(f'运行脚本已升级，原文件备份：{backup}' if backup else '运行脚本已经是当前版本。')
        return 0
    except (ValueError, OSError) as exc:
        print(f'升级失败：{exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
