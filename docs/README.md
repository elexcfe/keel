# Keel 资料索引

按当前任务找需要的资料，不必每次全部读完。这里记录资料的位置、用途和哪些内容以什么为准，不记录日常进度。

Keel 的目标是加快人与 AI 的协作效率，减少反复解释、等待和返工。早期文档中的“让人跟得上 AI”是这个目标的一部分。

| 想了解什么 | 看哪里 | 阅读时注意 |
| --- | --- | --- |
| 怎样提出开发任务、跟踪进度和关闭任务 | [Issues 工作流](issues-workflow.md) | GitHub Issue 管任务状态，执行计划管实施细节 |
| 产品给谁用、为什么这样做、还有哪些设想 | [产品说明](keel-product-brief.md) | 产品目标、使用过程、范围取舍；商业设想明确标为待验证 |
| 怎样实现、分几步做、如何验收和处理风险 | [项目计划](keel-project-plan.md) | 与 Pi 接入和评测方案衔接；旧工期与旧指标仅作草案保留 |
| Pi 和 Keel 怎么分工、怎样逐步接入 | [Pi 接入方案](keel-pi-integration-plan.md) | Agent 运行形式和接入方式以此为准；开发顺序由项目计划概括 |
| 怎么衡量验证和自动纠错的效果 | [评测方案](keel-evaluation.md) | 包含指标定义和每次任务需要保存的记录 |
| 用哪些真实 bug 验证能力 | [靶场清单](keel-target-bugs.md) | 使用前核对版本、环境和命令；早期工具与计时步骤以 Pi 接入、评测方案为准 |
| 怎样从 GitHub Issue 准备 pi 的 Bug 复现任务 | [make_bug 使用说明](../script_tool/make_bug/README.md) | 一键生成描述、要求和代码快照；区分联网准备和复现约束 |
| 怎样修改 pi 的 Bug 复现要求 | [复现提示词模板](../script_tool/make_bug/resource/prompts/reproduce.md) | 修改后影响新生成的 TASK.md；已有任务可单独编辑自己的 TASK.md |
| pi 怎样自主补齐依赖、选择缓存并恢复环境 | [环境恢复要求](../script_tool/make_bug/resource/prompts/environment.md) | 与任务要求一起传入 pi，自主选择依赖与跨 Issue 缓存，区分环境失败和目标 Bug |
| 怎样固化复现并验证修复 | [验证交付要求](../script_tool/make_bug/resource/prompts/verification.md) | pi 生成复现包；verify.sh 独立重跑并保存每次证据 |
| 后续可能增加什么能力 | [代码仓知识与 Bug 检索构想](enhancements/repository-memory.md) | 待核心能力完成后推进，当前只做记录 |

## 文档维护记录

- [产品说明与项目计划重写](../plans/docs-rewrite.md)：本次恢复的内容、核对清单和完成情况。
- [Bug 复现工作流实施计划](../plans/make-bug.md)：Issue #2 的实现步骤与检查方法。
- [Pi 接入方案调整](../plans/pi-integration-docs.md)：Issue #3 的文档修改步骤和检查方法。

新增、改名、移动或删除资料时，更新这里的链接和说明。文档有了替代版本，也在这里说明，避免继续按旧方案开发。
