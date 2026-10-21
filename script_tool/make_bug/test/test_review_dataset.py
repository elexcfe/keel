"""材料配对、标签隔离与版本检查；不把引用有效性当成语义正确性。"""
import copy
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

HOME = Path(__file__).resolve().parents[1] / 'review_data'
spec = importlib.util.spec_from_file_location('review_dataset_prepare', HOME/'prepare.py')
data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(data)


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def descriptor(self, path):
        return {'path': path.name, 'bytes': path.stat().st_size, 'sha256': data.evidence.file_digest(path)}

    def test_source_copy_and_tamper_detection(self):
        p = self.root/'original';p.write_bytes(b'unchanged')
        meta = self.descriptor(p)
        self.assertEqual(data.load_source(self.root, meta), b'unchanged')
        p.write_bytes(b'changed')
        with self.assertRaises(ValueError):data.load_source(self.root, meta)
        meta['path'] = '../outside'
        with self.assertRaises(ValueError):data.load_source(self.root, meta)

    def test_archive_reads_one_regular_member_without_extracting(self):
        p = self.root/'archive.tar'
        with tarfile.open(p, 'w') as archive:
            member = tarfile.TarInfo('../../not-extracted');member.size = 4
            archive.addfile(member, io.BytesIO(b'text'))
        meta = {**self.descriptor(p), 'member': '../../not-extracted'}
        self.assertEqual(data.load_source(self.root, meta), b'text')
        self.assertEqual(list(self.root.iterdir()), [p])
        with tarfile.open(p, 'a') as archive:
            member = tarfile.TarInfo('../../not-extracted');member.size = 4
            archive.addfile(member, io.BytesIO(b'evil'))
        meta.update(self.descriptor(p))
        with self.assertRaises(ValueError):data.load_source(self.root, meta)

    def test_symlink_sources_and_archive_links_rejected(self):
        p = self.root/'original';p.write_bytes(b'original')
        (self.root/'link').symlink_to(p)
        with self.assertRaises(ValueError):
            data.load_source(self.root, {**self.descriptor(p), 'path': 'link'})
        archive_path = self.root/'archive.tar'
        with tarfile.open(archive_path, 'w') as archive:
            member = tarfile.TarInfo('member');member.type = tarfile.SYMTYPE;member.linkname = 'original';archive.addfile(member)
        with self.assertRaises(ValueError):
            data.load_source(self.root, {**self.descriptor(archive_path), 'member': 'member'})

    def test_catalog_is_development_only_and_covers_every_inventoried_run(self):
        catalog = json.loads((HOME/'catalog.json').read_text())
        labels = json.loads((HOME/'labels.json').read_text())
        inventory = json.loads((HOME.parents[2]/'plans/reproduction-review-inventory.json').read_text())
        selected = {c['run_id'] for c in catalog['cases']}
        excluded = {c['run_id'] for c in catalog['excluded']}
        originals = {r['run_id'] for t in inventory['tasks'] for r in t['runs']}
        self.assertFalse(selected & excluded)
        self.assertEqual(selected | excluded, originals)
        self.assertEqual({c['split'] for c in catalog['cases']}, {'development'})
        self.assertEqual(labels['catalog_sha256'], data.evidence.digest(data.review_replay.encoded(catalog)))
        self.assertEqual({c['case_id'] for c in labels['cases']}, {c['case_id'] for c in catalog['cases']})
        self.assertNotIn('suggest_pass', [c['preferred_decision'] for c in labels['cases']])
        self.assertEqual(labels['annotation_provenance']['human_review'], 'not performed')

    def fixture(self):
        catalog = {'cases': [{'case_id': 'case-1', 'run_id': 'run-1', 'issue_group': 'issue-1', 'split': 'development'}]}
        request = {'documents': [{'id': 'm1', 'format': 'text', 'text': 'primary text\n'}]}
        raw = data.review_replay.encoded(request)
        out = self.root/'requests/case-1';out.mkdir(parents=True)
        (out/'request.json').write_bytes(raw)
        label = {'case_id': 'case-1', 'run_id': 'run-1', 'issue_group': 'issue-1', 'request_sha256': data.evidence.digest(raw),
                 'findings': [{'citations': [{'material_id': 'm1', 'start_line': 1, 'end_line': 1, 'quote': 'primary text'}]}]}
        return catalog, {'catalog_sha256': data.evidence.digest(data.review_replay.encoded(catalog)), 'cases': [label]}

    def test_label_check_rejects_changed_input_quote_and_missing_labels(self):
        catalog, labels = self.fixture()
        result = data.check_labels(catalog, labels, self.root)
        self.assertEqual(result['citations_checked'], 1)
        self.assertFalse(result['semantic_accuracy_verified'])
        for mode in ('quote', 'hash', 'missing', 'run'):
            bad = copy.deepcopy(labels)
            if mode == 'quote':bad['cases'][0]['findings'][0]['citations'][0]['quote'] = 'answer'
            elif mode == 'hash':bad['cases'][0]['request_sha256'] = '0'*64
            elif mode == 'run':bad['cases'][0]['run_id'] = 'run-other'
            else:bad['cases'] = []
            with self.subTest(mode=mode), self.assertRaises(ValueError):data.check_labels(catalog, bad, self.root)
        self.assertNotIn('answer', (self.root/'requests/case-1/request.json').read_text())

    def test_issue_group_cannot_cross_development_and_holdout(self):
        catalog, labels = self.fixture()
        c = copy.deepcopy(catalog['cases'][0]);c.update(case_id='case-2', split='heldout');catalog['cases'].append(c)
        label = copy.deepcopy(labels['cases'][0]);label['case_id'] = 'case-2';labels['cases'].append(label)
        labels['catalog_sha256'] = data.evidence.digest(data.review_replay.encoded(catalog))
        with self.assertRaisesRegex(ValueError, '跨组'):data.check_labels(catalog, labels, self.root)

    def test_builder_refuses_existing_output_and_source_task_directory(self):
        catalog = {'cases': [{'record': {'path': 'history/task/result/record.json'}, 'materials': []}]}
        with self.assertRaises(ValueError):data.build(self.root, catalog, self.root)
        with self.assertRaises(ValueError):data.build(self.root, catalog, self.root/'history/task/new')
        self.assertFalse((self.root/'history').exists())


if __name__ == '__main__':
    unittest.main()
