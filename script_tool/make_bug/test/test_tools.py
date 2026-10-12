import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import prepare_tools as t
import setup_tools as s


class ToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.shared = self.root / "shared tools ' $()"

    def task(self, name):
        path = self.root / name
        path.mkdir()
        (path / 'tools-config.json').write_text(json.dumps({'shared': str(self.shared)}))
        return path

    def test_shared_browsers_task_tools_are_separate(self):
        one, two = self.task('one'), self.task('two')
        a, b = t.prepare(one), t.prepare(two)
        self.assertEqual(a['PLAYWRIGHT_BROWSERS_PATH'], b['PLAYWRIGHT_BROWSERS_PATH'])
        self.assertNotEqual(a['MAKE_BUG_TASK_TOOLS'], b['MAKE_BUG_TASK_TOOLS'])
        self.assertNotIn('NODE_PATH', a)
        for key in ('npm_config_cache', 'PIP_CACHE_DIR'):
            self.assertEqual(a[key], b[key])
            self.assertTrue(Path(a[key]).is_dir())
            self.assertTrue(Path(a[key]).is_relative_to(self.shared))
        self.assertEqual(a['PLAYWRIGHT_SKIP_BROWSER_GC'], '1')
        self.assertEqual(json.loads((one / 'result/logs/tools.json').read_text())['prepared_packages'], [])

    def test_shell_exports_quote_paths(self):
        task = self.task('task')
        # 独立执行生成脚本，再交给 Bash，确认空格、引号、$() 均保持字面值。
        (task / 'prepare_tools.py').write_text(Path(t.__file__).read_text())
        result = subprocess.run(['bash', '-c',
            'tools_env="$(python3 ./prepare_tools.py)" || exit; eval "$tools_env"; printf "%s" "$MAKE_BUG_SHARED_TOOLS"'],
            cwd=task, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, str(self.shared))

    def test_version_is_pinned_and_reused_without_package_reinstall(self):
        calls = []
        def run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if cmd[0] == 'npm':
                root = Path(cmd[cmd.index('--prefix') + 1])
                cli = root / 'node_modules/playwright/cli.js'
                cli.parent.mkdir(parents=True, exist_ok=True)
                cli.write_text('fixture')
        with patch.object(s.subprocess, 'check_output', return_value='v24.0.0\n'), patch.object(s.subprocess, 'run', side_effect=run):
            first = s.setup(self.shared, '1.55.0')
            second = s.setup(self.shared, '1.55.0')
        self.assertEqual(first, second)
        self.assertEqual(sum(cmd[0] == 'npm' for cmd, _ in calls), 1)
        self.assertIn('playwright@1.55.0', calls[0][0])
        self.assertTrue(all('PLAYWRIGHT_BROWSERS_PATH' in kwargs['env'] for _, kwargs in calls))
        self.assertTrue((first / 'installed.json').exists())
        task = self.task('inventory')
        t.prepare(task)
        self.assertEqual(json.loads((task / 'result/logs/tools.json').read_text())['prepared_packages'], [str(first)])

    def test_task_setup_uses_own_shared_config(self):
        task = self.task('configured task')
        with patch.object(s, '__file__', str(task / 'setup_tools.py')):
            self.assertEqual(s.default_shared(), self.shared)
            with patch.object(sys, 'argv', ['setup_tools.py', '--playwright-version', '1.55.0']), patch.object(s, 'setup') as setup:
                self.assertEqual(s.main(), 0)
                setup.assert_called_once_with(self.shared, '1.55.0')
            with patch.object(sys, 'argv', ['setup_tools.py', '--playwright-version', '1.55.0', '--shared-tools-dir', str(self.root / 'override')]), patch.object(s, 'setup') as setup:
                self.assertEqual(s.main(), 0)
                setup.assert_called_once_with(self.root / 'override', '1.55.0')

    def test_failed_install_not_marked_ready(self):
        with patch.object(s.subprocess, 'check_output', return_value='v24.0.0'), patch.object(s.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'npm')):
            with self.assertRaises(subprocess.CalledProcessError):
                s.setup(self.shared, '1.55.0')
        self.assertFalse(list(self.shared.rglob('installed.json')))

    def test_latest_rejected_before_install(self):
        with patch.object(s.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                s.setup(self.shared, 'latest')
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
