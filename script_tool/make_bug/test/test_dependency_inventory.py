import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import dependency_inventory as inventory
import setup_tools


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.task = Path(self.temp.name)

    def package(self, location, name, version):
        path = self.task / 'repo' / location
        path.mkdir(parents=True, exist_ok=True)
        (path / 'package.json').write_text(json.dumps({'name': name, 'version': version}))
        return path

    def test_scan_nested_scoped_pnpm_and_stale_entries(self):
        self.package('node_modules/@scope/a', '@scope/a', '1.2.0')
        self.package('node_modules/a/node_modules/b', 'b', '2.0.0')
        self.package('node_modules/a', 'a', '1.0.0')
        self.package('node_modules/.pnpm/c@3/node_modules/c', 'c', '3.0.0')
        data = inventory.scan(self.task)
        self.assertEqual({e['name'] for e in data['dependencies']}, {'@scope/a', 'a', 'b', 'c'})
        self.assertTrue(all(e['installer'] == 'unknown' for e in data['dependencies']))
        (self.task / 'repo/node_modules/a/package.json').unlink()
        data = inventory.scan(self.task)
        self.assertNotIn('a', {e['name'] for e in data['dependencies']})

    def test_pi_record_preserved_and_failed_install_not_successful(self):
        path = self.package('node_modules/a', 'a', '1.0.0')
        inventory.scan(self.task, method='npm ci', installer='prepare_deps.py', status='installed', preserve=False)
        subprocess.run([sys.executable, inventory.__file__, '--task', str(self.task), 'record', '--category', 'verification',
                        '--name', 'helper', '--version', '2.0', '--method', 'pip install helper==2.0',
                        '--path', str(self.task / 'tools/helper')], env={**os.environ, 'MAKE_BUG_INSTALLER': 'pi'}, check=True, capture_output=True)
        data = inventory.scan(self.task)
        self.assertEqual({e['name']: e['installer'] for e in data['dependencies']}, {'a': 'prepare_deps.py', 'helper': 'pi'})
        inventory.project_state(self.task, 'failed')
        data = inventory.update(self.task)
        self.assertEqual({e['name']: e['status'] for e in data['dependencies']}, {'a': 'unverified', 'helper': 'installed'})
        (path / 'package.json').write_text('{"name":"a","version":"2.0.0"}')
        entry = next(e for e in inventory.scan(self.task)['dependencies'] if e['name'] == 'a')
        self.assertEqual(entry['installer'], 'unknown')

    def test_invalid_inventory_is_not_overwritten(self):
        target = self.task / 'dependencies.json'
        target.write_text('broken')
        with self.assertRaises(ValueError):
            inventory.update(self.task)
        self.assertEqual(target.read_text(), 'broken')

    def test_browser_inventory_contains_version_revision_and_pi(self):
        core = self.package('node_modules/playwright-core', 'playwright-core', '1.55.0')
        (core / 'browsers.json').write_text(json.dumps({'browsers': [{'name': 'chromium', 'revision': '123', 'browserVersion': '140.0.0.0'}]}))
        browser = self.task / 'browsers/chromium-123'
        browser.mkdir(parents=True)
        (browser / 'INSTALLATION_COMPLETE').touch()
        entries = setup_tools.tool_entries(self.task / 'repo', {'PLAYWRIGHT_BROWSERS_PATH': str(browser.parent)}, 'pi', 'npm install')
        chromium = next(e for e in entries if e['name'] == 'chromium')
        self.assertEqual(chromium['version'], '140.0.0.0')
        self.assertEqual(chromium['installer'], 'pi')
        self.assertEqual(chromium['path'], str(browser))
        self.assertEqual(chromium['revision'], '123')

    def test_pnp_probe_failure_is_visible(self):
        (self.task / 'repo').mkdir()
        (self.task / 'repo/.pnp.cjs').touch()
        with patch.object(inventory.subprocess, 'check_output', side_effect=OSError('node missing')):
            data = inventory.scan(self.task)
        self.assertIn('PnP 盘点失败', data['preparations']['project']['warnings'][0])

    def test_concurrent_records_are_merged(self):
        processes = [subprocess.Popen([sys.executable, inventory.__file__, '--task', str(self.task), 'record', '--category', 'verification',
                     '--name', f'helper-{i}', '--version', '1.0', '--method', 'fixture', '--installer', 'pi',
                     '--path', str(self.task / f'tool-{i}')], stdout=subprocess.DEVNULL) for i in range(8)]
        for process in processes:
            self.assertEqual(process.wait(timeout=10), 0)
        self.assertEqual(len(inventory.update(self.task)['dependencies']), 8)

    def test_categories_and_default_view(self):
        self.package('node_modules/react', 'react', '19.0.0')
        self.package('node_modules/playwright', 'playwright', '1.55.0')
        data = inventory.scan(self.task)
        self.assertEqual({e['name']: e['category'] for e in data['dependencies']}, {'react': 'project', 'playwright': 'verification'})
        view = inventory.overview(data)
        self.assertNotIn('project', view['groups'])
        self.assertEqual(view['counts']['project'], 1)
        self.assertEqual(view['groups']['verification'][0]['name'], 'playwright')
        self.assertEqual(inventory.overview(data, 'project')['groups']['project'][0]['name'], 'react')

    def test_explicit_category_survives_reinstall_and_version_change(self):
        path = self.package('node_modules/custom-test', 'custom-test', '1.0')
        inventory.scan(self.task)
        inventory.update(self.task, classification=('custom-test', str(path), 'verification'))
        (path / 'package.json').write_text('{"name":"custom-test","version":"2.0"}')
        data = inventory.scan(self.task, method='npm ci', installer='pi', status='installed', preserve=False)
        entry = data['dependencies'][0]
        self.assertEqual(entry['category'], 'verification')
        self.assertEqual(entry['version'], '2.0')
        self.assertEqual(entry['installer'], 'pi')
        inventory.project_state(self.task, 'failed')
        self.assertEqual(inventory.update(self.task)['dependencies'][0]['status'], 'unverified')

    def test_services_and_test_database_stay_separate_during_scan(self):
        for name, selected in [('postgres', 'services'), ('test-postgres', 'verification'), ('unknown-tool', 'unclassified')]:
            subprocess.run([sys.executable, inventory.__file__, '--task', str(self.task), 'record',
                            '--name', name, '--version', '16', '--method', 'fixture', '--category', selected,
                            '--path', str(self.task / name)], check=True, capture_output=True)
        data = inventory.scan(self.task)
        self.assertEqual(data['summary'], {'project': 0, 'services': 1, 'verification': 1, 'unclassified': 1})
        before = (self.task / 'dependencies.json').read_bytes()
        result = subprocess.run([sys.executable, inventory.__file__, '--task', str(self.task), 'show'], check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)['groups']['services'][0]['name'], 'postgres')
        self.assertEqual((self.task / 'dependencies.json').read_bytes(), before)

    def test_record_requires_category_and_classify_requires_existing_record(self):
        result = subprocess.run([sys.executable, inventory.__file__, '--task', str(self.task), 'record',
                                 '--name', 'a', '--version', '1', '--method', 'fixture', '--path', '/tmp/a'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        with self.assertRaises(ValueError):
            inventory.update(self.task, classification=('missing', '/tmp/a', 'services'))
