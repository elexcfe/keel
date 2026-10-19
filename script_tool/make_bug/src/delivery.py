"""持续记录交付事实；文件完整性与 Bug 语义验收分开。"""
import re
from pathlib import Path
from urllib.parse import unquote
from evidence import file_digest
from session_stats import save

REQUIRED = ('result/REPRO.md', 'scenario/manifest.json', 'scenario/env.sh', 'scenario/check.sh', 'scenario/run.sh')


def inventory(task):
    result = {}
    for name in REQUIRED:
        path = task / name
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(task.resolve()):
                raise ValueError('文件不在任务内')
            result[name] = {'sha256': file_digest(path), 'bytes': path.stat().st_size}
        except (OSError, ValueError) as exc:
            result[name] = {'error': str(exc)}
    return result


def checkpoint(task, run, initial, status):
    files = inventory(task)
    missing = [name for name, value in files.items() if 'error' in value or not value.get('bytes')]
    report = task / 'result/REPRO.md'
    warnings = []
    if 'result/REPRO.md' not in missing:
        if files['result/REPRO.md'] == initial.get('result/REPRO.md'):
            warnings.append('报告与运行前一致，不能证明是本轮交付。')
        try:
            content = report.read_text()
            for target in re.findall(r'\]\(([^)]+)\)', content):
                if '://' in target or target.startswith('#'):
                    continue
                path = report.parent / unquote(target.split('#')[0].strip('<>'))
                if not path.resolve().is_relative_to(task.resolve()) or not path.is_file() or not path.stat().st_size:
                    warnings.append('报告本地链接不可读取：' + target)
            (run / 'REPRO.partial.md').write_text(content)
        except (OSError, UnicodeError) as exc:
            warnings.append('报告不可读取：' + str(exc))
    runs = task / 'result/runs'
    records = sorted(str(p.relative_to(task)) for p in runs.glob('*/record.json')) if runs.exists() else []
    value = {'execution': status, 'files': files, 'missing': missing, 'warnings': warnings,
             'run_records': records, 'review': 'pending',
             'delivery': 'incomplete' if missing or warnings else 'files_present',
             'note': '文件存在不证明复现成功；历史场景记录也可能包含在清单中。'}
    save(run / 'delivery.json', value)
    (run / 'DELIVERY.md').write_text('# 交付检查\n\n执行：' + status + '\n\n' +
        '\n'.join('- 缺失：' + name for name in missing) + '\n' +
        '\n'.join('- ' + warning for warning in warnings) +
        '\n\n场景记录：\n' + '\n'.join('- ' + name for name in records) +
        '\n\n语义验收待完成。原始会话和执行日志见本目录 sessions/、events/。\n')
    return value
