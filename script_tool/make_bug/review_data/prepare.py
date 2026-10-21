"""从登记原件按字节组装评审案例；不补写报告、不执行场景、不调用模型。"""
import argparse
import io
import json
import re
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import evidence
import review_replay


def load_source(base, descriptor):
    path = evidence.local_file(base, descriptor['path'])
    for parent in [path, *path.relative_to(base).parents]:
        candidate = parent if parent.is_absolute() else base / parent
        if candidate.is_symlink():
            raise ValueError('不允许源文件符号链接')
    data = path.read_bytes()
    if len(data) != descriptor['bytes'] or evidence.digest(data) != descriptor['sha256']:
        raise ValueError('原件变化：' + descriptor['path'])
    if 'member' in descriptor:
        # 只在内存读取指定普通成员，不把归档路径写入文件系统。
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
            matches = [m for m in archive if m.name == descriptor['member']]
            if len(matches) != 1 or not matches[0].isfile():
                raise ValueError('归档成员缺失、重复或不是普通文件')
            data = archive.extractfile(matches[0]).read()
    return data


def build(base, catalog, output):
    base, output = Path(base).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('输出必须是全新目录')
    for case in catalog['cases']:
        sources = [case['record']] + [m['source'] for m in case['materials']]
        for source in sources:
            parts = Path(source['path']).parts
            boundary = base.joinpath(*parts[:parts.index('task')+1]) if 'task' in parts else (base/source['path']).parent
            if output.resolve().is_relative_to(boundary.resolve()):
                raise ValueError('输出不能落在原始任务或来源目录内')
    output.mkdir(parents=True, exist_ok=False)
    audit = {'schema': 1, 'catalog_sha256': evidence.digest(review_replay.encoded(catalog)),
             'model_called': False, 'originals_changed': False, 'cases': []}
    for case in catalog['cases']:
        cid = case['case_id']
        if not re.fullmatch(r'case-[0-9]+', cid):
            raise ValueError('案例编号非法')
        inputs = output / 'inputs' / cid
        inputs.mkdir(parents=True, exist_ok=False)
        record = load_source(base, case['record'])
        (inputs/'record.json').write_bytes(record)
        manifest = {k: case[k] for k in ('case_id', 'run_id', 'issue_url', 'origin')}
        manifest.update(schema=1, frozen_at=catalog['frozen_at'],
                        record={'path': 'record.json', 'sha256': evidence.digest(record)}, materials=[])
        for item in case['materials']:
            key = item['id']
            if not re.fullmatch(r'm[0-9]+', key):
                raise ValueError('材料编号非法')
            data = load_source(base, item['source'])
            if evidence.digest(data) != item['sha256'] or len(data) != item['bytes']:
                raise ValueError('材料摘要不符：' + key)
            with (inputs/key).open('xb') as stream:
                stream.write(data)
            public = {k: item[k] for k in ('id', 'role', 'format', 'sha256')}
            public.update(path=key)
            for k in ('snapshot_path', 'evidence_path'):
                if k in item:
                    public[k] = item[k]
            manifest['materials'].append(public)
        evidence.save(inputs/'manifest.json', manifest)
        result = review_replay.replay(inputs, 'manifest.json', output/'requests'/cid)
        if result['status'] != 'prepared':
            raise ValueError(f'{cid} 输入准备失败：{result.get("error")}')
        audit['cases'].append({'case_id': cid, 'request_sha256': result['request_sha256'],
                               'manifest_sha256': result['manifest_sha256'], 'mechanical': result['mechanical']})
    evidence.save(output/'build.json', audit)
    return audit


def check_labels(catalog, labels, output):
    """评估侧检查：只核对标签引用/分组/输入绑定，不证明标注语义正确。"""
    if labels['catalog_sha256'] != evidence.digest(review_replay.encoded(catalog)):
        raise ValueError('标签不属于此版材料清单')
    groups, cases = {}, {c['case_id']: c for c in catalog['cases']}
    if len(cases) != len(catalog['cases']) or set(cases) != {c['case_id'] for c in labels['cases']} or len(labels['cases']) != len(cases):
        raise ValueError('案例/标签缺失或重复')
    for case in catalog['cases']:
        group, split = case['issue_group'], case['split']
        if group in groups and groups[group] != split:
            raise ValueError('同一 Issue 跨组泄漏')
        groups[group] = split
    count = 0
    for label in labels['cases']:
        case = cases[label['case_id']]
        raw = (Path(output)/'requests'/case['case_id']/'request.json').read_bytes()
        if evidence.digest(raw) != label['request_sha256']:
            raise ValueError('标签请求摘要不符')
        request = json.loads(raw)
        docs = {d['id']: d for d in request['documents']}
        if label['issue_group'] != case['issue_group'] or label['run_id'] != case['run_id']:
            raise ValueError('标签 Issue/轮次不符')
        for finding in label['findings']:
            if not finding['citations']:
                raise ValueError('标注缺少引用')
            for ref in finding['citations']:
                doc = docs[ref['material_id']]
                lines = doc.get('text', '').splitlines()
                start, end = ref['start_line'], ref['end_line']
                if not 1 <= start <= end <= len(lines) or ref['quote'] != '\n'.join(lines[start-1:end]):
                    raise ValueError('标注引用失配')
                count += 1
    return {'cases': len(cases), 'issue_groups': len(groups), 'citations_checked': count, 'semantic_accuracy_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--check-labels', action='store_true', help='只校验已有输出与评估侧标签；不重新组装')
    args = parser.parse_args()
    home = Path(__file__).resolve().parent
    catalog = json.loads((home/'catalog.json').read_text())
    if args.check_labels:
        result = check_labels(catalog, json.loads((home/'labels.json').read_text()), args.out)
    else:
        result = build(args.base, catalog, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
