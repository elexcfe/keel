# 环境准备

先运行 `python3 dependency_inventory.py show` 读取 `dependencies.json` 的分组视图：优先检查验证依赖 `verification`、运行服务 `services` 和待分类项 `unclassified`；项目依赖 `project` 默认只显示数量、锁文件、运行时与准备状态，需要排查时加 `--category project` 看明细。清单不是运行保证，使用前检查版本及能否启动。

- 再看 `result/logs/dependency-preparation.json`：`ready` 只表示准备脚本正常退出；`failed` 时从 `dependency-preparation.log` 和 `dependencies.log` 找根因；`skipped` 时自行检查环境。
- 缺少依赖就按项目锁文件和运行需要自行补齐，可联网下载，不可查找 Bug 修复答案。保留业务代码和锁定版本，额外辅助工具不要混进项目依赖。
- 通用工具优先复用 `MAKE_BUG_SHARED_TOOLS`，按精确版本、平台和运行时隔离；不得覆盖其他任务使用的版本。不能安全共享的工具放 `MAKE_BUG_TASK_TOOLS`，仍可复用下载缓存。浏览器数据和凭据只留在当前任务。
- 项目依赖运行 `python3 prepare_deps.py`；已有目录不匹配时先查原因，确认后才加 `--retry`（npm ci 会重建依赖目录）。仅当浏览器任务受阻于 Electron 下载时使用 `--profile browser --retry`，不能宣称 Electron 可用。
- 独立 Playwright 和 Chromium 用 `python3 setup_tools.py --playwright-version <精确版本>`。保留启动器设置的浏览器缓存环境变量，驱动与浏览器版本必须匹配。共享目录只追加新版本；自行发布工具需加锁并在安装检查完成后发布，不能安全处理时使用任务独立目录。
- 上述脚本会自动更新 `dependencies.json`。你自行安装其他依赖后，也必须逐项写入同一文件，至少填写 `name`、`version`、`install_method`、`installer`（填 `pi`），以及实际 `path`、`status` 和用途 `category`。项目启动/构建包填 `project`，正常运行必需的数据库等填 `services`，测试工具或测试专用服务填 `verification`，无法判断填 `unclassified`。可用：`python3 dependency_inventory.py record --category <用途> --name <名称> --version <实际版本> --method '<安装命令>' --installer pi --path <安装路径>`。失败用 `--status failed`；不要把声明范围当实际版本，或把安装成功当启动成功。
- 已有 Node 依赖可用 `python3 dependency_inventory.py scan` 盘点；历史来源不明保留 `unknown`。用途归错时用 `python3 dependency_inventory.py classify --name <名称> --path <安装路径> --category <用途>` 调整，后续盘点保留此判断。不要另写重复依赖清单，报告引用此 JSON，只解释必要配置、异常和启动结果。

同一错误只有新诊断或配置变化后才重试。确实缺权限、凭据或资源时，报告阻塞及最小解除条件；环境失败不算目标 Bug。修复环境后以最新清单和实际运行结果为准，启动器留下的准备状态不会自动刷新。

## 有限排查与命令时限

- shell 命令默认 120 秒超时。安装、构建或长测试确有需要时，显式设置 bash 工具的 timeout（秒），最多 600 秒；更大的值会被执行约束截为 600 秒。长任务应拆成可检查的步骤，不能靠反复重启同一命令延长等待。
- 浏览器缺系统库时，先对实际浏览器执行 `ldd` 查看缺项，再用 `ldconfig -p`、`dpkg-query` 等检查已安装包和库路径。确需搜索时只查有依据的目录，并限制深度；禁止 `find /`、扫描整个用户目录或挂载盘来碰运气，也不读取其他实验/任务的产物。
- 无安装权限时先检查当前任务允许的安装方式；不能安全补齐就记录缺少的库、失败命令和最小解除条件并结束，不等待交互式 sudo 或无限下载。不要修改宿主机全局环境来救场。
- 命令超时是环境/工具异常，不是目标 Bug。保留输出和原因，缩小范围或更换诊断方法后再试；服务用后台方式启动并重定向日志，通过有时限的就绪检查确认启动。

结束前在交接文件或复现报告中列出本任务实际需要的依赖：已验证可用、仍缺失/安装失败、已安装但未验证。每项附检查命令和结果；依赖清单同步实际状态，未知版本不猜测。不要用“安装完成”代替浏览器能启动、服务能访问或项目能运行的检查。
