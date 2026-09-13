# GitHub Issues 开发任务流程

Keel 的开发任务放在 `elexcfe/keel` 的 GitHub Issues。Issue 保存任务状态和结果；`plans/` 只保存复杂任务的实施细节；`docs/` 保存长期知识。

## 提出任务

用户说明开发目标后，AI 先查已有 Issue，避免重复。明确要求开发或创建任务时，可以创建 Issue；普通讨论和未决定的设想不自动建任务。

## 怎样划分 Issue

- 一个 Issue 对应一个可单独验收的结果：说得清完成后的变化，能用测试、演示或文档检查验证，也能独立审查。
- 修一个明确的 bug、增加一项完整能力、完成一次有明确交付物的文档修订或调查，都可以是一个 Issue。调查的结果可以是有依据的结论，不必是代码。
- 不按文件或操作步骤拆分。例如“保存并重新读取验证结果”可以是一个 Issue，数据库字段、读取接口和测试通常是其中的步骤。
- 一个目标包含多个可独立交付的结果时，拆成几个 Issue，写清依赖；必要时用一个总 Issue 关联。不要把整个版本塞进一个无法独立验收的大任务。
- 错别字等零碎修改通常直接做，或归入已有相关 Issue，不强制单独建任务。
- AI 负责判断复用、新建还是拆分，并简要说明理由。只有涉及目标、范围或优先级选择时才请用户决定。

## Issue 与执行计划

- 简单 Issue 不建计划；复杂 Issue 默认对应一份 `plans/<任务名>.md`，两边互相引用。计划里写 Issue 链接，Issue 中写仓库相对路径；计划已推送时可补可访问的链接。
- Issue 保存目的、范围、验收条件、整体进度、阻塞和完成结果，是任务状态的唯一来源。
- 计划保存实施顺序、相关代码、检查方法和技术决定，可以勾选已完成步骤。重要阶段或阻塞同步到 Issue，不逐条复制操作记录。
- 若计划中出现多个可独立交付的结果，再按需要拆 Issue，不强求一对一。已有已完成计划保留；未完成内容按实际目标整理，不机械迁移全部历史记录。

## 创建方式

使用仓库中的 `.github/ISSUE_TEMPLATE/task.md`：写清问题、本次范围和可检查的完成条件。信息不足且影响目标时先询问，不能替用户编造需求。标题用一句话描述具体结果。

通过 `gh issue create --repo elexcfe/keel --title '任务标题' --body-file <正文文件>` 创建。正文文件保留实际换行，返回成功后读取 Issue 核对并给出链接；请求结果不明时先查重复记录再重试。不要用测试垃圾 Issue 验证权限。

## 执行与状态

1. 开工先用 `gh issue view <编号> --repo elexcfe/keel --comments` 读取目标、验收条件和最近进展。
2. 重要进展、阻塞或方向变化时更新同一 Issue；不为每个操作发评论。写清已完成、剩余工作和阻塞原因。复杂任务关联执行计划，不再建立另一份待办总表。
3. 修复并验证后，把实际检查命令、结果和限制写入 Issue。不能把未运行或失败的检查写成通过。
4. 代码任务默认等修改合入默认分支、验收通过后关闭；仍未合入时记录“实现完成，待合入”。纯文档等任务按约定验收条件关闭。未完成而取消的任务说明原因，以 not planned 关闭。
5. 范围外的问题先记录在当前 Issue；有足够证据且已获后续任务管理授权时再单独建 Issue。不自动开始下一项任务，除非用户已授权这一批任务。

先使用 Open/Closed 和明确的进展评论，不强制标签或看板；有实际需要再添加。不要因为缺少标签阻止创建第一条 Issue。

## 常用操作

```bash
gh auth status
gh repo view elexcfe/keel --json nameWithOwner,hasIssuesEnabled,viewerPermission,isArchived
gh issue list --repo elexcfe/keel --state open
gh issue view 1 --repo elexcfe/keel --comments
# 正文保存在文件后再发送，避免命令行转义破坏内容
gh issue comment 1 --repo elexcfe/keel --body-file <进展文件>
```

若 PATH 中没有 gh，当前环境安装位置为 `~/.local/bin/gh`。首次使用需完成 `gh auth login --hostname github.com --git-protocol ssh --web --skip-ssh-key`。登录在 GitHub 页面完成，不把密码或令牌贴进聊天。

网页模板只有进入 GitHub 默认分支后才会显示；本地 CLI 可以立即根据模板组织正文，不依赖模板发布。
