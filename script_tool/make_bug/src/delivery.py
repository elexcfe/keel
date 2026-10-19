"""持续记录交付事实；文件完整性与 Bug 语义验收分开。"""
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from urllib.parse import unquote
from evidence import file_digest
from session_stats import save

REQUIRED = ('result/REPRO.md', 'scenario/manifest.json', 'scenario/env.sh', 'scenario/check.sh', 'scenario/run.sh')


def inventory(task, purpose='reproduce'):
    result = {}
    required = REQUIRED + ('result/FIX.md',) if purpose == 'repair' else REQUIRED
    for name in required:
        path = task / name
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(task.resolve()):
                raise ValueError('文件不在任务内')
            result[name] = {'sha256': file_digest(path), 'bytes': path.stat().st_size}
        except (OSError, ValueError) as exc:
            result[name] = {'error': str(exc)}
    return result


def report_freshness(task, report_key, files, content, records):
    """按运行结束时间检查报告是否跟上进度；不判断文字是否真实。"""
    checked_at = datetime.now(timezone.utc)
    report = {'path': report_key, 'sha256': files[report_key].get('sha256'), 'updated_at': None}
    value = {'status': 'no_completed_run', 'report': report, 'latest_run': None,
             'includes_latest_run': None, 'updated_after_run': None,
             'update_delay_seconds': None, 'record_errors': []}
    latest_time = None
    for name in records:
        path = task / name
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(task.resolve()):
                raise ValueError('记录不在任务内或为符号链接')
            record = json.loads(path.read_text())
            if not isinstance(record, dict):
                raise ValueError('记录不是对象')
            # 已解析但尚未写入结束时间的记录仍在运行，不把它当作最新完成轮次。
            if 'finished_at' not in record:
                continue
            if record.get('run_id') != path.parent.name:
                raise ValueError('运行编号与目录不符')
            finished = datetime.fromisoformat(record['finished_at'])
            if finished.tzinfo is None or finished.utcoffset() is None:
                raise ValueError('结束时间缺少时区')
            finished = finished.astimezone(timezone.utc)
            if finished > checked_at:
                raise ValueError('结束时间在未来')
            if latest_time is None or (finished, record['run_id']) > (latest_time, value['latest_run']['run_id']):
                latest_time = finished
                value['latest_run'] = {'run_id': record['run_id'], 'finished_at': finished.isoformat(), 'record': name}
        except (OSError, ValueError, TypeError, UnicodeError, OverflowError) as exc:
            value['record_errors'].append({'record': name, 'error': str(exc)})
    if 'error' in files[report_key] or not files[report_key].get('bytes'):
        value['status'] = 'missing'
        return value
    try:
        updated = datetime.fromtimestamp((task / report_key).stat().st_mtime, timezone.utc)
        report['updated_at'] = updated.isoformat()
        if updated > checked_at:
            raise ValueError('报告修改时间在未来')
        if content is None:
            raise ValueError('报告内容不可读取')
    except (OSError, ValueError, OverflowError) as exc:
        value.update(status='unknown', report_error=str(exc))
        return value
    if value['record_errors']:
        value['status'] = 'unknown'
        return value
    if latest_time is not None:
        run_id = value['latest_run']['run_id']
        value['includes_latest_run'] = re.search(r'(?<![A-Za-z0-9_-])' + re.escape(run_id) + r'(?![A-Za-z0-9_-])', content) is not None
        value['updated_after_run'] = updated >= latest_time
        value['status'] = 'current' if value['includes_latest_run'] and value['updated_after_run'] else 'stale'
        if value['status'] == 'current':
            # 这是当前版本的更新时间差，后续编辑会更新它；不是首次交付延迟。
            value['update_delay_seconds'] = (updated - latest_time).total_seconds()
    return value


def checkpoint(task, run, initial, status, purpose='reproduce'):
    files = inventory(task, purpose)
    missing = [name for name, value in files.items() if 'error' in value or not value.get('bytes')]
    report_name = 'FIX' if purpose == 'repair' else 'REPRO'
    report_key = f'result/{report_name}.md'
    report = task / report_key
    warnings = []
    content = None
    if report_key not in missing:
        if files[report_key] == initial.get(report_key):
            warnings.append('报告与运行前一致，不能证明是本轮交付。')
        try:
            content = report.read_text()
            for target in re.findall(r'\]\(([^)]+)\)', content):
                if '://' in target or target.startswith('#'):
                    continue
                path = report.parent / unquote(target.split('#')[0].strip('<>'))
                if not path.resolve().is_relative_to(task.resolve()) or not path.is_file() or not path.stat().st_size:
                    warnings.append('报告本地链接不可读取：' + target)
            (run / f'{report_name}.partial.md').write_text(content)
        except (OSError, UnicodeError) as exc:
            warnings.append('报告不可读取：' + str(exc))
    runs = task / 'result/runs'
    records = sorted(str(p.relative_to(task)) for p in runs.glob('*/record.json')) if runs.exists() else []
    freshness = report_freshness(task, report_key, files, content, records)
    if freshness['status'] == 'stale':
        warnings.append(f'报告已过期：请在场景结束后更新 {report_key}，包含最新运行 {freshness["latest_run"]["run_id"]} 的结果及未完成项。')
    elif freshness['status'] == 'unknown':
        warnings.append('无法确认报告是否跟上最新场景：记录或报告时间无效，见 report_freshness。')
    value = {'execution': status, 'files': files, 'missing': missing, 'warnings': warnings,
             'run_records': records, 'report_freshness': freshness, 'review': 'pending',
             'delivery': 'incomplete' if missing or warnings else 'files_present',
             'note': '文件存在不证明复现成功；历史场景记录也可能包含在清单中。'}
    save(run / 'delivery.json', value)
    (run / 'DELIVERY.md').write_text('# 交付检查\n\n执行：' + status + '\n\n' +
        '\n'.join('- 缺失：' + name for name in missing) + '\n' +
        '\n'.join('- ' + warning for warning in warnings) +
        '\n\n报告进度：' + freshness['status'] +
        ('；最新运行：' + freshness['latest_run']['run_id'] if freshness['latest_run'] else '') +
        '\n\n场景记录：\n' + '\n'.join('- ' + name for name in records) +
        '\n\n语义验收待完成。原始会话和执行日志见本目录 sessions/、events/。\n')
    return value
