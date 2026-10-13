"""场景与源码快照、证据索引和可读报告；不判断 Bug 是否成立。"""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
from urllib.parse import quote


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def local_file(root, name):
    relative = Path(name)
    path = root / relative
    if relative.is_absolute() or '..' in relative.parts or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f'缺少目录内文件：{name}')
    return path


def digest(value):
    return hashlib.sha256(value).hexdigest()


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def snapshot(root, names, archive=None):
    """归档实际读取的同一份内容，同时记录文件模式、链接与缺失文件。"""
    entries = {}
    bundle = tarfile.open(archive, 'w:gz') if archive else None
    try:
        for name in sorted(set(names)):
            path = root / name
            if Path(name).is_absolute() or '..' in Path(name).parts or not path.parent.resolve().is_relative_to(root.resolve()):
                raise ValueError(f'不支持目录外路径：{name}')
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                entries[name] = {'kind': 'missing'}
                continue
            info = tarfile.TarInfo(name)
            info.mode = stat.S_IMODE(mode)
            if stat.S_ISLNK(mode):
                target = os.readlink(path)
                entries[name] = {'kind': 'symlink', 'target': target, 'mode': info.mode}
                info.type, info.linkname = tarfile.SYMTYPE, target
                if bundle:
                    bundle.addfile(info)
            elif stat.S_ISREG(mode):
                data = path.read_bytes()
                entries[name] = {'kind': 'file', 'sha256': digest(data), 'mode': info.mode}
                info.size = len(data)
                if bundle:
                    bundle.addfile(info, io.BytesIO(data))
            else:
                raise ValueError(f'不支持归档目录、子模块或特殊文件：{name}')
    finally:
        if bundle:
            bundle.close()
    return {'digest': digest(json.dumps(entries, sort_keys=True).encode()), 'files': entries}


def source_snapshot(repo, archive=None):
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=repo).decode().split('\0')
    return snapshot(repo, filter(None, names), archive)


def scenario(task, archive=None):
    manifest = json.loads(local_file(task, 'scenario/manifest.json').read_text())
    if not isinstance(manifest, dict) or manifest.get('schema') != 1:
        raise ValueError('scenario/manifest.json 必须是 schema: 1 的对象。')
    for key in ('title', 'data'):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            raise ValueError(f'场景需要非空 {key}。')
    for key in ('steps', 'coverage', 'files'):
        values = manifest.get(key)
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError(f'场景需要非空 {key} 列表。')
    names = set(manifest['files']) | {'scenario/manifest.json', 'scenario/env.sh', 'scenario/check.sh', 'scenario/run.sh'}
    for name in names:
        local_file(task, name)
    record = snapshot(task, names, archive)
    return {**record, 'definition': manifest}


def read_observations(path):
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get('schema') != 1:
        raise ValueError('observations.json 必须是 schema: 1 的对象。')
    for key in ('observations', 'checks', 'limitations'):
        if not isinstance(data.get(key), list):
            raise ValueError(f'observations.json 缺少 {key} 列表。')
    if any(not isinstance(x, str) for x in data['limitations']):
        raise ValueError('limitations 必须是文字列表。')
    for key in ('observations', 'checks'):
        for item in data[key]:
            if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'].strip():
                raise ValueError(f'{key} 需要 name。')
            if key == 'checks' and item.get('status') not in ('passed', 'failed', 'inconclusive'):
                raise ValueError('检查状态必须是 passed / failed / inconclusive。')
            if key == 'observations' and 'value' not in item:
                raise ValueError('观察需要 value。')
            if not isinstance(item.get('evidence'), list) or any(not isinstance(v, str) for v in item['evidence']):
                raise ValueError('每条观察或检查需要 evidence 路径列表，可为空并标记证据不足。')
    return data


def collect_evidence(run, observations):
    artifacts, warnings = [], []
    for folder in ('logs', 'images', 'videos'):
        for path in sorted((run / folder).rglob('*')):
            if path.is_symlink() or not path.resolve().is_relative_to(run.resolve()):
                warnings.append(f'未收录目录外文件或符号链接：{path.relative_to(run)}')
            elif path.is_file():
                try:
                    artifacts.append({'path': path.relative_to(run).as_posix(), 'bytes': path.stat().st_size,
                                      'sha256': file_digest(path)})
                except OSError as exc:
                    warnings.append(f'证据无法读取：{path.relative_to(run)}：{exc}')
    available = {item['path'] for item in artifacts if item['bytes'] > 0}
    if observations is None:
        warnings.append('没有有效观察记录；已有原始材料仍保留。')
    else:
        for item in observations['observations'] + observations['checks']:
            if not item['evidence']:
                warnings.append(f'缺少证据引用：{item["name"]}')
            for name in item['evidence']:
                if name not in available:
                    warnings.append(f'证据缺失、为空或不在本轮材料中：{name}')
        if not observations['observations']:
            warnings.append('未记录观察事实。')
    return {'artifacts': artifacts, 'warnings': warnings}


def markdown(run, record):
    def text(value):
        # 场景内容是数据，不允许注入 HTML、链接或 Markdown 结构。
        value = str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        for char in ('\\', '`', '*', '_', '[', ']', '#', '|'):
            value = value.replace(char, '\\' + char)
        return value.replace('\n', ' ')

    def link(name):
        return f'[{text(name)}]({quote(name, safe="/")})'

    execution = record['execution']
    lines = ['# 场景运行记录', '', f'运行：{text(run.name)}', '',
             f'执行：**{text(execution["status"])}** — {text(execution["reason"])}', '',
             '评审：待人或 AI 根据原问题和证据判断；本报告不自动宣布复现或修复成功。', '']
    definition = record.get('scenario', {}).get('definition', {})
    if definition:
        lines += [f'场景：{text(definition["title"])}', '', f'数据条件：{text(definition["data"])}', '', '操作步骤：', '']
        lines += [f'{i}. {text(step)}' for i, step in enumerate(definition['steps'], 1)]
        lines += ['', f'场景版本：`{record["scenario"]["digest"]}`', '']
    facts = record
    available = {a['path'] for a in record['evidence']['artifacts']}
    for title, key in (('观察事实', 'observations'), ('自动检查', 'checks')):
        lines += [f'## {title}', '']
        for item in facts.get(key, []):
            value = item.get('value') if key == 'observations' else item['status']
            refs = ' · '.join(link(p) for p in item['evidence'] if p in available)
            lines += [f'- {text(item["name"])}：{text(value)}。{refs}']
        if not facts.get(key):
            lines += ['未提供。']
        lines += ['']
    lines += ['## 原始证据', '']
    for artifact in record['evidence']['artifacts']:
        name = artifact['path']
        lines += [f'- {link(name)}']
        if Path(name).suffix.lower() in ('.png', '.jpg', '.jpeg'):
            lines += ['', f'![{text(name)}]({quote(name, safe="/")})', '']
    lines += ['', '## 代码与场景', '']
    if record.get('code'):
        lines += [f'Git 提交：`{record["code"].get("head", "未读取")}`', '',
                  f'本轮源码内容摘要：`{record["code"]["digest"]}`', '']
    for name in ('code/source.tar.gz', 'code/index.json', 'code/working-tree.diff', 'scenario.tar.gz', 'BUG.md'):
        if (run / name).is_file():
            lines += [f'- {link(name)}']
    lines += ['', '源码归档包括 Git 跟踪文件及未忽略的新增文件，不包括 .git、被忽略的依赖、外部环境和数据库。归档保留符号链接，不包含链接指向的外部内容。', '', '## 限制与完整性', '']
    warnings = record['evidence']['warnings'] + record.get('integrity_warnings', []) + facts.get('limitations', [])
    lines += [f'- {text(w)}' for w in warnings] or ['未发现结构性缺失；不代表证据内容已获确认。']
    (run / 'REPORT.md').write_text('\n'.join(lines) + '\n')
