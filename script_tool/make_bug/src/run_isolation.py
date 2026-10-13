"""创建独立任务和缓存副本；这是目录隔离，不是操作系统沙箱。"""
import json
import os
from pathlib import Path
import shutil
import subprocess


def copy_tree(source, destination, *, exclude=()):
    source = Path(source).resolve()
    # 拒绝绝对或跨副本链接，避免复制后仍写到源目录。
    for directory, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = [d for d in dirs if d not in exclude]
        for name in dirs + files:
            if name in exclude:
                continue
            item = Path(directory) / name
            if item.is_symlink() and (os.path.isabs(os.readlink(item)) or not item.resolve().is_relative_to(source)):
                raise ValueError(f'隔离副本不支持绝对或外部链接：{item}')
    shutil.copytree(source, destination, symlinks=True, ignore=shutil.ignore_patterns(*exclude))


def isolate(source, destination, *, cache_seed=None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('隔离目标必须与源任务分离。')
    if destination.exists():
        raise ValueError('隔离目录必须是新目录，不覆盖已有实验。')
    repo = source / 'repo'
    if not (repo / '.git').is_dir() or (repo / '.git/objects/info/alternates').exists():
        raise ValueError('需要有独立 .git 的任务仓库，不支持 worktree 或共享对象库。')
    worktree = subprocess.run(['git', 'config', '--local', '--get', 'core.worktree'], cwd=repo, capture_output=True, text=True)
    if worktree.stdout.strip():
        raise ValueError('不能复制指定了外部 core.worktree 的仓库。')
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo).strip():
        raise ValueError('实验模板仓库必须无改动及未跟踪文件，先准备干净任务；不会清理源任务。')
    destination.mkdir(parents=True)
    task = destination / 'task'
    task.mkdir()
    names = ('TASK.md', 'BUG.md', 'SCENARIO.md', 'ENVIRONMENT.md', 'PREPARE.md', 'MONITOR.md',
             'deps-config.json', 'tools-config.json', 'run_pi.sh', 'run_scenario.sh', 'command_timeout.mjs')
    for name in names:
        path = source / name
        if path.is_file():
            if path.is_symlink():
                raise ValueError(f'不复制链接入口：{path}')
            shutil.copy2(path, task / name)
    for path in source.glob('*.py'):
        if path.is_symlink():
            raise ValueError(f'不复制链接脚本：{path}')
        shutil.copy2(path, task / path.name)
    # 只复制版本库内已跟踪文件；忽略的安装产物和历史证据不带入实验。
    copied_repo = task / 'repo'
    copied_repo.mkdir()
    copy_tree(repo / '.git', copied_repo / '.git')
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=repo).decode().split('\0')
    for name in filter(None, names):
        original, copied = repo / name, copied_repo / name
        copied.parent.mkdir(parents=True, exist_ok=True)
        if original.is_symlink():
            target = os.readlink(original)
            if os.path.isabs(target) or not original.resolve().is_relative_to(repo):
                raise ValueError(f'不支持复制外部源码链接：{original}')
            copied.symlink_to(target)
        elif original.is_file():
            shutil.copy2(original, copied)
        else:
            raise ValueError(f'不支持子模块或非普通源码文件：{original}')
    for name in ('scenario', 'result/logs', 'result/images'):
        (task / name).mkdir(parents=True, exist_ok=True)
    cache = destination / 'cache'
    cache.mkdir()
    if cache_seed:
        seed = Path(cache_seed).resolve()
        if destination.is_relative_to(seed) or seed.is_relative_to(destination):
            raise ValueError('下载缓存种子不能位于隔离目标内或包含它。')
        copy_tree(seed, cache / 'downloads')
    config = json.loads((task / 'deps-config.json').read_text())
    config['cache'] = str(cache)
    (task / 'deps-config.json').write_text(json.dumps(config, indent=2) + '\n')
    (task / 'tools-config.json').write_text(json.dumps({'shared': str(destination / 'shared-tools')}, indent=2) + '\n')
    return task
