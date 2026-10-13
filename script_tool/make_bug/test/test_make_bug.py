import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import make_bug as m
import update_task


SHA = "a" * 40
BASE = "b" * 40
ISSUE = {"title": "示例 Bug", "body": "步骤一\n\n预期正确，实际错误。",
         "html_url": "https://github.com/example/project/issues/12", "updated_at": "2026-10-09"}


class SelectionTests(unittest.TestCase):
    def test_urls(self):
        self.assertEqual(m.parse_url(ISSUE["html_url"] + "#issuecomment-1"), ("example", "project", 12))
        for url in ["http://github.com/a/b/issues/1", "https://evil.test/a/b/issues/1",
                    "https://github.com/a/b/pull/1", "https://github.com/a/../issues/1"]:
            with self.assertRaises(m.WorkflowError):
                m.parse_url(url)

    def test_ref_does_not_query_pr(self):
        with patch.object(m, "api") as api:
            self.assertEqual(m.select_ref("gh", "example", "project", 12, "v1.0^", None)[0], "v1.0^")
            api.assert_not_called()
        with self.assertRaises(m.WorkflowError):
            m.select_ref("gh", "example", "project", 12, "--bad", None)
        with self.assertRaises(m.WorkflowError):
            m.select_ref("gh", "example", "project", 12, "", None)

    def test_ambiguous_pr(self):
        for candidates in [[], [1, 2]]:
            with patch.object(m, "closing_prs", return_value=candidates), self.assertRaisesRegex(m.WorkflowError, "--ref"):
                m.select_ref("gh", "example", "project", 12, None, None)

    def test_first_parent(self):
        with patch.object(m, "closing_prs", return_value=[42]), patch.object(m, "api", side_effect=[
            {"merged": True, "merge_commit_sha": SHA, "html_url": "https://github.com/example/project/pull/42"},
            {"parents": [{"sha": BASE}, {"sha": "c" * 40}]},
        ]):
            ref, record = m.select_ref("gh", "example", "project", 12, None, None)
            self.assertEqual(ref, BASE)
            self.assertEqual(record["merge_commit"], SHA)

    def test_fix_pr_validation(self):
        with self.assertRaisesRegex(m.WorkflowError, "必须属于"):
            m.select_ref("gh", "example", "project", 12, None, "https://github.com/other/project/pull/3")
        with patch.object(m, "api", return_value={"merged": False}), self.assertRaisesRegex(m.WorkflowError, "尚未合并"):
            m.select_ref("gh", "example", "project", 12, None, "https://github.com/example/project/pull/3")

    def test_graphql_filter_and_pagination(self):
        connection = {"nodes": [
            {"number": 1, "merged": True, "repository": {"nameWithOwner": "example/project"}},
            {"number": 2, "merged": False, "repository": {"nameWithOwner": "example/project"}},
            {"number": 3, "merged": True, "repository": {"nameWithOwner": "other/project"}},
        ], "pageInfo": {"hasNextPage": False}}
        with patch.object(m, "api", return_value={"data": {"repository": {"issue": {"closedByPullRequestsReferences": connection}}}}):
            self.assertEqual(m.closing_prs("gh", "example", "project", 12), [1])
            connection["pageInfo"]["hasNextPage"] = True
            with self.assertRaises(m.WorkflowError):
                m.closing_prs("gh", "example", "project", 12)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "source repo"
        self.repo.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@localhost")
        (self.repo / "app.txt").write_text("bug\n")
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "bug")
        self.base = self.git("rev-parse", "HEAD")
        (self.repo / "app.txt").write_text("fixed\n")
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "secret fix")
        (self.repo / "app.txt").write_text("uncommitted user changes\n")
        self.args = argparse.Namespace(issue=ISSUE["html_url"], ref=self.base, fix_pr=None,
                                       repo=self.repo, output=self.root / "output with spaces", run=False,
                                       cache_dir=self.root / "cache", refresh_cache=False,
                                       shared_tools_dir=self.root / "shared tools")

    def git(self, *args):
        return m.command(["git", "-C", self.repo, *args])

    def prepare(self):
        with patch.object(m, "api", return_value=ISSUE), patch.object(m, "executable", side_effect=lambda name: name), contextlib.redirect_stdout(io.StringIO()):
            return m.prepare(self.args)

    def test_snapshot_and_original_untouched(self):
        head, status = self.git("rev-parse", "HEAD"), self.git("status", "--porcelain")
        output = self.prepare()
        snapshot = output / "task/repo"
        self.assertEqual((snapshot / "app.txt").read_text(), "bug\n")
        self.assertEqual(m.command(["git", "-C", snapshot, "rev-list", "--count", "HEAD"]), "1")
        self.assertEqual(m.command(["git", "-C", snapshot, "remote"]), "")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("status", "--porcelain"), status)
        self.assertEqual((self.repo / "app.txt").read_text(), "uncommitted user changes\n")
        self.assertIn(ISSUE["body"], (output / "task/BUG.md").read_text())
        record = json.loads((output / "preparation.json").read_text())
        self.assertEqual(record["commit"], self.base)
        self.assertFalse(record["network_isolation"])
        self.assertFalse((output / "task/preparation.json").exists())
        self.assertEqual(record["deliverables_dir"], "task/result")
        self.assertTrue((output / "task/result/images").is_dir())
        self.assertTrue((output / "task/result/logs").is_dir())
        self.assertFalse((output / "task/result/REPRO.md").exists())
        self.assertTrue((output / "task/prepare_deps.py").is_file())
        self.assertTrue((output / "task/tools/tmp").is_dir())
        self.assertEqual(json.loads((output / "task/deps-config.json").read_text())["project"], "example--project")

    def test_existing_output_is_untouched(self):
        self.args.output.mkdir()
        marker = self.args.output / "keep"
        marker.write_text("user data")
        with self.assertRaisesRegex(m.WorkflowError, "拒绝覆盖"):
            self.prepare()
        self.assertEqual(marker.read_text(), "user data")

    def test_template_is_loaded_each_time_and_existing_tasks_are_stable(self):
        template = self.root / "prompt.md"
        template.write_text("# 首次模板\n保留 {literal} 和 $shell 文字。\n", encoding="utf-8")
        with patch.object(m, "TASK_TEMPLATE", template):
            first = self.prepare()
            original = (first / "task/TASK.md").read_text()
            self.assertTrue(original.startswith(template.read_text()))
            self.assertIn(self.base, original)
            template.write_text("# 更新模板\n只影响新任务。\n", encoding="utf-8")
            self.args.output = self.root / "new-task"
            second = self.prepare()
        self.assertTrue((second / "task/TASK.md").read_text().startswith("# 更新模板"))
        self.assertEqual((first / "task/TASK.md").read_text(), original)

    def test_missing_or_empty_template_fails_before_network(self):
        template = self.root / "missing.md"
        for content in (None, "  \n"):
            if content is not None:
                template.write_text(content)
            with patch.object(m, "TASK_TEMPLATE", template), patch.object(m, "api") as api:
                with self.assertRaises(m.WorkflowError):
                    m.prepare(self.args)
                api.assert_not_called()
            self.assertFalse(self.args.output.exists())

    def test_bad_revision_publishes_nothing(self):
        self.args.ref = "missing-commit"
        with self.assertRaises(m.WorkflowError):
            self.prepare()
        self.assertFalse(self.args.output.exists())
        self.assertEqual(list(self.root.glob(".make-bug-*")), [])

    def test_github_failure_publishes_nothing(self):
        with patch.object(m, "executable", side_effect=lambda name: name), patch.object(m, "api", side_effect=m.WorkflowError("API failed")):
            with self.assertRaisesRegex(m.WorkflowError, "API failed"):
                m.prepare(self.args)
        self.assertFalse(self.args.output.exists())

    def test_external_symlink_is_rejected(self):
        (self.repo / "outside").symlink_to("/etc/passwd")
        self.git("add", "outside")
        self.git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "external link")
        self.args.ref = "HEAD"
        with self.assertRaises(tarfile.FilterError):
            self.prepare()
        self.assertFalse(self.args.output.exists())

    def test_launcher_cwd_arguments_and_exit_code(self):
        output = self.prepare()
        launcher = output / "task/run_pi.sh"
        subprocess.run(["bash", "-n", str(launcher)], check=True)
        fake = self.root / "fake pi"
        fake.write_text("#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n"
                        "Path('pi-invocation.json').write_text(json.dumps({'installer':os.environ.get('MAKE_BUG_INSTALLER'),'inventory':json.loads(Path('dependencies.json').read_text()),'cwd':os.getcwd(),'args':sys.argv[1:], 'browser_cache':os.environ.get('PLAYWRIGHT_BROWSERS_PATH'), 'task_tools':os.environ.get('MAKE_BUG_TASK_TOOLS')}))\n"
                        "sys.exit(7)\n")
        fake.chmod(0o755)
        result = subprocess.run(["bash", str(launcher), "--print", "--model", "example model"],
                                cwd=self.root, env={**os.environ, "PI_BIN": str(fake)}, capture_output=True)
        self.assertEqual(result.returncode, 7)
        invocation = json.loads((output / "task/pi-invocation.json").read_text())
        self.assertEqual(invocation["installer"], "pi")
        self.assertEqual(invocation["inventory"]["schema"], 1)
        self.assertIn("先读取 dependencies.json", invocation["args"][-1])
        self.assertEqual(invocation["cwd"], str(output / "task"))
        self.assertEqual(invocation["task_tools"], str(output / "task/tools"))
        self.assertTrue(Path(invocation["browser_cache"]).is_relative_to(self.args.shared_tools_dir))
        self.assertNotIn("--no-session", invocation["args"])
        self.assertNotIn("--continue", invocation["args"])
        self.assertNotIn("--resume", invocation["args"])
        self.assertIn("--no-mcp", invocation["args"])
        self.assertIn("--no-context-files", invocation["args"])
        self.assertNotIn("--offline", invocation["args"])
        self.assertEqual(invocation["args"][invocation["args"].index("--tools") + 1],
                         "read,bash,edit,write,grep,find,ls")
        self.assertIn("example model", invocation["args"])
        self.assertIn(str(output / "task/TASK.md"), invocation["args"])
        (output / "task/pi-invocation.json").unlink()
        (output / "task/prepare_deps.py").write_text("raise SystemExit(6)\n")
        result = subprocess.run(["bash", str(launcher)], cwd=self.root,
                                env={**os.environ, "PI_BIN": str(fake), "MAKE_BUG_SKIP_DEPS": "0"}, capture_output=True)
        self.assertEqual(result.returncode, 7)
        self.assertTrue((output / "task/pi-invocation.json").exists())
        state = json.loads((output / "task/result/logs/dependency-preparation.json").read_text())
        self.assertEqual(state, {"status": "failed", "exit_code": 6})
        self.assertTrue((output / "task/ENVIRONMENT.md").is_file())
        self.assertTrue((output / "task/setup_tools.py").is_file())
        self.assertTrue((output / "task/run_scenario.sh").is_file())
        self.assertTrue((output / "task/run_scenario.py").is_file())
        self.assertTrue((output / "task/progress.py").is_file())
        self.assertTrue((output / "task/scenario").is_dir())
        self.assertIn(str(output / "task/SCENARIO.md"), invocation['args'])
        subprocess.run(['bash', '-n', str(output / "task/run_scenario.sh")], check=True)
        invocation = json.loads((output / "task/pi-invocation.json").read_text())
        self.assertIn(str(output / "task/ENVIRONMENT.md"), invocation['args'])

    def test_launcher_shows_dependency_output_before_install_finishes(self):
        task = self.prepare() / 'task'
        fake = self.root / 'fake-pi'
        fake.write_text('#!/bin/sh\nexit 0\n')
        fake.chmod(0o755)
        (task / 'prepare_deps.py').write_text("""import time
from pathlib import Path
print('LIVE_DOWNLOAD_PROGRESS', flush=True)
deadline = time.monotonic() + 3
while not Path('output-seen').exists() and time.monotonic() < deadline:
    time.sleep(.02)
raise SystemExit(0 if Path('output-seen').exists() else 6)
""")
        with subprocess.Popen(['bash', str(task / 'run_pi.sh')], cwd=self.root,
                              env={**os.environ, 'PI_BIN': str(fake), 'MAKE_BUG_SKIP_DEPS': '0'},
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
            for line in process.stdout:
                if 'LIVE_DOWNLOAD_PROGRESS' in line:
                    (task / 'output-seen').touch()
            self.assertEqual(process.wait(timeout=10), 0)
        state = json.loads((task / 'result/logs/dependency-preparation.json').read_text())
        self.assertEqual(state, {'status': 'ready', 'exit_code': 0})
        self.assertIn('LIVE_DOWNLOAD_PROGRESS', (task / 'result/logs/dependency-preparation.log').read_text())

    def test_upgrade_runner_preserves_task_and_config(self):
        task = self.prepare() / 'task'
        (task / 'run_pi.sh').write_text('old launcher\n')
        (task / 'verify.sh').write_text('old verify\n')
        (task / 'repro').mkdir()
        (task / 'repro/baseline.json').write_text('historical evidence')
        (task / 'setup_tools.py').unlink()
        (task / 'TASK.md').write_text('user instructions\n')
        config = (task / 'deps-config.json').read_bytes()
        backup = update_task.update(task)
        self.assertEqual((backup / 'run_pi.sh').read_text(), 'old launcher\n')
        self.assertEqual((task / 'TASK.md').read_text(), 'user instructions\n')
        self.assertEqual((task / 'deps-config.json').read_bytes(), config)
        self.assertEqual((task / 'repo/app.txt').read_text(), 'bug\n')
        self.assertEqual((task / 'run_pi.sh').read_text(), m.LAUNCHER)
        self.assertTrue((task / 'setup_tools.py').is_file())
        self.assertIn('python3 dependency_inventory.py show', (task / 'ENVIRONMENT.md').read_text())
        self.assertFalse((task / 'verify.sh').exists())
        self.assertEqual((backup / 'verify.sh').read_text(), 'old verify\n')
        self.assertEqual((task / 'repro/baseline.json').read_text(), 'historical evidence')
        self.assertTrue((task / 'evidence.py').exists())
        self.assertIsNone(update_task.update(task))

    def test_clone_branch(self):
        original = m.command
        self.args.repo = None

        def run(args, cwd=None):
            if args[:3] == ["gh", "repo", "clone"]:
                return original(["git", "clone", "--bare", self.repo, args[4]])
            return original(args, cwd)

        with patch.object(m, "command", side_effect=run) as commands:
            output = self.prepare()
            self.args.output = self.root / "second-output"
            self.prepare()
        clones = [c for c in commands.call_args_list if c.args[0][:3] == ["gh", "repo", "clone"]]
        self.assertEqual(len(clones), 1)
        self.assertIn("--progress", clones[0].args[0])
        self.assertFalse(any("fetch" in c.args[0] for c in commands.call_args_list))
        self.assertEqual((output / "task/repo/app.txt").read_text(), "bug\n")

    def test_cache_fetches_missing_commit(self):
        cache = self.args.cache_dir / "example/project.git"
        cache.parent.mkdir(parents=True)
        m.command(["git", "clone", "--bare", self.repo, cache])
        (self.repo / "new.txt").write_text("new commit")
        self.git("add", "new.txt")
        self.git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "new")
        self.args.ref = self.git("rev-parse", "HEAD")
        self.args.repo = None
        output = self.prepare()
        self.assertEqual((output / "task/repo/new.txt").read_text(), "new commit")

    def test_cache_refreshes_branch_only_when_requested(self):
        cache = self.args.cache_dir / "example/project.git"
        cache.parent.mkdir(parents=True)
        m.command(["git", "clone", "--bare", self.repo, cache])
        old = self.git("rev-parse", "HEAD")
        branch = self.git("symbolic-ref", "--short", "HEAD")
        (self.repo / "new.txt").write_text("new")
        self.git("add", "new.txt")
        self.git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "new")
        with contextlib.redirect_stdout(io.StringIO()):
            _, sha = m.cached_source("gh", "git", "example", "project", branch, self.args.cache_dir)
            self.assertEqual(sha, old)
            _, sha = m.cached_source("gh", "git", "example", "project", branch, self.args.cache_dir, True)
            self.assertEqual(sha, self.git("rev-parse", "HEAD"))

    def test_failed_clone_leaves_no_cache(self):
        with patch.object(m, "command", side_effect=m.WorkflowError("clone failed")), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(m.WorkflowError):
                m.cached_source("gh", "git", "example", "project", self.base, self.args.cache_dir)
        self.assertFalse((self.args.cache_dir / "example/project.git").exists())
        self.assertEqual(list(self.args.cache_dir.glob("example/.clone-*")), [])

    def test_cli_failure_is_nonzero(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(m.main(["not-a-github-url"]), 1)
        self.assertIn("需要 GitHub", stderr.getvalue())

    def test_cli_run_propagates_agent_status(self):
        with patch.object(m, "prepare", return_value=self.args.output), patch.object(m.subprocess, "call", return_value=9) as run:
            self.assertEqual(m.main([ISSUE["html_url"], "--run"]), 9)
        run.assert_called_once_with(["bash", str(self.args.output / "task/run_pi.sh")])


if __name__ == "__main__":
    unittest.main()
