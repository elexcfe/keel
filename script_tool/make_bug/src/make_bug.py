#!/usr/bin/env python3
"""从 GitHub Issue 准备 pi 的本地复现任务。仅依赖 Python 标准库。"""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

from progress import run_progress


class WorkflowError(Exception):
    pass


def command(args, cwd=None):
    # 只对显式要求进度的长命令流式输出，API JSON 等仍单独捕获。
    if '--progress' in args:
        result = run_progress(args, cwd=cwd, label='下载项目' if 'clone' in args else '更新项目缓存')
        if result.returncode:
            raise WorkflowError(f"命令失败：{shlex.join(str(x) for x in args)}\n{result.stdout[-4000:]}")
        return result.stdout.strip()
    result = subprocess.run([str(x) for x in args], cwd=cwd, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise WorkflowError(f"命令失败：{shlex.join(str(x) for x in args)}\n{result.stderr.strip()}")
    return result.stdout.strip()


def executable(name):
    found = shutil.which(name)
    if not found and name == "gh":
        local = Path.home() / ".local/bin/gh"
        if local.is_file() and os.access(local, os.X_OK):
            found = str(local)
    if not found:
        raise WorkflowError(f"找不到 {name}，请先安装并加入 PATH。")
    return found


def parse_url(url, kind="issues"):
    match = re.fullmatch(
        rf"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/{kind}/([1-9][0-9]*)/?(?:[?#].*)?", url)
    if not match or any(x in (".", "..") for x in match.groups()[:2]):
        raise WorkflowError(f"需要 GitHub {kind} 链接，例如 https://github.com/owner/repo/{kind}/123")
    owner, repo, number = match.groups()
    return owner, repo, int(number)


def api(gh, endpoint, *extra):
    try:
        return json.loads(command([gh, "api", endpoint, *extra]))
    except json.JSONDecodeError as exc:
        raise WorkflowError("GitHub 返回了无效 JSON。") from exc


def closing_prs(gh, owner, repo, number):
    query = """query($owner:String!,$repo:String!,$number:Int!){
      repository(owner:$owner,name:$repo){issue(number:$number){
        closedByPullRequestsReferences(first:100){
          nodes{number merged repository{nameWithOwner}}
          pageInfo{hasNextPage}
        }
      }}
    }"""
    data = api(gh, "graphql", "-f", f"query={query}", "-f", f"owner={owner}",
               "-f", f"repo={repo}", "-F", f"number={number}")
    if data.get("errors"):
        raise WorkflowError("无法查询关联 PR，请使用 --ref 或 --fix-pr。")
    connection = data["data"]["repository"]["issue"]["closedByPullRequestsReferences"]
    if connection["pageInfo"]["hasNextPage"]:
        raise WorkflowError("关联 PR 过多，请使用 --ref 或 --fix-pr。")
    return [p["number"] for p in connection["nodes"]
            if p["merged"] and p["repository"]["nameWithOwner"].lower() == f"{owner}/{repo}".lower()]


def select_ref(gh, owner, repo, number, ref, fix_pr):
    if ref is not None:
        if ref.startswith("-") or not ref.strip():
            raise WorkflowError("--ref 不能为空或以 '-' 开头。")
        return ref, {"method": "explicit-ref", "requested_ref": ref}
    if fix_pr is not None:
        p_owner, p_repo, pr_number = parse_url(fix_pr, "pull")
        if f"{p_owner}/{p_repo}".lower() != f"{owner}/{repo}".lower():
            raise WorkflowError("修复 PR 必须属于 Issue 所在仓库。")
    else:
        candidates = closing_prs(gh, owner, repo, number)
        if len(candidates) != 1:
            raise WorkflowError(f"找到 {len(candidates)} 个已合并的关闭 PR，无法唯一确定版本。"
                                "请补充 --ref <问题版本> 或 --fix-pr <修复PR链接>。")
        pr_number = candidates[0]
    pr = api(gh, f"repos/{owner}/{repo}/pulls/{pr_number}")
    if not pr.get("merged") or not pr.get("merge_commit_sha"):
        raise WorkflowError("修复 PR 尚未合并，无法选择修复前版本；请指定 --ref。")
    sha = pr["merge_commit_sha"]
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise WorkflowError("GitHub 返回了无效的合并提交号。")
    commit = api(gh, f"repos/{owner}/{repo}/commits/{sha}")
    parents = commit.get("parents", [])
    if not parents:
        raise WorkflowError("修复提交没有父提交，请指定 --ref。")
    base = parents[0]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40,64}", base):
        raise WorkflowError("GitHub 返回了无效的父提交号。")
    return base, {"method": "merge-first-parent", "fix_pr": pr["html_url"],
                  "merge_commit": sha, "requested_ref": base,
                  "caution": "第一父提交只是候选；rebase 多提交 PR 可能已包含部分修复，请核对并用 --ref 覆盖。"}


TOOL_ROOT = Path(__file__).resolve().parents[1]
TASK_TEMPLATE = TOOL_ROOT / "resource/prompts/reproduce.md"


def read_task_template():
    try:
        template = TASK_TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowError(f"无法读取 pi 提示词模板：{TASK_TEMPLATE}，请检查文件是否存在且可读。") from exc
    if not template.strip():
        raise WorkflowError(f"pi 提示词模板为空：{TASK_TEMPLATE}")
    return template.rstrip() + "\n"


LAUNCHER = """#!/usr/bin/env bash
set -euo pipefail
task_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$task_dir/run_pi.py" "$@"
"""


def export_snapshot(git, source, ref, destination, temporary):
    sha = command([git, "-C", source, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"])
    archive = temporary / "snapshot.tar"
    command([git, "-C", source, "archive", "--format=tar", f"--output={archive}", sha])
    destination.mkdir()
    with tarfile.open(archive) as bundle:
        # Python 的 data filter 拒绝路径穿越、特殊文件与指向目录外的符号链接。
        bundle.extractall(destination, filter="data")
    if (destination / ".git").exists():
        raise WorkflowError("代码快照包含意外的 .git 目录。")
    command([git, "init", "--quiet", destination])
    command([git, "-C", destination, "add", "--all", "--force"])
    command([git, "-C", destination, "-c", "user.name=make_bug", "-c",
             "user.email=make_bug@localhost", "-c", "core.hooksPath=/dev/null",
             "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "复现初始代码"])
    return sha


def cached_source(gh, git, owner, repo, ref, cache_dir, refresh=False):
    """完整缓存仅供准备阶段使用；Agent 收到的仍是无历史快照。"""
    parent = cache_dir.resolve() / owner.lower()
    source = parent / f"{repo.lower()}.git"
    parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        print("首次下载项目，完成后保留代码缓存……", flush=True)
        with tempfile.TemporaryDirectory(prefix=".clone-", dir=parent) as temp:
            staging = Path(temp) / "source.git"
            command([gh, "repo", "clone", f"{owner}/{repo}", staging, "--", "--bare", "--progress"])
            try:
                staging.rename(source)
            except OSError:
                # 另一次准备可能先完成；已有缓存绝不覆盖。
                if not source.exists():
                    raise
    if command([git, "-C", source, "rev-parse", "--is-bare-repository"]) != "true":
        raise WorkflowError(f"缓存目录不是裸 Git 仓库，请检查或换 --cache-dir：{source}")

    def resolve():
        return command([git, "-C", source, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"])

    if not refresh:
        try:
            sha = resolve()
            print(f"复用代码缓存：{source}", flush=True)
            return source, sha
        except WorkflowError:
            print("缓存缺少目标提交，正在增量更新……", flush=True)
    else:
        print("按要求增量更新缓存……", flush=True)
    command([git, "-C", source, "fetch", "--progress", "--prune", "--tags", "origin",
             "+refs/heads/*:refs/heads/*"])
    return source, resolve()


def prepare(args):
    owner, repo, number = parse_url(args.issue)
    output = (args.output or TOOL_ROOT / "output" / f"{owner}-{repo}-{number}").absolute()
    if output.exists() or output.is_symlink():
        raise WorkflowError(f"输出已存在，拒绝覆盖：{output}。请换 --output。")
    if sys.version_info < (3, 12):
        raise WorkflowError("需要 Python 3.12 或更新版本（安全解压代码快照）。")
    task_template = read_task_template()
    gh, git = executable("gh"), executable("git")
    issue = api(gh, f"repos/{owner}/{repo}/issues/{number}")
    if "pull_request" in issue:
        raise WorkflowError("输入链接对应 Pull Request，请使用 Bug Issue 链接。")
    print("已获取 Issue，正在确定问题版本……", flush=True)
    ref, selection = select_ref(gh, owner, repo, number, args.ref, args.fix_pr)
    output.parent.mkdir(parents=True, exist_ok=True)
    # 失败仅清理本次临时目录；最终目录只在全部生成成功后发布。
    with tempfile.TemporaryDirectory(prefix=".make-bug-", dir=output.parent) as temp:
        temporary = Path(temp)
        result = temporary / "result"
        task = result / "task"
        task.mkdir(parents=True)
        (task / "result/images").mkdir(parents=True)
        (task / "result/logs").mkdir()
        if args.repo:
            source = args.repo.resolve()
            command([git, "-C", source, "rev-parse", "--git-dir"])
        else:
            cache_dir = args.cache_dir or TOOL_ROOT / "cache"
            source, ref = cached_source(gh, git, owner, repo, ref, cache_dir, args.refresh_cache)
        sha = export_snapshot(git, source, ref, task / "repo", temporary)
        body = issue.get("body") or "（Issue 正文为空，请补充复现信息。）"
        (task / "BUG.md").write_text(
            f"# {issue['title']}\n\n以下为 Issue 正文快照，保留原文；不包含评论。"
            "原文可能带有修复线索，请在交给 Agent 前审阅；附件链接未下载。\n\n---\n\n" + body + "\n", encoding="utf-8")
        (task / "TASK.md").write_text(task_template + f"\n## 代码基线\n原仓库提交：`{sha}`。repo/ 的 Git 仅含本地初始快照，不含原仓库历史。\n", encoding="utf-8")
        (task / "run_pi.sh").write_text(LAUNCHER, encoding="utf-8")
        (task / "run_pi.sh").chmod(0o755)
        shutil.copyfile(TOOL_ROOT / "resource/prompts/environment.md", task / "ENVIRONMENT.md")
        for source, target in (("prepare.md", "PREPARE.md"), ("monitor.md", "MONITOR.md"), ("repair.md", "REPAIR.md")):
            shutil.copyfile(TOOL_ROOT / "resource/prompts" / source, task / target)
        for name in ("run_pi.py", "repair.py", "run_isolation.py", "session_stats.py", "process_tree.py", "delivery.py", "command_timeout.mjs"):
            shutil.copyfile(Path(__file__).with_name(name), task / name)
        shutil.copyfile(Path(__file__).with_name("dependency_inventory.py"), task / "dependency_inventory.py")
        from dependency_inventory import update as initialize_inventory
        initialize_inventory(task)
        shutil.copyfile(Path(__file__).with_name("prepare_deps.py"), task / "prepare_deps.py")
        shutil.copyfile(Path(__file__).with_name("prepare_tools.py"), task / "prepare_tools.py")
        shutil.copyfile(Path(__file__).with_name("setup_tools.py"), task / "setup_tools.py")
        shutil.copyfile(Path(__file__).with_name("progress.py"), task / "progress.py")
        from run_scenario import LAUNCHER as SCENARIO_LAUNCHER
        shutil.copyfile(Path(__file__).with_name("run_scenario.py"), task / "run_scenario.py")
        shutil.copyfile(Path(__file__).with_name("evidence.py"), task / "evidence.py")
        (task / "run_scenario.sh").write_text(SCENARIO_LAUNCHER)
        (task / "scenario").mkdir()
        shutil.copyfile(TOOL_ROOT / "resource/prompts/scenario.md", task / "SCENARIO.md")
        shared_tools = getattr(args, "shared_tools_dir", None) or TOOL_ROOT / "tools/shared"
        (task / "tools-config.json").write_text(json.dumps({"shared": str(shared_tools.resolve())}, indent=2) + "\n")
        for directory in ("bin", "node", "python", "tmp"):
            (task / "tools" / directory).mkdir(parents=True, exist_ok=True)
        deps_cache = getattr(args, "deps_cache_dir", None) or TOOL_ROOT / "cache/dependencies"
        (task / "deps-config.json").write_text(json.dumps({
            "cache": str(deps_cache.resolve()), "project": f"{owner.lower()}--{repo.lower()}",
            "profile": getattr(args, "dependency_profile", "full")
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        warnings = ["尚未验证 Bug 能否复现。", "未安装依赖或下载 Issue 附件。",
                    "允许联网安装依赖；禁止网络搜索和查阅修复答案，不提供网络沙箱。",
                    "保留 Issue 原文，未自动过滤正文中的修复线索。"]
        if selection.get("caution"):
            warnings.append(selection["caution"])
        if (task / "repo/.gitmodules").exists():
            warnings.append("仓库包含子模块；快照不包含子模块内容，需要在启动前准备。")
        if (task / "repo/.gitattributes").exists():
            warnings.append("存在 .gitattributes；如使用 Git LFS，快照可能仅包含指针文件。")
        metadata = {"issue_url": issue["html_url"], "repository": f"{owner}/{repo}",
                    "issue_updated_at": issue.get("updated_at"), "commit": sha,
                    "prepared_at": datetime.now(timezone.utc).isoformat(),
                    "selection": selection, "warnings": warnings,
                    "status": "prepared-not-reproduced", "network_isolation": False,
                    "network_policy": "allow-environment-downloads-no-search-or-solutions",
                    "deliverables_dir": "task/result"}
        (result / "preparation.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # 使用独占创建保留目标路径，避免覆盖另一次同时执行的结果。
        output.mkdir()
        try:
            for child in result.iterdir():
                child.rename(output / child.name)
        except BaseException:
            shutil.rmtree(output)
            raise
    print(f"已准备：{output}\n代码版本：{sha}\n注意：" + "\n注意：".join(warnings))
    print(f"启动 pi（可联网准备依赖）：bash {shlex.quote(str(output / 'task/run_pi.sh'))}")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="从 GitHub Issue 生成 Bug 材料、代码快照和 pi 复现入口。")
    parser.add_argument("issue", help="GitHub Issue 链接")
    version = parser.add_mutually_exclusive_group()
    version.add_argument("--ref", help="明确的问题版本：提交号、tag 或本地 Git ref")
    version.add_argument("--fix-pr", help="修复 PR 链接，取合并提交的第一父提交")
    parser.add_argument("--repo", type=Path, help="复用本地 Git 仓库，只读取指定提交，不切换、不拉取")
    parser.add_argument("--cache-dir", type=Path, help="代码缓存根目录，默认本工具的 cache/；使用 --repo 时不适用")
    parser.add_argument("--refresh-cache", action="store_true", help="增量更新缓存后解析版本，用于获取分支最新状态")
    parser.add_argument("--deps-cache-dir", type=Path, help="依赖缓存目录，默认本工具 cache/dependencies；可配合 --repo")
    parser.add_argument("--dependency-profile", choices=("full", "browser"), default="full", help="browser 跳过 Electron 二进制，仅适合浏览器复现；默认 full")
    parser.add_argument("--shared-tools-dir", type=Path, help="常用工具根目录，默认本工具 tools/shared")
    parser.add_argument("--output", type=Path, help="输出目录，必须不存在")
    parser.add_argument("--run", action="store_true", help="生成后立即启动 pi（允许联网准备依赖）")
    args = parser.parse_args(argv)
    if args.repo and (args.cache_dir or args.refresh_cache):
        parser.error("--repo 与 --cache-dir/--refresh-cache 不能同时使用")
    try:
        output = prepare(args)
        if args.run:
            return subprocess.call(["bash", str(output / "task/run_pi.sh")])
        return 0
    except (WorkflowError, OSError, tarfile.TarError) as exc:
        print(f"准备失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
