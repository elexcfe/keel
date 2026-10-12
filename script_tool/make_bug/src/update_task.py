#!/usr/bin/env python3
"""升级已有任务的运行脚本，保留项目、任务要求、配置和缓存。"""
import argparse
from pathlib import Path
import shutil
import tempfile

from make_bug import LAUNCHER, TOOL_ROOT
from verify import LAUNCHER as VERIFY_LAUNCHER


def update(task):
    task = task.resolve()
    for name in ('repo', 'TASK.md', 'BUG.md', 'deps-config.json', 'tools-config.json'):
        if not (task / name).exists():
            raise ValueError(f'不是完整任务目录，缺少 {name}：{task}')
    replacements = {
        name: (TOOL_ROOT / 'src' / name).read_bytes()
        for name in ('prepare_deps.py', 'prepare_tools.py', 'setup_tools.py', 'verify.py', 'progress.py')
    }
    (task / 'repro').mkdir(exist_ok=True)
    replacements['verify.sh'] = VERIFY_LAUNCHER.encode()
    replacements['VERIFICATION.md'] = (TOOL_ROOT / 'resource/prompts/verification.md').read_bytes()
    replacements['run_pi.sh'] = LAUNCHER.encode()
    replacements['ENVIRONMENT.md'] = (TOOL_ROOT / 'resource/prompts/environment.md').read_bytes()
    if all((task / name).is_file() and (task / name).read_bytes() == data
           for name, data in replacements.items()):
        return None
    backups = task / 'runner-backups'
    backups.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='before-update-', dir=backups))
    for name in replacements:
        if (task / name).exists():
            shutil.copy2(task / name, backup / name)
    for name, data in replacements.items():
        (task / name).write_bytes(data)
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
