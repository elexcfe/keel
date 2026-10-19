# Keel 资料索引

按当前任务找需要的资料，不必每次全部读完。这里记录资料的位置、用途和哪些内容以什么为准，不记录日常进度。

Keel 的目标是加快人与 AI 的协作效率，减少反复解释、等待和返工。早期文档中的“让人跟得上 AI”是这个目标的一部分。

| 想了解什么 | 看哪里 | 阅读时注意 |
| --- | --- | --- |
| 怎样提出开发任务、跟踪进度和关闭任务 | [Issues 工作流](issues-workflow.md) | GitHub Issue 管任务状态，执行计划管实施细节 |
| 产品给谁用、为什么这样做、还有哪些设想 | [产品说明](keel-product-brief.md) | 端到端交付与帮助人理解两个亮点、使用过程和范围；效果及商业设想待验证 |
| 怎样实现、分几步做、如何验收和处理风险 | [项目计划](keel-project-plan.md) | 与 Pi 接入和评测方案衔接；旧工期与旧指标仅作草案保留 |
| Pi 和 Keel 怎么分工、怎样逐步接入 | [Pi 接入方案](keel-pi-integration-plan.md) | Agent 运行形式和接入方式以此为准；开发顺序由项目计划概括 |
| 怎样监测 Pi 运行、隔离任务并汇总会话成本 | [make_bug 运行监测](../script_tool/make_bug/README.md#pi-运行监测默认开启) | single/split 均默认监测，Linux/WSL；Pi 1.0.4 最小调用与用量读取已验证 |
| 怎样比较 Pi 单次任务与环境/复现两次任务 | [Pi 会话拆分实验](pi-session-experiment.md) | 对照设计、交接边界、会话采集和判定标准；运行进展见关联计划与 Issue |
| 怎么衡量交付与理解效果、分阶段做对照 | [评测方案](keel-evaluation.md) | 端到端交付率、理解用时/准确率、失败口径；先闭环，再同模型，后主流 Agent 对比，当前不启动付费实验 |
| 用哪些真实 bug 验证能力 | [靶场清单](keel-target-bugs.md) | 使用前核对版本、环境和命令；早期工具与计时步骤以 Pi 接入、评测方案为准 |
| 怎样运行已有场景、新建或迁移复现任务 | [make_bug 使用说明](../script_tool/make_bug/README.md) | 先看“产出与验收分工”和“快速运行”；人工 Actual 场景的本地诊断路径及来源登记见“人工诊断材料”，不作为 Pi 交付示例 |
| 怎样启动已验收任务的修复 | [同会话修复使用说明](../script_tool/make_bug/README.md#同一会话修复闭环)、[修复提示](../script_tool/make_bug/resource/prompts/repair.md) | Pi 同会话修改、检查、纠错和报告；参考准入与最终验收由外层负责 |
| 怎样修改 pi 的 Bug 复现要求 | [复现提示词模板](../script_tool/make_bug/resource/prompts/reproduce.md) | 修改后影响新生成的 TASK.md；已有任务可单独编辑自己的 TASK.md |
| pi 怎样自主补齐依赖、选择缓存并恢复环境 | [环境恢复要求](../script_tool/make_bug/resource/prompts/environment.md) | 与任务要求一起传入 pi，按用途查看 dependencies.json，优先检查验证依赖和服务，补装后分类登记；区分环境失败和目标 Bug |
| 怎样交付可重跑场景与行为证据 | [场景交付要求](../script_tool/make_bug/resource/prompts/scenario.md) | Pi 产出具体场景与报告，外层独立验收；记录代码快照及原始材料 |
| 后续可能增加什么能力 | [代码仓知识与 Bug 检索构想](enhancements/repository-memory.md) | 待核心能力完成后推进，当前只做记录 |

## 文档维护记录

- [产品说明与项目计划重写](../plans/docs-rewrite.md)：文档恢复记录，以及 Issue #8 的定位与分阶段评测调整。
- [Bug 复现工作流实施计划](../plans/make-bug.md)：Issue #2 的实现步骤与检查方法。
- [Pi 运行监测](../plans/pi-run-monitoring.md)：Issue #7 的实现与验证。
- [统一依赖清单](../plans/dependency-inventory.md)：Issue #5 的清单格式、接入与验证。
- [场景运行与证据交付](../plans/scenario-evidence.md)：Issue #4 的替换步骤、检查方法与实际验收。
- [最小修复闭环](../plans/repair-loop.md)：Issue #9 的同会话修复边界、离线验收与单次 Actual 验证记录；本次准入未通过，修复效果仍待验证。
- [Pi 接入方案调整](../plans/pi-integration-docs.md)：Issue #3 的文档修改步骤和检查方法。

新增、改名、移动或删除资料时，更新这里的链接和说明。文档有了替代版本，也在这里说明，避免继续按旧方案开发。
