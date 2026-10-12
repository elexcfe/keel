import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from progress import run_progress


class ProgressTests(unittest.TestCase):
    def test_output_is_visible_before_child_finishes_and_log_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'seen'
            class Terminal(io.StringIO):
                def write(self, text):
                    if '下载阶段' in text:
                        marker.touch()
                    return super().write(text)
            terminal, log = Terminal(), io.StringIO()
            code = """import pathlib, sys, time
print('下载阶段 25%', flush=True)
deadline = time.monotonic() + 3
while not pathlib.Path(sys.argv[1]).exists() and time.monotonic() < deadline:
    time.sleep(.02)
if not pathlib.Path(sys.argv[1]).exists(): sys.exit(9)
print('链接完成', file=sys.stderr, flush=True)
"""
            with contextlib.redirect_stderr(terminal):
                result = run_progress([sys.executable, '-u', '-c', code, str(marker)], log=log)
            self.assertEqual(result.returncode, 0)
            self.assertIn('下载阶段 25%', log.getvalue())
            self.assertIn('链接完成', terminal.getvalue())
            self.assertNotIn('用时', log.getvalue())

    def test_silent_process_shows_activity_and_real_failure_code(self):
        terminal = io.StringIO()
        with contextlib.redirect_stderr(terminal):
            result = run_progress([sys.executable, '-c', 'import time; time.sleep(.25); raise SystemExit(7)'], interval=.02, label='安装')
        self.assertEqual(result.returncode, 7)
        self.assertIn('等待新输出', terminal.getvalue())
        self.assertIn('失败，退出码 7', terminal.getvalue())
        self.assertNotIn('100%', terminal.getvalue())

    def test_terminal_activity_is_cleared(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        terminal = Terminal()
        with contextlib.redirect_stderr(terminal):
            result = run_progress([sys.executable, '-c', 'import time; time.sleep(.25); print("done")'], interval=.02)
        self.assertEqual(result.returncode, 0)
        self.assertIn('\r\033[K', terminal.getvalue())
        self.assertIn('done', terminal.getvalue())


if __name__ == '__main__':
    unittest.main()
