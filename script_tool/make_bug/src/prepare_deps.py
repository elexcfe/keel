#!/usr/bin/env python3
"""任务启动前复用依赖；只发布成功安装且可迁移的 node_modules。"""
import fcntl
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from progress import run_progress


class DependencyError(Exception):
    pass


def output(args, cwd, env):
    return subprocess.check_output(args, cwd=cwd, env=env, text=True).strip()


def detect(repo):
    manifest = repo / 'package.json'
    if not manifest.exists():
        return None
    package = json.loads(manifest.read_text())
    locks = {'npm': ['npm-shrinkwrap.json', 'package-lock.json'],
             'yarn': ['yarn.lock'], 'pnpm': ['pnpm-lock.yaml']}
    declared = package.get('packageManager', '')
    manager = declared.split('@')[0] if declared else None
    present = [name for name, files in locks.items() if any((repo / f).exists() for f in files)]
    if manager and manager not in locks:
        raise DependencyError(f'不支持包管理器 {manager}，请手工准备依赖。')
    if not manager:
        if len(present) != 1:
            raise DependencyError('没有唯一锁文件，无法确定安装方式；请手工准备依赖。')
        manager = present[0]
    if manager not in present:
        raise DependencyError(f'{manager} 缺少锁文件，不自动生成或更换依赖版本。')
    return manager, declared


def install_command(manager, version, downloads):
    if manager == 'npm':
        return ['npm', 'ci', '--no-audit', '--no-fund', '--prefer-offline']
    if manager == 'pnpm':
        return ['pnpm', 'install', '--frozen-lockfile', '--prefer-offline', '--store-dir', str(downloads / 'pnpm')]
    if int(version.split('.')[0]) == 1:
        return ['yarn', 'install', '--frozen-lockfile', '--non-interactive', '--prefer-offline']
    return ['yarn', 'install', '--immutable']


def environment(cache):
    env = dict(os.environ)
    downloads = cache / 'downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    env['npm_config_cache'] = str(downloads / 'npm')
    return env, downloads


def fingerprint(repo, manager, version, env):
    digest = hashlib.sha256()
    runtime = output(['node', '-p', 'JSON.stringify({version:process.version,abi:process.versions.modules,platform:process.platform,arch:process.arch})'], repo, env)
    settings = {k: v for k, v in env.items() if k.lower().startswith(('npm_config_', 'yarn_', 'pnpm_', 'node_', 'electron_')) or k in ('CC', 'CXX', 'CFLAGS', 'LDFLAGS', 'MAKE_BUG_DEPENDENCY_PROFILE')}
    digest.update(json.dumps([2, manager, version, runtime, platform.libc_ver(), settings], sort_keys=True).encode())
    # 安装脚本、本地 workspace 依赖可能受源码影响；保守地把所有已跟踪内容纳入键。
    files = output(['git', 'ls-files', '-z'], repo, env).split('\0')
    for name in sorted(filter(None, files)):
        path = repo / name
        digest.update(name.encode() + b'\0')
        if path.is_symlink():
            digest.update(os.readlink(path).encode())
        elif path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b'<missing>')
    for name in ('.npmrc', '.yarnrc', '.yarnrc.yml'):
        path = Path.home() / name
        if path.is_file():
            digest.update(name.encode() + path.read_bytes())
    return digest.hexdigest()


def reuse_fingerprint(repo, manager, version, env, inputs=None):
    """同任务复用指纹；跨任务安装产物仍使用完整源码指纹。"""
    files = set(filter(None, output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], repo, env).split('\0')))
    files = {name for name in files if 'node_modules' not in Path(name).parts}
    selected = {name for name in files if Path(name).name in {
        'package.json', 'package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock',
        'pnpm-lock.yaml', 'pnpm-workspace.yaml', '.npmrc', '.yarnrc', '.yarnrc.yml',
        '.pnp.cjs', 'binding.gyp', '.node-version', '.nvmrc',
    } or name.startswith(('.yarn/patches/', '.yarn/plugins/', '.yarn/releases/', 'patches/'))}
    hooks = {'preinstall', 'install', 'postinstall', 'prepare', 'prepublish', 'preprepare', 'postprepare'}
    sensitive = False
    for name in sorted(selected):
        if Path(name).name != 'package.json':
            continue
        package = json.loads((repo / name).read_text())
        sensitive |= bool(hooks.intersection(package.get('scripts', {})))
        for group in ('dependencies', 'devDependencies', 'optionalDependencies'):
            for value in package.get(group, {}).values():
                if isinstance(value, str) and value.startswith(('file:', 'link:')):
                    local = (repo / name).parent / value.split(':', 1)[1]
                    local = local.resolve()
                    if not local.is_relative_to(repo.resolve()):
                        raise DependencyError('外部本地依赖无法自动校验，请移入任务目录。')
                    if local.is_file():
                        selected.add(str(local.relative_to(repo.resolve())))
                    elif local.is_dir():
                        for directory, dirs, local_files in os.walk(local, followlinks=False):
                            dirs[:] = [d for d in dirs if d not in ('node_modules', '.git')]
                            selected.update(str((Path(directory) / f).relative_to(repo.resolve())) for f in local_files)
                    else:
                        raise DependencyError(f'本地依赖不存在：{value}')
    if sensitive and inputs is None:
        selected.update(files)  # 未声明安装输入时保守处理
    if inputs is not None:
        if not isinstance(inputs, list) or any(not isinstance(x, str) or Path(x).is_absolute() or '..' in Path(x).parts for x in inputs):
            raise DependencyError('dependency_inputs 必须是仓库内相对 glob 列表。')
        for pattern in inputs:
            selected.update(str(p.relative_to(repo)) for p in repo.glob(pattern) if p.is_file())
    runtime = output(['node', '-p', 'JSON.stringify({version:process.version,abi:process.versions.modules,platform:process.platform,arch:process.arch})'], repo, env)
    settings = {k: v for k, v in env.items() if k.lower().startswith(('npm_config_', 'yarn_', 'pnpm_', 'node_', 'electron_')) or k in ('CC', 'CXX', 'CFLAGS', 'LDFLAGS', 'MAKE_BUG_DEPENDENCY_PROFILE')}
    digest = hashlib.sha256(json.dumps([1, manager, version, runtime, platform.libc_ver(), settings, inputs], sort_keys=True).encode())
    for name in sorted(selected):
        path = repo / name
        digest.update(name.encode() + b'\0')
        digest.update(path.read_bytes() if path.is_file() else b'<missing>')
    for name in ('.npmrc', '.yarnrc', '.yarnrc.yml'):
        path = Path.home() / name
        if path.is_file():
            digest.update(name.encode() + path.read_bytes())
    return digest.hexdigest()


def module_dirs(repo):
    found = []
    for directory, dirs, _ in os.walk(repo, followlinks=False):
        dirs[:] = [d for d in dirs if d != '.git']
        if 'node_modules' in dirs:
            found.append(Path(directory) / 'node_modules')
            dirs.remove('node_modules')
    return found


def portable(repo, manager, version):
    # pnpm 布局元数据与 Yarn PnP/install-state 可能含路径或构建状态，交给管理器重新链接。
    if manager == 'pnpm' or (manager == 'yarn' and int(version.split('.')[0]) >= 2):
        return False
    hooks = {'preinstall', 'install', 'postinstall', 'prepare', 'prepublish', 'preprepare', 'postprepare'}
    for directory, dirs, files in os.walk(repo, followlinks=False):
        dirs[:] = [d for d in dirs if d != '.git']
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink():
                if os.path.isabs(os.readlink(path)) or not path.resolve().is_relative_to(repo):
                    return False
        if 'binding.gyp' in files or any(f.endswith('.node') for f in files):
            return False
        if 'package.json' in files:
            package = json.loads((Path(directory) / 'package.json').read_text())
            if hooks.intersection(package.get('scripts', {})):
                return False
    return True


def prepare(task, retry=False, profile_override=None):
    repo = (task / 'repo').resolve()
    selected = detect(repo)
    if not selected:
        print('非 Node 项目，跳过依赖缓存。')
        return 'not-node'
    config = json.loads((task / 'deps-config.json').read_text())
    if profile_override is not None:
        if profile_override not in ('full', 'browser'):
            raise DependencyError(f'未知依赖模式：{profile_override}')
        config['profile'] = profile_override
        (task / 'deps-config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    cache = Path(config['cache']).expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    manager, declared = selected
    env, downloads = environment(cache)
    profile = config.get('profile', 'full')
    if profile not in ('full', 'browser'):
        raise DependencyError(f'未知依赖模式：{profile}')
    env['MAKE_BUG_DEPENDENCY_PROFILE'] = profile
    if profile == 'browser':
        env['ELECTRON_SKIP_BINARY_DOWNLOAD'] = '1'
        print('浏览器依赖模式：跳过 Electron 二进制下载，其他构建照常执行。')
    version = output([manager, '--version'], repo, env)
    if declared and version != declared.split('@', 1)[1].split('+', 1)[0]:
        raise DependencyError(f'项目要求 {declared}，当前为 {manager}@{version}；请配置对应版本。')
    if manager == 'yarn':
        env['YARN_CACHE_FOLDER'] = str(downloads / ('yarn1' if version.startswith('1.') else 'yarn'))
        if not version.startswith('1.'):
            env['YARN_ENABLE_GLOBAL_CACHE'] = 'false'
    key = fingerprint(repo, manager, version, env)
    reuse_key = reuse_fingerprint(repo, manager, version, env, config.get('dependency_inputs'))
    bucket = cache / 'installed' / config['project'] / key
    bucket.parent.mkdir(parents=True, exist_ok=True)
    state = task / 'deps-state.json'
    log_dir = task / 'result/logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    # 同一缓存键串行发布，失败/中断时临时目录不会成为可命中的缓存。
    with (bucket.parent / (key + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = module_dirs(repo)
        has_existing = bool(existing or (repo / '.pnp.cjs').exists())
        previous = json.loads(state.read_text()) if state.exists() else {}
        if has_existing:
            matching = previous.get('reuse_key') == reuse_key or ('reuse_key' not in previous and previous.get('key') == key)
            if matching and previous.get('status') in ('installed', 'restored', 'download-cache-only') and not retry:
                if 'reuse_key' not in previous:
                    previous['reuse_key'] = reuse_key
                    state.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + '\n')
                print('本任务依赖已准备，保留当前目录。')
                return 'existing'
            if not retry and not (matching and previous.get('status') in ('installing', 'failed')):
                raise DependencyError('已有未匹配的依赖，保留不覆盖。确认需要续装后运行 python3 prepare_deps.py --retry，或自行管理环境并设置 MAKE_BUG_SKIP_DEPS=1。')
            print('重试依赖安装，使用已有下载缓存。')
        status = 'installed'
        manifest = bucket / 'complete.json'
        if manifest.is_file() and not has_existing:
            paths = json.loads(manifest.read_text())['paths']
            for rel in paths:
                relative = Path(rel)
                if relative.is_absolute() or '..' in relative.parts or relative.name != 'node_modules':
                    raise DependencyError('依赖缓存目录记录无效。')
                shutil.copytree(bucket / 'tree' / rel, repo / rel, symlinks=True)
            status = 'restored'
            print('已复制独立的 node_modules，跳过安装。')
        else:
            cmd = install_command(manager, version, downloads)
            print('准备依赖：' + ' '.join(cmd), flush=True)
            attempt = {'key': key, 'reuse_key': reuse_key, 'status': 'installing', 'manager': manager, 'version': version, 'profile': profile}
            state.write_text(json.dumps(attempt, ensure_ascii=False, indent=2) + '\n')
            try:
                with (log_dir / 'dependencies.log').open('w') as log:
                    result = run_progress(cmd, cwd=repo, env=env, log=log, label='准备项目依赖')
            except BaseException:
                attempt['status'] = 'failed'
                state.write_text(json.dumps(attempt, ensure_ascii=False, indent=2) + '\n')
                raise
            if result.returncode:
                attempt.update(status='failed', exit_code=result.returncode)
                state.write_text(json.dumps(attempt, ensure_ascii=False, indent=2) + '\n')
                (log_dir / 'dependencies.json').write_text(json.dumps(attempt, ensure_ascii=False, indent=2) + '\n')
                raise DependencyError(f'依赖安装失败（退出码 {result.returncode}），见 {log_dir / "dependencies.log"}；未发布缓存。修正原因后可重新运行，复用已下载内容。')
            modules = module_dirs(repo)
            if modules and portable(repo, manager, version) and fingerprint(repo, manager, version, env) == key and not manifest.exists():
                with tempfile.TemporaryDirectory(prefix='.deps-', dir=bucket.parent) as temp:
                    staging = Path(temp) / 'entry'
                    staging.mkdir()
                    paths = [str(p.relative_to(repo)) for p in modules]
                    for path, rel in zip(modules, paths):
                        shutil.copytree(path, staging / 'tree' / rel, symlinks=True)
                    (staging / 'complete.json').write_text(json.dumps({'paths': paths}))
                    staging.rename(bucket)
                print('已保存可迁移依赖，下次同环境直接复制。')
            else:
                status = 'download-cache-only'
                print('此项目需要管理器处理构建或链接；复用下载缓存，不直接复用已安装目录。')
        record = {'key': key, 'reuse_key': reuse_key, 'status': status, 'manager': manager, 'version': version, 'profile': profile}
        state.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        (log_dir / 'dependencies.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        return status


def main():
    parser = argparse.ArgumentParser(description='复用或安装任务依赖')
    parser.add_argument('--retry', action='store_true', help='明确允许在已有依赖目录上重新运行包管理器；npm ci 会重建 node_modules')
    parser.add_argument('--profile', choices=('full', 'browser'), help='选择并保存本任务模式；browser 只跳过 Electron 二进制')
    args = parser.parse_args()
    try:
        prepare(Path(__file__).resolve().parent, retry=args.retry, profile_override=args.profile)
        return 0
    except (DependencyError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'依赖准备失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
