# Keel 资料索引

按当前任务找需要的资料，不必每次全部读完。这里记录资料的位置、用途和哪些内容以什么为准，不记录日常进度。

Keel 的目标是加快人与 AI 的协作效率，减少反复解释、等待和返工。早期文档中的“让人跟得上 AI”是这个目标的一部分。

| 想了解什么 | 看哪里 | 阅读时注意 |
| --- | --- | --- |
| 怎样提出开发任务、跟踪进度和关闭任务 | [Issues 工作流](issues-workflow.md) | GitHub Issue 管任务状态，执行计划管实施细节 |
| 产品给谁用、为什么这样做、还有哪些设想 | [产品说明](keel-product-brief.md) | 围绕现有编码 Agent，改善正确交付与人工核查；评审自动化和降本尚待验证 |
| 怎样实现、分几步做、如何验收和处理风险 | [项目计划](keel-project-plan.md) | 回放评审→有限反馈→原始 Issue 对照→按需 Skill/CI；不沿用未测量的工期和指标 |
| 下一项任务是什么、怎样验收 | [复现评审回放原型](../plans/reproduction-review-replay.md) | 只读冻结材料，给有依据的评审建议并分析误判；当前仅登记，不开发、不调用模型 |
| Pi 和 Keel 怎么分工、怎样逐步接入 | [Pi 接入方案](keel-pi-integration-plan.md) | Agent 运行形式和接入方式以此为准；开发顺序由项目计划概括 |
| 怎样监测 Pi 运行、隔离任务并汇总会话成本 | [make_bug 运行监测](../script_tool/make_bug/README.md#pi-运行监测默认开启) | single/split 均默认监测，Linux/WSL；Pi 1.0.4 最小调用与用量读取已验证 |
| 怎样比较 Pi 单次任务与环境/复现两次任务 | [Pi 会话拆分实验](pi-session-experiment.md) | 对照设计、交接边界、会话采集和判定标准；运行进展见关联计划与 Issue |
| 怎么衡量评审、交付与人工成本 | [评测方案](keel-evaluation.md) | 回放质量、原始 Issue 公平对照、共同误判抽检和完整成本；组件效果不等于全流程收益 |
| 用哪些真实 bug 验证能力 | [靶场清单](keel-target-bugs.md) | 使用前核对版本、环境和命令；早期工具与计时步骤以 Pi 接入、评测方案为准 |
| 怎样运行已有场景、新建或迁移复现任务 | [make_bug 使用说明](../script_tool/make_bug/README.md) | 先看“产出与验收分工”和“快速运行”；人工 Actual 场景的本地诊断路径及来源登记见“人工诊断材料”，不作为 Pi 交付示例 |
| 怎样启动已验收任务的修复 | [同会话修复使用说明](../script_tool/make_bug/README.md#同一会话修复闭环)、[修复提示](../script_tool/make_bug/resource/prompts/repair.md) | Pi 同会话修改、检查、纠错和报告；参考准入与最终验收由外层负责 |
| 已有任务与交付入口怎样复用 | [任务入口计划](../plans/task-lifecycle.md)、[交付入口计划](../plans/task-handoff.md) | Issue #11/#12 已有最小 CLI 与 Markdown 入口；显式准入和最终审核仍在外层，真实效果待验证 |
| 怎样修改 pi 的 Bug 复现要求 | [复现提示词模板](../script_tool/make_bug/resource/prompts/reproduce.md) | 修改后影响新生成的 TASK.md；已有任务可单独编辑自己的 TASK.md |
| pi 怎样自主补齐依赖、选择缓存并恢复环境 | [环境恢复要求](../script_tool/make_bug/resource/prompts/environment.md) | 与任务要求一起传入 pi，按用途查看 dependencies.json，优先检查验证依赖和服务，补装后分类登记；区分环境失败和目标 Bug |
| 怎样交付可重跑场景与行为证据 | [场景交付要求](../script_tool/make_bug/resource/prompts/scenario.md) | Pi 产出具体场景与报告，外层独立验收；记录代码快照及原始材料 |
| 怎样识别报告滞后、限时核对视频 | [报告更新与过期检测](../script_tool/make_bug/README.md#收尾与进程清理)、[限时视频检查](../script_tool/make_bug/README.md#限时视频检查) | 提醒 Pi 更新，过期不硬拦 bash；视频工具只校验来源与时间，不代写分析或替代语义验收 |
| 后续可能增加什么能力 | [代码仓知识与 Bug 检索构想](enhancements/repository-memory.md) | 待核心能力完成后推进，当前只做记录 |

## 文档维护记录

- [产品说明与项目计划重写](../plans/docs-rewrite.md)：文档恢复记录，以及 Issue #8 的定位与分阶段评测调整。
- [复现评审回放原型](../plans/reproduction-review-replay.md)：下一项任务的输入隔离、评审建议协议、开发步骤和验收条件；仅登记，尚未实施。
- [Bug 复现工作流实施计划](../plans/make-bug.md)：Issue #2 的实现步骤与检查方法。
- [Pi 运行监测](../plans/pi-run-monitoring.md)：Issue #7 的实现与验证。
- [统一依赖清单](../plans/dependency-inventory.md)：Issue #5 的清单格式、接入与验证。
- [场景运行与证据交付](../plans/scenario-evidence.md)：Issue #4 的替换步骤、检查方法与实际验收。
- [最小修复闭环](../plans/repair-loop.md)：Issue #9 的同会话修复边界、离线验收与单次 Actual 验证记录；共享预算继续后，两条 Actual 路径在外层反馈下完成修复验收；首次交付与报告效率仍有缺口。
- [统一任务入口](../plans/task-lifecycle.md)：Issue #11 的跨阶段关联、预算与审核衔接实现及离线验收。
- [统一交付入口](../plans/task-handoff.md)：Issue #12 的环境恢复、复现演示和修复说明关联实现及离线验收；视频导航按需后置。
- [画面编号实验结论](../plans/video-marker-experiment.md)：路线已终止，实验代码和独立依赖已清理；保留数据、结论和证据出处，不继续扩展。
- [Pi 接入方案调整](../plans/pi-integration-docs.md)：Issue #3 的文档修改步骤和检查方法。

新增、改名、移动或删除资料时，更新这里的链接和说明。文档有了替代版本，也在这里说明，避免继续按旧方案开发。
