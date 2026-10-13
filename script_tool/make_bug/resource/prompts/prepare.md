# 本阶段：只准备运行环境

根据 BUG.md 与本地项目说明，按 ENVIRONMENT.md 准备项目、验证工具和必要服务。可以阅读代码以确定环境要求；不得修改业务实现、已有断言或锁定依赖版本，不读取修复答案或其他任务材料。只使用隔离测试数据，不接触生产数据和凭据。只可联网获取环境所需依赖和 BUG 引用附件，不查 Issue 评论、修复 PR 或其他版本。

本阶段不复现目标异常，不编写目标 Bug 的测试，不交付 Bug 结论。依赖登记在 dependencies.json，未知历史安装者保留 unknown。

结束前创建 environment/handoff.json，包含：status（ready 或 blocked）、cwd、start_commands、stop_commands、environment_variables（只列变量名，不含秘密）、ports、data_setup、data_cleanup、checks、limitations。除 status、cwd 为文字外，其余字段均为列表。各命令使用任务内相对路径，不能依赖终端交互状态。checks 引用本阶段实际执行日志；不要编造成功。

创建 environment/check.sh，仅检查依赖/启动条件，不安装、不修改代码、不触发目标 Bug。服务若需启动检查，先执行启动命令再检查，结束时停止自己的服务。第二阶段会重新启动，不能把遗留进程当作交接。即使阻塞也保存已尝试命令、日志和最小解除条件。
