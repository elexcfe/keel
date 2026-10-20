"""只读检查 Pi 交付材料，生成 Keel Markdown 导航；不生成任务说明或证据。"""
import hashlib
import difflib
import json
import os
from pathlib import Path
import re
import tarfile
from urllib.parse import quote

import delivery
import evidence
import repair
from run_scenario import RUNTIME_FILES


def check_archive(task, record):
    """核对用于交付差异的归档，避免只看当前源码而漏掉归档损坏。"""
    path = evidence.local_file(task, f'result/runs/{record["run_id"]}/code/source.tar.gz')
    entries = {}
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            if member.name in entries:
                raise ValueError('源码归档有重复文件。')
            if member.issym():
                entry = {'kind': 'symlink', 'target': member.linkname, 'mode': member.mode}
            elif member.isfile():
                with archive.extractfile(member) as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                entry = {'kind': 'file', 'sha256': digest, 'mode': member.mode}
            else:
                raise ValueError('源码归档含未支持的文件类型。')
            entries[member.name] = entry
    expected = record['code']['files']
    entries.update({name: value for name, value in expected.items() if value['kind'] == 'missing' and name not in entries})
    if entries != expected or evidence.digest(json.dumps(entries, sort_keys=True).encode()) != record['code']['digest']:
        raise ValueError('源码归档与运行记录不一致：' + record['run_id'])


def inspect(control, state, task, *, budget=None):
    import task_flow
    errors, warnings, links, commands = [], [], [], {}
    digests = {}
    if budget is None:
        budget = task_flow.usage(control, state)
    for field, limit in (('execution_seconds', 'execution_seconds'), ('cost_usd', 'cost_usd')):
        if budget[field] is None:
            errors.append('执行预算用量未知：' + field)
        elif budget[field] > state['limits'][limit] + 1e-9:
            errors.append('执行预算已超限：' + field)

    def add(root, name, label):
        path = evidence.local_file(root, name)
        if not path.stat().st_size:
            raise ValueError('文件为空：' + name)
        relative = os.path.relpath(path, control)
        digests[relative] = evidence.file_digest(path)
        if root == task and name == 'dependencies.json':
            inventory = json.loads(path.read_text())
            if not isinstance(inventory, dict) or inventory.get('schema') != 1 or not isinstance(inventory.get('dependencies'), list):
                raise ValueError('依赖清单格式无效。')
            # 运行器每次盘点都会刷新这一字段；实际依赖/版本/状态仍全部绑定。
            inventory.pop('updated_at', None)
            preparations = inventory.get('preparations', {})
            if not isinstance(preparations, dict) or any(not isinstance(p, dict) for p in preparations.values()):
                raise ValueError('依赖准备记录格式无效。')
            for preparation in preparations.values():
                preparation.pop('recorded_at', None)
            digests[relative] = evidence.digest(json.dumps(inventory, sort_keys=True).encode())
        links.append({'label': label, 'path': relative})
        return path

    def attempt(action):
        try:
            return action()
        except (OSError, ValueError, KeyError, TypeError, UnicodeError, tarfile.TarError) as exc:
            errors.append(str(exc))
            return None

    for name, label in [('BUG.md', '原问题'), ('dependencies.json', '依赖清单'),
                        ('result/REPRO.md', 'Pi 复现报告'), ('result/FIX.md', 'Pi 修复报告'),
                        ('scenario/manifest.json', '场景约定'), ('scenario/env.sh', '环境准备脚本'),
                        ('scenario/check.sh', '就绪检查脚本'), ('scenario/run.sh', '场景步骤'),
                        ('run_scenario.sh', '正式重跑入口')]:
        attempt(lambda name=name, label=label: add(task, name, label))
    for name in RUNTIME_FILES:
        attempt(lambda name=name: add(task, name, '重跑工具：' + name))
    # 外层独立重跑使用仓库模块；升级审核端也不能沿用旧内容的验收摘要。
    digests['keel_review_runtime'] = evidence.snapshot(
        Path(__file__).parent,
        (*RUNTIME_FILES, 'handoff.py', 'task_flow.py', 'delivery.py', 'session_stats.py',
         'run_pi.py', 'run_isolation.py'),
    )['digest']
    baseline = attempt(lambda: task_flow.reference(control, state, task))
    if state['reference']:
        attempt(lambda: add(control, state['reference'], '外层复现准入'))
    run_path, run = task_flow.latest_run(control, state)
    candidate = None
    if not run or run.get('purpose') != 'repair':
        errors.append('尚无修复执行记录。')
    else:
        attempt(lambda: add(control, str(run_path.relative_to(control)), 'Pi 执行记录'))
        closure = attempt(lambda: json.loads(add(run_path.parent, 'repair-result.json', '修复交付核查').read_text()))
        if run.get('status') != 'completed' or not closure or closure.get('status') != 'awaiting_review':
            errors.append('修复尚未正常完成并通过交付核查。')
        if closure and baseline:
            candidate = attempt(lambda: repair.read_record(task, closure.get('run_id')))
            if closure.get('baseline_run') != baseline['run_id']:
                errors.append('修复核查引用了不同参考运行。')
            if candidate:
                result = repair.compare(baseline, candidate)
                errors.extend(result['errors'])
                if result['status'] != 'checks_passed':
                    errors.append('最终场景检查未通过。')
                current = attempt(lambda: repair.current_matches(task, candidate, source=True))
                errors.extend(current or [])
                report_path = task / 'result/FIX.md'
                if report_path.is_file() and evidence.file_digest(report_path) != closure.get('report_sha256'):
                    errors.append('FIX.md 与修复收尾记录不同，需重新核查交付。')
    review_checks = [check for review in state['reviews'] for check in review.get('checks', [])]
    independent_ids = {check['run_id'] for check in review_checks}
    records = [str(p.relative_to(task)) for p in (task / 'result/runs').glob('*/record.json')
               if p.parent.name not in independent_ids]
    files = delivery.inventory(task, 'repair')
    content = attempt(lambda: evidence.local_file(task, 'result/FIX.md').read_text())
    freshness = delivery.report_freshness(task, 'result/FIX.md', files, content, records)
    if freshness['status'] != 'current':
        errors.append('修复报告进度：' + freshness['status'])
    if candidate and (not freshness['latest_run'] or freshness['latest_run']['run_id'] != candidate['run_id']):
        errors.append('最终候选不是最新正式运行，需要重新核查。')
    for record, label in ((baseline, '修复前'), (candidate, '修复后')):
        if not record:
            continue
        attempt(lambda record=record: check_archive(task, record))
        folder = f'result/runs/{record["run_id"]}'
        for name, description in [('record.json', '观察和检查'), ('code/source.tar.gz', '代码快照'),
                                  ('code/working-tree.diff', '工作树差异')]:
            # 空 diff 是有效的事实，不据此声称没有修改。
            if name.endswith('.diff') and (task / folder / name).is_file() and not (task / folder / name).stat().st_size:
                warnings.append(label + '工作树 diff 为空；比较代码快照判断跨提交变化。')
                continue
            attempt(lambda folder=folder, name=name, label=label, description=description:
                    add(task, folder + '/' + name, label + description))
    descriptor = attempt(lambda: json.loads(add(task, 'result/HANDOFF.json', 'Pi 交付引用').read_text()))
    if descriptor is None:
        errors.append('缺少有效 HANDOFF.json 对象。')
    if descriptor is not None:
        def validate_descriptor():
            if not isinstance(descriptor, dict) or descriptor.get('schema') != 1:
                raise ValueError('HANDOFF.json schema 必须为 1。')
            env = descriptor['environment']
            if not isinstance(env, dict):
                raise ValueError('environment 必须是对象。')
            add(task, env['guide'], 'Pi 环境与服务说明')
            for key in ('prepare', 'check', 'reset'):
                value = env.get(key)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError('缺少环境命令：' + key)
                commands[key] = value
            demos = descriptor['demos']
            if not isinstance(demos, list) or not demos:
                raise ValueError('缺少复现演示导览。')
            expected = {r['run_id']: r for r in (baseline, candidate) if r}
            seen = set()
            for demo in demos:
                if not isinstance(demo, dict):
                    raise ValueError('演示项必须是对象。')
                run_id = demo['run_id']
                if run_id not in expected:
                    raise ValueError('演示轮次不对应参考或最终候选：' + str(run_id))
                seen.add(run_id)
                add(task, demo['guide'], 'Pi 操作与观察导览 ' + run_id)
                artifacts = demo.get('artifacts')
                if not isinstance(artifacts, list) or not artifacts:
                    raise ValueError('演示缺少原始证据。')
                available = {a['path'] for a in expected[run_id]['evidence']['artifacts']}
                for artifact in artifacts:
                    if artifact not in available or not artifact.startswith(('logs/', 'images/', 'videos/')):
                        raise ValueError('演示证据未列入该轮原始记录：' + str(artifact))
                    add(task / 'result/runs' / run_id, artifact, '原始演示 ' + run_id)
            if seen != set(expected) or not baseline or not candidate:
                raise ValueError('需要参考与最终候选两轮的演示。')
        attempt(validate_descriptor)
    warnings.append('命令由 Pi 提供，本入口仅显示，不执行或证明环境已恢复；语义、视频观察点、服务和数据条件由独立验收确认。')
    # 连同最新代码/场景摘要和文件内容绑定审核，修改后不得继承 accepted。
    fingerprint = hashlib.sha256(json.dumps(digests, sort_keys=True).encode()).hexdigest()
    # 外层独立重跑不要求已退出的 Pi 再改报告，也不改变开始审核时绑定的交付指纹。
    # 原始重跑记录单独校验，最后一轮必须通过且与候选代码一致。
    for index, check in enumerate(review_checks):
        def validate_review_check():
            record = repair.read_record(task, check['run_id'])
            path = add(task, f'result/runs/{check["run_id"]}/record.json', '独立验收重跑')
            if evidence.file_digest(path) != check['record_sha256']:
                raise ValueError('独立重跑记录已变化。')
            if index == len(review_checks) - 1 and baseline and candidate:
                if repair.compare(baseline, record)['status'] != 'checks_passed' or record['code']['digest'] != candidate['code']['digest']:
                    raise ValueError('最后独立重跑未通过，或代码与候选不一致。')
                check_archive(task, record)
        attempt(validate_review_check)
    return {'errors': sorted(set(errors)), 'warnings': warnings, 'links': links, 'commands': commands,
            'baseline_run': baseline['run_id'] if baseline else None,
            'candidate_run': candidate['run_id'] if candidate else None, 'fingerprint': fingerprint}


def source_diff(task, before, after):
    """直接比较两份已归档源码，不解压、不运行、不修改原始快照。"""
    def entries(run_id):
        path = evidence.local_file(task, f'result/runs/{run_id}/code/source.tar.gz')
        result = {}
        with tarfile.open(path, 'r:gz') as archive:
            for member in archive:
                if member.isdir():
                    continue
                # 大文件只输出内容摘要，避免巨大派生 diff；读取流也有边界。
                text = None
                digest = hashlib.sha256()
                if member.isfile():
                    stream = archive.extractfile(member)
                    chunks = []
                    while chunk := stream.read(65536):
                        digest.update(chunk)
                        if member.size <= 1024 * 1024:
                            chunks.append(chunk)
                    data = b''.join(chunks)
                    if member.size <= 1024 * 1024 and b'\0' not in data:
                        try:
                            text = data.decode('utf-8')
                        except UnicodeError:
                            pass
                result[member.name] = (member.mode, member.type.decode('ascii'), member.linkname, digest.hexdigest(), text)
        return result
    old, new = entries(before), entries(after)
    lines = ['# 两次代码快照差异；二进制/超过 1 MiB 的文件仅列摘要。\n']
    for name in sorted(old.keys() | new.keys()):
        a, b = old.get(name), new.get(name)
        if a == b:
            continue
        lines.append(f'\n# {name!r}: {a[:4] if a else None} -> {b[:4] if b else None}\n')
        if (a is None or a[4] is not None) and (b is None or b[4] is not None):
            difference = difflib.unified_diff((a[4] if a else '').splitlines(keepends=True),
                                             (b[4] if b else '').splitlines(keepends=True),
                                             fromfile='before/' + name, tofile='after/' + name)
            lines.extend(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n'
                         for line in difference)
    return ''.join(lines)


def write(control, state, task):
    import task_flow
    report = inspect(control, state, task)
    status = task_flow.status(control, state, task, report=report)
    lines = ['# Keel 交付入口', '', '这里只汇集事实与链接；环境、操作、原因与修复说明由 Pi 产出。', '',
             '任务：' + state['task_id'], '', '状态：' + status['stage'], '',
             '参考运行：' + str(report['baseline_run']), '最终候选：' + str(report['candidate_run']), '',
             '## 缺项与限制', '']
    lines.extend('- ' + e.replace('\n', ' ') for e in report['errors'] + report['warnings'])
    lines += ['', '## 环境准备与重跑', '', '先阅读 Pi 环境说明，以下命令由 Pi 提供；工作目录为任务目录，勿在未核查时盲目执行。']
    for key, value in report['commands'].items():
        fence = '`' * max(3, max((len(s) for s in re.findall(r'`+', value)), default=0) + 1)
        lines += ['', key + '：', '', fence + 'bash', value, fence]
    lines += ['', '环境就绪后，在任务目录执行既有正式入口：', '', '```bash', 'bash run_scenario.sh', '```',
              '', '重跑会生成新的正式运行；旧验收不会自动覆盖新运行。', '', '## 原始材料与说明', '']
    lines.extend('- [' + item['label'] + '](' + quote(item['path'], safe='/.-_') + ')' for item in report['links'])
    if report['baseline_run'] and report['candidate_run'] and not report['errors']:
        diff = control / 'CHANGES.diff'
        if diff.is_symlink():
            raise ValueError('派生 diff 不能是符号链接。')
        diff.write_text(source_diff(task, report['baseline_run'], report['candidate_run']))
        lines += ['', '[两次源码快照的实际差异](CHANGES.diff)']
    lines += ['', '## 独立验收与预算', '', '[审核决定和任务引用](task.json)', '',
              '```json', json.dumps({'reviews': state['reviews'], 'budget': status['budget']}, ensure_ascii=False, indent=2), '```', '']
    target = control / 'HANDOFF.md'
    if target.is_symlink():
        raise ValueError('交付入口不能是符号链接。')
    target.write_text('\n'.join(lines))
    return {'path': str(target), 'errors': report['errors'], 'stage': status['stage']}
