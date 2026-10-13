import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import prepare_deps as d


@unittest.skipUnless(shutil.which('npm') and shutil.which('node'), '需要 Node/npm 才能运行真实依赖测试')
class DependenciesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.cache = self.root / 'shared cache'
        # 本地依赖 + 强制 offline，真实 npm 测试不访问注册表。
        self.env_patch = patch.dict(os.environ, {'npm_config_offline': 'true', 'npm_config_update_notifier': 'false'})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        (self.source / 'package.json').write_text(json.dumps({
            'name': 'cache-fixture', 'version': '1.0.0',
            'dependencies': {'local-fixture': 'file:vendor/local-fixture'}}))
        vendor = self.source / 'vendor/local-fixture'
        vendor.mkdir(parents=True)
        (vendor / 'package.json').write_text(json.dumps({'name': 'local-fixture', 'version': '1.0.0', 'main': 'index.js'}))
        (vendor / 'index.js').write_text('module.exports = 42;')
        self.run_cmd(['npm', 'install', '--package-lock-only', '--ignore-scripts', '--offline', '--no-audit', '--no-fund'], self.source)
        self.run_cmd(['git', 'init', '-q'], self.source)
        self.run_cmd(['git', 'add', '.'], self.source)

    def run_cmd(self, args, cwd):
        return subprocess.check_output(args, cwd=cwd, env=os.environ, text=True, stderr=subprocess.STDOUT).strip()

    def task(self, name):
        task = self.root / name
        task.mkdir()
        shutil.copytree(self.source, task / 'repo', symlinks=True)
        (task / 'deps-config.json').write_text(json.dumps({'cache': str(self.cache), 'project': 'fixture'}))
        return task

    def prepare(self, task):
        with contextlib.redirect_stdout(io.StringIO()):
            return d.prepare(task)

    def test_real_install_then_restore_without_install_and_isolation(self):
        first, second, third = self.task('first'), self.task('second'), self.task('third')
        self.assertEqual(self.prepare(first), 'installed')
        entry = next(e for e in json.loads((first / 'dependencies.json').read_text())['dependencies'] if e['name'] == 'local-fixture')
        self.assertEqual(entry['version'], '1.0.0')
        self.assertEqual(entry['installer'], 'prepare_deps.py')
        self.assertIn('npm ci', entry['install_method'])
        with patch.object(d, 'install_command', side_effect=AssertionError('不应再次安装')):
            self.assertEqual(self.prepare(second), 'restored')
        self.assertEqual(self.run_cmd(['node', '-p', 'require("local-fixture")'], second / 'repo'), '42')
        # 复制后的 workspace 链接应指向自己的源码，而不是第一个任务。
        (first / 'repo/vendor/local-fixture/index.js').write_text('module.exports = 99;')
        self.assertEqual(self.run_cmd(['node', '-p', 'require("local-fixture")'], second / 'repo'), '42')
        metadata = second / 'repo/node_modules/.package-lock.json'
        metadata.write_text('changed in second task')
        self.assertEqual(self.prepare(third), 'restored')
        self.assertNotEqual((third / 'repo/node_modules/.package-lock.json').read_text(), metadata.read_text())
        before = json.loads((third / 'dependencies.json').read_text())['dependencies']
        self.assertEqual(self.prepare(third), 'existing')
        self.assertEqual(json.loads((third / 'dependencies.json').read_text())['dependencies'], before)
        entry = next(e for e in before if e['name'] == 'local-fixture')
        self.assertTrue(entry['install_method'].startswith('cache-copy:'))

    def test_source_lock_and_runtime_changes_invalidate_key(self):
        task = self.task('task')
        repo = task / 'repo'
        env, _ = d.environment(self.cache)
        version = self.run_cmd(['npm', '--version'], repo)
        key = d.fingerprint(repo, 'npm', version, env)
        source = repo / 'vendor/local-fixture/index.js'
        source.write_text('module.exports=43;')
        self.assertNotEqual(key, d.fingerprint(repo, 'npm', version, env))
        source.write_text('module.exports = 42;')
        lock = repo / 'package-lock.json'
        lock.write_text(lock.read_text() + '\n')
        self.assertNotEqual(key, d.fingerprint(repo, 'npm', version, env))
        key = d.fingerprint(repo, 'npm', version, env)
        self.assertNotEqual(key, d.fingerprint(repo, 'npm', 'other-version', env))
        original = d.output
        with patch.object(d, 'output', side_effect=lambda args, cwd, env: 'other-node-abi' if args[0] == 'node' else original(args, cwd, env)):
            self.assertNotEqual(key, d.fingerprint(repo, 'npm', version, env))

    def test_business_edit_reuses_but_local_dependency_edit_invalidates(self):
        task = self.task('reuse-business')
        source = task / 'repo/app.js'
        source.write_text('old')
        self.run_cmd(['git', 'add', 'app.js'], task / 'repo')
        self.prepare(task)
        state_path = task / 'deps-state.json'
        old_state = json.loads(state_path.read_text())
        old_state.pop('reuse_key')
        state_path.write_text(json.dumps(old_state))
        self.assertEqual(self.prepare(task), 'existing')
        self.assertIn('reuse_key', json.loads(state_path.read_text()))
        source.write_text('fixed')
        with patch.object(d, 'install_command', side_effect=AssertionError('不应重装')):
            self.assertEqual(self.prepare(task), 'existing')
        # 新增本地依赖文件还未 git add，也必须使同任务复用失效。
        (task / 'repo/vendor/local-fixture/new.js').write_text('new dependency input')
        with self.assertRaises(d.DependencyError):
            self.prepare(task)

    def test_hook_inputs_are_conservative_unless_declared(self):
        task = self.task('hook-inputs')
        repo = task / 'repo'
        p = repo / 'package.json'
        data = json.loads(p.read_text())
        data['scripts'] = {'prepare': 'node build.js'}
        p.write_text(json.dumps(data))
        (repo / 'build.js').write_text('build')
        (repo / 'app.js').write_text('old')
        env, _ = d.environment(self.cache)
        key = d.reuse_fingerprint(repo, 'npm', '11', env)
        declared = d.reuse_fingerprint(repo, 'npm', '11', env, ['build.js'])
        (repo / 'app.js').write_text('fixed')
        self.assertNotEqual(key, d.reuse_fingerprint(repo, 'npm', '11', env))
        self.assertEqual(declared, d.reuse_fingerprint(repo, 'npm', '11', env, ['build.js']))
        (repo / 'build.js').write_text('new build')
        self.assertNotEqual(declared, d.reuse_fingerprint(repo, 'npm', '11', env, ['build.js']))

    def test_install_failure_does_not_publish(self):
        task = self.task('failed')
        with patch.object(d, 'install_command', return_value=['node', '-e', 'process.exit(5)']):
            with self.assertRaisesRegex(d.DependencyError, '退出码 5'):
                self.prepare(task)
        self.assertFalse(list(self.cache.rglob('complete.json')))
        self.assertEqual(json.loads((task / 'deps-state.json').read_text())['status'], 'failed')
        self.assertEqual(json.loads((task / 'dependencies.json').read_text())['preparations']['project']['status'], 'failed')

    def test_failed_partial_install_can_retry_without_deleting_cache(self):
        task = self.task('retry')
        with patch.object(d, 'install_command', return_value=['node', '-e',
                'require("fs").mkdirSync("node_modules",{recursive:true}); process.exit(5)']):
            with self.assertRaises(d.DependencyError):
                self.prepare(task)
        self.assertTrue((task / 'repo/node_modules').is_dir())
        self.assertEqual(self.prepare(task), 'installed')
        self.assertEqual(self.run_cmd(['node', '-p', 'require("local-fixture")'], task / 'repo'), '42')

    def test_browser_profile_only_skips_electron_and_changes_key(self):
        first, second = self.task('full'), self.task('browser')
        config = second / 'deps-config.json'
        data = json.loads(config.read_text())
        data['profile'] = 'browser'
        config.write_text(json.dumps(data))
        cmd = ['node', '-e', 'console.log(JSON.stringify({electron:process.env.ELECTRON_SKIP_BINARY_DOWNLOAD,scripts:process.env.npm_config_ignore_scripts}))']
        with patch.dict(os.environ, {'ELECTRON_SKIP_BINARY_DOWNLOAD': ''}), patch.object(d, 'install_command', return_value=cmd):
            self.prepare(first)
            self.prepare(second)
        a = json.loads((first / 'deps-state.json').read_text())
        b = json.loads((second / 'deps-state.json').read_text())
        self.assertNotEqual(a['key'], b['key'])
        self.assertEqual(b['profile'], 'browser')
        self.assertEqual(json.loads((second / 'result/logs/dependencies.log').read_text())['electron'], '1')

    def test_explicit_retry_accepts_legacy_partial_install(self):
        task = self.task('legacy')
        (task / 'repo/node_modules').mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(d.prepare(task, retry=True), 'installed')

    def test_agent_profile_selection_is_persisted(self):
        task = self.task('profile-selection')
        with contextlib.redirect_stdout(io.StringIO()):
            d.prepare(task, profile_override='browser')
        self.assertEqual(json.loads((task / 'deps-config.json').read_text())['profile'], 'browser')
        self.assertEqual(self.prepare(task), 'existing')

    def test_existing_dependencies_not_destroyed(self):
        task = self.task('existing')
        path = task / 'repo/node_modules/keep'
        path.parent.mkdir()
        path.write_text('user content')
        with self.assertRaisesRegex(d.DependencyError, '保留不覆盖'):
            self.prepare(task)
        self.assertEqual(path.read_text(), 'user content')

    def test_install_hooks_only_use_download_cache(self):
        task = self.task('hook')
        package = task / 'repo/package.json'
        data = json.loads(package.read_text())
        data['scripts'] = {'postinstall': 'node -e "console.log(123)"'}
        package.write_text(json.dumps(data))
        self.assertEqual(self.prepare(task), 'download-cache-only')
        self.assertFalse(list(self.cache.rglob('complete.json')))

    def test_external_links_and_native_files_are_not_portable(self):
        task = self.task('links')
        repo = task / 'repo'
        self.assertTrue(d.portable(repo, 'npm', '11.0.0'))
        (repo / 'external').symlink_to(self.source)
        self.assertFalse(d.portable(repo, 'npm', '11.0.0'))
        (repo / 'external').unlink()
        (repo / 'native.node').write_bytes(b'fake')
        self.assertFalse(d.portable(repo, 'npm', '11.0.0'))
        self.assertFalse(d.portable(repo, 'pnpm', '10.0.0'))
        self.assertFalse(d.portable(repo, 'yarn', '4.0.0'))

    def test_manager_ambiguity_and_version_mismatch(self):
        task = self.task('ambiguous')
        (task / 'repo/yarn.lock').write_text('lock')
        with self.assertRaisesRegex(d.DependencyError, '唯一锁文件'):
            self.prepare(task)
        package = task / 'repo/package.json'
        data = json.loads(package.read_text())
        data['packageManager'] = 'npm@0.0.0'
        package.write_text(json.dumps(data))
        with self.assertRaisesRegex(d.DependencyError, '当前为'):
            self.prepare(task)

    def test_manager_commands_keep_lockfiles_and_share_store(self):
        store = self.root / 'downloads'
        self.assertIn('--store-dir', d.install_command('pnpm', '10.0.0', store))
        self.assertIn('--frozen-lockfile', d.install_command('yarn', '1.22.0', store))
        self.assertIn('--immutable', d.install_command('yarn', '4.0.0', store))


if __name__ == '__main__':
    unittest.main()
