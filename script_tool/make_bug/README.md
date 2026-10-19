# 为 pi 准备 Bug 复现任务

输入 GitHub Issue 链接，生成问题描述、Agent 要求、指定版本的代码，以及 pi 启动脚本。不改动现有仓库，不覆盖已生成任务。

## 产出与验收分工

Pi 负责具体任务的场景脚本、测试数据恢复方法、观察和断言逻辑、截图/视频采集及复现报告。Keel 提供通用提示、运行器、进程清理、时间预算与交付检查；人或独立验收 AI 负责重跑、核查材料和判断结论。发现产物缺陷时，改进通用提示/生成/校验逻辑，再交由 Pi 重新产出；验收者不修补某次产物来取得通过。Pi 的自述和文件齐全均不能替代外层验收。

## 快速运行

以下命令在 **Keel 仓库根目录** 执行。先按现有任务或新任务选择入口，不需要重新生成已有任务。

| 你现在要做什么 | 使用哪个入口 |
| --- | --- |
| 重跑已经交付的场景、收集新证据 | 任务中的 `run_scenario.sh`，不调用 Pi、不安装依赖 |
| 首次探索问题，或补齐/修改场景 | 任务中的 `run_pi.sh`，会准备依赖并调用已配置的 Pi |
| 从 GitHub Issue 创建新任务 | `src/make_bug.py`，仅准备材料与代码，之后再启动 Pi |
| 更新旧任务的工具 | `src/update_task.py`，更新工具不等于迁移完成场景 |

### 独立验收 Pi 已交付的场景

将下列路径替换为 Pi 已交付场景的任务目录；场景缺失时先交由 Pi 产出，验收者不补写脚本或数据。

```bash
bash /absolute/path/to/task/run_scenario.sh
bash /absolute/path/to/task/run_scenario.sh --repeat 3 --timeout 300
```

`--timeout` 是环境检查、场景执行各自的超时秒数；`--repeat` 为 1–10 次，执行阻塞或出错时停止后续轮次。场景执行与证据完整性、目标异常是否成立分开验收。缺少必需证据时记录部分完成或受阻，不由验收者补齐后再计为 Pi 成功。

本机 Actual #8351 的人工修订场景只作诊断材料，不是正式资源或 Pi 独立交付示例。新克隆不会携带本地诊断文件；后续 Pi 验证应创建干净任务，不复用人工场景作为输入。来源及历史运行归属见“人工诊断材料”。

### 从新 Issue 开始

先满足下文“准备环境”中的 Python、Git、gh 与 Pi 要求。下面以 Actual #7738 为例：

```bash
# 1. 生成材料和代码；输出目录必须不存在
python3 script_tool/make_bug/src/make_bug.py https://github.com/actualbudget/actual/issues/7738

# 2. 启动 Pi，探索问题并按 SCENARIO.md 交付场景
bash script_tool/make_bug/output/actualbudget-actual-7738/task/run_pi.sh
```

第 1 步遇到版本歧义时，按下文说明指定 `--ref` 或 `--fix-pr`。生成器只创建 scenario/ 目录，**不会自动写好场景**。第 2 步需要 Pi 完成 manifest.json、env.sh、check.sh、run.sh 和相关采集脚本；若 Pi 报告缺材料或环境阻塞，先处理报告中的原因。

场景已交付后，无需再启动 Pi 即可重跑：

```bash
# 3. 独立执行已有场景
bash script_tool/make_bug/output/actualbudget-actual-7738/task/run_scenario.sh
```

其他 Issue 使用生成器打印的实际 task 路径；指定 `--output` 后也按该路径运行。脚本会自行切换到任务目录，从其他目录运行时请使用绝对路径。

### 已有旧任务怎样迁移

先更新运行工具，将 `/absolute/path/to/task` 替换成已有任务目录：

```bash
python3 script_tool/make_bug/src/update_task.py /absolute/path/to/task
```

更新会备份并移除旧 verify 入口，**不提供兼容层，也不会自动转换旧 repro/**。随后在该任务启动 `run_pi.sh`，让 Pi 按任务内的 SCENARIO.md 将已有操作、环境检查、测试及证据采集明确迁移到 scenario/，保留历史结果和已有业务改动。旧脚本的 outcome.json、target_failed 与退出码规则不能直接照搬，必须按 [场景交付要求](resource/prompts/scenario.md) 分开执行状态、观察和检查结果。新场景交付后再运行 `run_scenario.sh`。

### 运行后看哪里

终端最后打印本轮 `REPORT.md` 的完整路径。先打开它：里面按操作步骤列出观察、自动检查、图片、视频及其他证据链接，并提供代码快照和改动文件。结果目录为 `task/result/runs/run-时间-随机值/`，每轮独立保存，不覆盖上一次。

需要交给 AI 分析时，提供该轮 REPORT.md、record.json 及引用的原始材料；不要只提供一个退出码。代码改动在 `code/working-tree.diff`，新增文件完整内容在 `code/source.tar.gz`，两者的含义见下文“独立运行场景”。视频由具体场景采集，并非每次运行器启动都会自动生成；缺少视频时查看报告中的限制和证据警告。

### 运行失败怎样处理

| 现象 | 从哪里排查、怎样继续 |
| --- | --- |
| 找不到 run_scenario.sh | 确认任务路径；旧任务先更新工具，新任务先生成材料 |
| 缺少 scenario/manifest.json 或场景文件 | 场景尚未交付/迁移完成，启动该任务的 Pi 按 SCENARIO.md 补齐 |
| blocked，环境检查未通过 | 查看本轮 logs/check.log；按 ENVIRONMENT.md 恢复环境后重跑。Node 依赖可单独执行任务中的 `python3 prepare_deps.py`，浏览器和系统库还需按日志处理 |
| error，场景执行错误或超时 | 查看本轮 logs/run.log 和原始框架报告；定位服务、操作或脚本问题后重跑，不要仅为通过而放宽断言 |
| error，缺少有效 observations.json | 场景未按交付约定输出观察，修正采集/结果解析脚本；已有材料仍在本轮目录 |
| completed，但检查 failed 或证据有警告 | 场景执行完成，不代表 Bug 消失；查看具体异常、缺失证据或文件变化，由人或 AI 评审 |

没有 `--baseline` 或 `--prepare`。重跑只记录任务中**当前代码**的行为，不会自动修复或恢复旧版本；退出状态和完整产物说明见下文“独立运行场景”。

## 目录与上库范围

```text
make_bug/
├── README.md
├── .gitignore
├── src/                 # Python 实现与入口
├── resource/prompts/    # 可编辑的提示词模板
├── test/                # 自动测试
├── output/              # 生成任务、报告、截图（不提交）
├── cache/               # 仓库与依赖缓存（不提交）
└── tools/               # 下载的公共工具和浏览器（不提交）
```

上库内容是源码、资源、测试、说明和忽略规则。output、cache、tools、Python 字节码和本地虚拟环境都由本目录的 `.gitignore` 排除，不需要逐次手工挑选。空的运行目录不会提交，缺失时脚本会自动创建。

入口已移到 `src/make_bug.py`，工具预装入口为 `src/setup_tools.py`；旧的根目录脚本路径不再使用。现有任务的目录与配置不移动，共享缓存也保持原位置。资源随源码一起分发，不能只复制 src 目录。

## 准备环境

- Python 3.12+、Git、GitHub CLI（`gh`），完成 `gh auth login`。
- 启动 Agent 时需要已安装并配置模型的 pi；仅生成材料不需要 pi。
- pi 需要支持 `--no-context-files`、`--no-mcp` 等开关，参见 [pi 官方 CLI 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/cli.md)。旧版出现未知参数时请更新 pi，或审阅后自行调整生成的启动脚本。

从 Keel 根目录执行：

```bash
python3 script_tool/make_bug/src/make_bug.py https://github.com/actualbudget/actual/issues/7738
```

默认查找唯一一个关闭该 Issue 的已合并 PR，使用合并提交的第一父提交。自动选中的是**候选问题版本**，不保证 Bug 一定存在。特别是通过 rebase 合并的多提交 PR，第一父提交可能已经包含部分修复，应明确指定 `--ref`。

无法唯一找到修复 PR 时，命令停止并说明原因。可以明确指定问题版本或修复 PR：

```bash
python3 script_tool/make_bug/src/make_bug.py https://github.com/actualbudget/actual/issues/7738 \
  --ref 'ee82a16026^'

python3 script_tool/make_bug/src/make_bug.py https://github.com/actualbudget/actual/issues/7738 \
  --fix-pr https://github.com/actualbudget/actual/pull/7814
```

`--ref` 与 `--fix-pr` 不能同时使用。支持 GitHub 公共站点的 Issue，私有仓库依赖当前 gh 账号权限；不支持企业自建 GitHub 域名。

## 复用已下载代码

```bash
python3 script_tool/make_bug/src/make_bug.py https://github.com/actualbudget/actual/issues/7738 \
  --repo /path/to/actual --ref 'ee82a16026^' --output /tmp/actual-7738
```

`--repo` 必须是对应项目的本地 Git 仓库。脚本只读取指定提交，不切分支、不拉取、不读取未提交改动；本地缺少该提交时明确失败。没有 `--repo` 时，首次下载到本工具的 `cache/<owner>/<repo>.git`，之后复用缓存。所需提交已存在时不下载项目；缺少提交时只做一次增量 fetch，仍不存在则明确失败。克隆失败会清理临时目录，不留下半成品缓存。

缓存默认位于 `/home/elexcfe/work/keel/script_tool/make_bug/cache/`（实际随脚本目录移动），也可用 `--cache-dir /path/to/cache` 指定。缓存按项目复用，不按 Issue 重复下载；包含完整历史，仅供准备阶段使用，不交给 pi。

`--ref` 如果使用分支名，默认解析缓存中的分支状态；想获取最新状态时加 `--refresh-cache`，只做增量更新。`--repo` 仍严格只读，不能同时使用缓存参数。

每个任务仍会单独导出代码快照，避免任务互相污染，因此仍需要本地复制时间和磁盘空间。Issue 信息每次仍从 GitHub 获取；启动器会自动复用依赖下载缓存；符合条件时还会复制已安装的 node_modules，详见下节。

输出目录必须不存在；重复运行时用新的 `--output`。默认结果位于本工具的 `output/<owner>-<repo>-<issue>/`，已被 Git 忽略。

## 下载与安装进度

首次下载仓库和增量 fetch 会实时显示 Git 的接收对象、字节数、速度及各阶段百分比（以 Git 实际输出为准，阶段 100% 不代表整个流程完成）。项目依赖安装实时显示包管理器的解析、下载、链接或构建输出，同时保留日志；run_pi.sh 不再等安装结束才显示内容。

工具没有输出可靠总量时，不显示虚构的整体百分比：终端显示活动进度条、已用时间和等待新输出的时间。输出直接连接终端时约每秒刷新，重定向或管道环境（包括 run_pi.sh 的 tee 日志转发）每 5 秒输出一行状态，便于保存和查看。活动条表示进程尚未退出，不保证下载正在推进；长时间无新输出可以结合日志排查网络、构建或输入等待。缓存命中会直接说明复用，不制造下载进度。

已有任务使用 `src/update_task.py` 升级后生效，旧启动脚本会备份。此改动不触发新的项目下载或依赖安装。

## 自动复用依赖

新生成任务的 `run_pi.sh` 会先调用 `prepare_deps.py`，记录准备结果后启动 pi；失败会交给 pi 诊断，不再直接截断任务。单独生成材料不安装依赖；开始执行任务时才会按项目锁文件安装，也会执行包管理器正常的安装脚本。

默认缓存位于本工具的 `cache/dependencies/`，与任务目录分开。可在生成时用 `--deps-cache-dir /path/to/cache` 指定，支持与 `--repo` 一起使用。按项目声明的 `packageManager` 或唯一锁文件选择 npm、Yarn、pnpm；版本不匹配、多个锁文件无法判断或缺少锁文件时明确停止，不猜测版本或重写锁文件。非 Node 项目直接跳过。

| 项目情况 | 如何复用 |
| --- | --- |
| npm / Yarn 1，安装产物可迁移 | 首次安装成功后保存 node_modules；后续命中时复制到新任务，不再执行安装 |
| 安装脚本、原生模块、绝对/外部链接 | 复用下载缓存，仍执行安装，保留必要构建步骤 |
| Yarn 2+ / PnP、pnpm | 复用共享下载缓存或 store，由包管理器重新处理路径、链接和构建状态 |
| 同一个任务再次启动 | 依赖状态匹配时保留已有目录，不重复安装 |

跨任务复制已安装目录仍使用包含完整源码的保守缓存键。同任务复用则单独比较依赖清单、锁文件、安装配置、Node/包管理器及平台和相关环境；普通业务代码修复不再使依赖失效。本地 file/link 依赖内容也纳入检查；存在安装钩子且未声明输入时仍保守比较源码。确认安装脚本的输入后，可在 deps-config.json 中设置 dependency_inputs 相对 glob 列表（例如 ["scripts/install/**"]），不能为了绕过检查随意设为空。

各任务获得普通文件副本和指向本任务的相对链接，不使用共享可写目录或硬链接。只在安装成功、源码未变化且产物可迁移时发布完整缓存；失败安装不进入缓存。同一缓存键的准备使用文件锁，适用于 Linux、WSL 和 macOS。

逐项依赖清单在 `task/dependencies.json`。安装日志和批次状态分别在 `task/result/logs/dependencies.log` 与 `task/result/logs/dependencies.json`，缓存状态在 `task/deps-state.json`；这些状态文件不代替依赖清单。已有成功且匹配的依赖直接复用；同一失败安装可自动续装。未匹配或旧版未记录的依赖默认保留，确认是本次安装产物后可用 `python3 prepare_deps.py --retry` 重跑包管理器（npm ci 会重建 node_modules，Yarn 会继续处理链接和构建），不需要重新生成项目。要绕过自动准备可执行：

```bash
MAKE_BUG_SKIP_DEPS=1 bash /path/to/task/run_pi.sh
```

也可以在任务目录单独运行 `python3 prepare_deps.py`。复制整个任务到另一位置或换机器后，需核对 `deps-config.json` 中的缓存路径；不要把状态文件当作依赖完整性保证。此处复用的是 Node 项目依赖；浏览器二进制由下节的常用工具目录管理，系统工具和项目构建输出不会自动缓存。缓存命中不代表 Bug 已复现。

## 依赖失败由 pi 接手

启动器把预备结果写入 `result/logs/dependency-preparation.json`（ready / failed / skipped），完整预备输出保存到 `dependency-preparation.log`。失败时仍启动 pi，同时传入 [environment.md](resource/prompts/environment.md)；pi 必须先读日志、定位真正错误，再在允许的范围内修复运行环境。不能把启动成功或依赖警告当作 Bug 复现结果。

默认保持完整依赖安装。`--dependency-profile browser` 可用于明确的浏览器任务，只跳过 Electron 二进制下载，不关闭其他安装脚本。pi 也可在确认问题与启动路径都属于浏览器、且故障仅是 Electron 下载时执行 `python3 prepare_deps.py --profile browser --retry`；该选择写入任务配置和依赖状态，缓存键区分模式。桌面任务不应采用此模式；失败原因未知时不自动降级。

已有任务无需重新生成或手改配置，可升级运行脚本：

```bash
python3 script_tool/make_bug/src/update_task.py /absolute/path/to/task
```

升级会备份旧运行脚本到任务的 `runner-backups/`，更新启动器、依赖与工具准备脚本（含 setup_tools.py）、场景入口、ENVIRONMENT.md 和 SCENARIO.md；保留 TASK.md、BUG.md、项目代码、依赖、任务配置与缓存。旧 verify.sh、verify.py、VERIFICATION.md 备份后移除，不提供兼容入口。旧 repro/ 和 result/ 历史证据保留，但需要明确迁移为 scenario/ 新格式，更新器不会猜测转换测试。启动 Pi 仍用 `bash run_pi.sh`；只是升级脚本不会执行安装或启动 Pi。

## 常用工具与一次性工具

项目依赖、Agent 常用工具和一次性工具分开放，避免每次临时下载浏览器，也避免把 Agent 的辅助依赖写进目标项目。

| 类别 | 默认位置 | 使用方式 |
| --- | --- | --- |
| 项目锁定依赖 | `task/repo/node_modules/` | 由项目包管理器与依赖缓存准备，不被共享工具替换 |
| 跨任务常用工具 | `script_tool/make_bug/tools/shared/` | pi 自行判断并按版本追加安装，后续任务检查后复用 |
| 本次任务的一次性工具 | `task/tools/` | Node 包、Python 虚拟环境、临时数据各自隔离 |
| 报告与证据 | `task/result/` | 报告、截图、日志集中交付，不混入工具安装文件 |

共享目录可在生成任务时用 `--shared-tools-dir /path/to/tools` 指定；它与 `--deps-cache-dir` 分别管理工具和项目依赖。

```text
tools/shared/
├── bin/                          # 准备者放置的公共 CLI，不覆盖项目命令
├── node/playwright/<版本-平台-Node版本>/
├── browsers/<平台>/playwright/   # Playwright 自己按 browser revision 管理
├── browsers/<平台>/puppeteer/    # Puppeteer 浏览器缓存
└── downloads/                    # 工具包下载缓存

task/tools/
├── bin/
├── node/<工具名>/
├── python/<工具名>/              # 每个工具自己的虚拟环境
└── tmp/                          # 临时数据、浏览器用户数据
```

任务根目录的 `dependencies.json` 是给人和 Pi 读取的统一清单。Pi 启动提示要求先用 `python3 dependency_inventory.py show` 查看分组，报告引用此文件，不重复罗列依赖。

清单中的每条记录用 `category` 表示用途，根部 `summary` 给出各类数量。`show` 将记录按用途分组，默认不展开项目包；完整明细仍只保存在这一份 JSON 中。

| 类别 | 内容 |
| --- | --- |
| `project` | 项目构建、启动所需的包、Node 和包管理器 |
| `services` | 正常运行必需的数据库、Redis 或后端服务 |
| `verification` | Playwright、浏览器、测试工具，以及仅为验证准备的服务 |
| `unclassified` | 用途尚不明确，需 Pi 查看后分类的依赖 |

数据库按用途分类，不能一律归到验证依赖。自动盘点将普通项目包归为 project，明确的 Playwright、Jest、Vitest 等测试工具归为 verification；共享浏览器安装器准备的工具也归为 verification。不根据 devDependencies 推断用途，也不自动发现或验证数据库服务。有限的名称规则不能识别所有用途，Pi 应检查待分类项及当前场景需要的工具，并可明确调整。内部 `group` 只用于盘点来源及失效处理，不代表用途。

```bash
# 默认看验证依赖、运行服务、待分类项；项目包只显示摘要
python3 dependency_inventory.py show
# 需要时查看某类明细；all 展示所有类别
python3 dependency_inventory.py show --category project
# 更正已有记录的用途，不修改版本和安装来源
python3 dependency_inventory.py classify \
  --name playwright --path "$PWD/repo/node_modules/playwright" \
  --category verification
```

手动 `record` 必须提供 `--category`；明确指定的用途在重新盘点、安装或更新版本后保留。服务可以用实际配置文件或数据目录作为 path，安装方式记实际部署命令，不把凭据写入清单；空的 services 只表示没有登记，不能证明项目无需服务。

每项至少包含以下字段（示例）：

```json
{
  "category": "verification",
  "name": "playwright",
  "version": "1.55.0",
  "install_method": "npm install --save-exact playwright@1.55.0",
  "installer": "pi",
  "path": "/path/to/task/tools/node/playwright/node_modules/playwright",
  "status": "installed"
}
```

`installer` 记录执行安装或缓存复制的主体：`prepare_deps.py`、`setup_tools.py`、`pi`；历史来源无法确认时为 `unknown`。Pi 启动后的脚本通过 `MAKE_BUG_INSTALLER=pi` 记录调用者。该字段是过程记录，不是身份认证。`installed` 只表示安装步骤完成，`detected` 表示仅发现文件，`unverified` 表示需重新检查，`failed` 表示手工登记的失败。实际版本无法读取时为 null；浏览器另保留安装 revision。安装完成不保证运行正常。

在任务目录运行：

```bash
# 只盘点已有 Node 包，不重新安装；保留已知安装者
python3 dependency_inventory.py scan

# Pi 自行安装其他工具后，按实际结果登记（此命令本身不会安装）
python3 dependency_inventory.py record --category verification \
  --name pytest --version 8.4.2 \
  --method 'python -m pip install pytest==8.4.2' --installer pi \
  --path "$PWD/tools/python/tests/lib/python3.12/site-packages/pytest"
```

`prepare_deps.py` 自动记录 Node 实际安装包（含 workspace、嵌套包、pnpm 和可读取的 Yarn PnP）、Node 和包管理器版本。`setup_tools.py` 自动记录 Playwright 及其 Chromium；直接运行源码预装时先存到共享目录的 `dependencies.json`，任务启动时导入到自己的清单。安装失败不会标为就绪；盘点失败可查看清单的 `preparations.project.warnings`。其他语言、系统库和 Pi 自行安装的工具由 Pi 逐项登记，不声称自动捕获所有 shell 安装行为。清单更新加锁并原子写入，同名不同安装路径分别保留。

新任务自带清单和记录工具；旧任务先按上文运行 `update_task.py`，再执行盘点命令即可。升级保留已有清单和自定义 TASK.md；ENVIRONMENT.md 会更新为新的统一记录要求。清单在已忽略的任务输出目录内，不应提交到 Keel 源码仓库。

启动器先运行 `prepare_tools.py`，创建目录，将共享工具登记到 `dependencies.json`，另将目录和环境变量写入 `result/logs/tools.json`，然后准备项目依赖、启动 pi。它设置 `MAKE_BUG_SHARED_TOOLS`、`MAKE_BUG_TASK_TOOLS`、`PLAYWRIGHT_BROWSERS_PATH`、`PUPPETEER_CACHE_DIR`、`npm_config_cache`、`PIP_CACHE_DIR`，共享工具和项目自己的浏览器安装器都使用这些路径。Playwright 缓存关闭自动旧版本回收，避免任务之间清掉彼此需要的 revision；长期不用的版本由准备者检查后清理。目录存在不等于工具已安装或能启动。

pi 根据 Issue、项目依赖与实际运行错误自行确定所需工具、精确版本和缓存价值，用户无需预先提供清单。通用工具和高成本下载优先跨 Issue 复用；任务专用脚本、不能安全迁移的安装产物保持独立，但仍可共享下载缓存。具体约定见 [环境要求](resource/prompts/environment.md)。脚本安装的依赖自动登记到 `dependencies.json`；Pi 自行补装的依赖也必须更新同一文件。

每个新任务都包含 `setup_tools.py`。pi 可在任务目录运行 `python3 setup_tools.py --playwright-version <精确版本>`；默认读取任务的 tools-config.json，不要求用户选择版本或执行预装。此入口只负责 Playwright + Chromium，其他工具由 pi 根据上述约定安装、验证和记录，并没有通用工具自动安装器。

也可以手工预装常用 Playwright + Chromium：

```bash
# 1.55.0 仅展示精确版本写法，实际按项目需求选择版本
python3 script_tool/make_bug/src/setup_tools.py --playwright-version 1.55.0

# 自定义共享目录时，与生成任务的参数保持一致
python3 script_tool/make_bug/src/setup_tools.py   --shared-tools-dir /path/to/tools --playwright-version 1.55.0
```

同版本重复执行会复用工具包，并调用浏览器安装器检查对应 revision，不重复下载已有文件。多个版本并存，不覆盖系统安装。脚本不自动安装操作系统库、不使用 sudo；安装记录只说明安装步骤成功，正式使用前仍需检查浏览器启动。当前入口预装的是 **Chromium**，不是系统 Google Chrome；如果 Issue 指定 Chrome 品牌或特定版本，需要另行匹配，不能把它们视作同一个环境。

pi 应优先使用项目自身锁定的 Playwright。使用独立共享工具时，从 `tools.json` 找到包目录，以绝对路径调用 `node <包目录>/node_modules/playwright/cli.js`；脚本可使用 `createRequire('<包目录>/package.json')('playwright')` 加载。不会设置全局 NODE_PATH，避免影响项目模块解析。不要运行会临时拉取最新包的 `npx ...@latest`。

临时 Node 工具用 `npm install --prefix "$MAKE_BUG_TASK_TOOLS/node/<工具名>" --save-exact <包名>@<精确版本>`；Python 工具先在 `tools/python/<工具名>` 创建 venv。复现脚本和证据放在任务中，浏览器 profile 不共享。缺少常用工具时由 pi 自行决定是否共享。共享安装必须隔离版本、平台和运行时，加锁并在成功检查后发布；无法安全迁移时只共享下载缓存，不搬运整个安装目录。pi 不覆盖、升级或删除已有公共工具。

浏览器缓存机制依据：[Playwright 浏览器管理](https://playwright.dev/docs/browsers)、[Puppeteer 缓存配置](https://pptr.dev/guides/configuration)。Puppeteer 只接入共享缓存路径，没有单独的预装入口；项目配置若覆盖路径，应按实际配置核对。

## 生成内容

```text
actualbudget-actual-7738/
├── preparation.json     # 给任务准备者：来源、准确提交、选择依据及限制
└── task/                # 交给复现 Agent 的目录
    ├── BUG.md           # 标题、Issue 正文原文，不包含评论
    ├── TASK.md          # 复现要求、证据要求、禁止网络搜索
    ├── ENVIRONMENT.md   # 环境检查与自动恢复要求
    ├── run_pi.sh        # 准备依赖后启动 pi
    ├── run_scenario.sh # 独立运行场景，不调用 Pi、不安装依赖
    ├── run_scenario.py # 执行器
    ├── evidence.py     # 快照、证据索引与可读报告
    ├── SCENARIO.md     # 场景与证据交付要求
    ├── scenario/       # 场景清单、操作脚本及观察采集
    ├── dependencies.json # 统一依赖清单：名称、实际版本、安装方式、安装者、路径、状态
    ├── dependency_inventory.py # 盘点已有 Node 依赖或登记 Pi 补装工具
    ├── prepare_tools.py # 工具目录与浏览器缓存环境
    ├── setup_tools.py   # pi 可直接调用的共享 Playwright 安装入口
    ├── tools-config.json # 常用工具根目录
    ├── tools/           # 本任务一次性工具
    ├── progress.py      # 实时输出与无输出时的活动进度
    ├── prepare_deps.py  # 依赖缓存、安装与恢复
    ├── deps-config.json # 共享依赖缓存位置
    ├── repo/            # 问题代码；Git 只有一个本地初始快照提交
    └── result/          # 最终交付，可整体复制
        ├── REPRO.md     # pi 执行后生成的复现报告
        ├── runs/        # 每轮 record.json、REPORT.md、代码快照与原始证据
        ├── images/      # 探索阶段截图，按需生成
        └── logs/        # 执行日志，按需生成
```

`BUG.md` 保留原文，不翻译、不由模型改写。正文可能包含修复线索或已被作者编辑过，交给 Agent 前请审阅；没有自动过滤答案的保证。附件和外链不自动下载。准备记录不传入 pi，但目录分开不是文件访问沙箱。

快照使用 `git archive`：遵循仓库的 `export-ignore`，不包含子模块内容；Git LFS 文件可能是指针。遇到这些情况，需要准备者补齐材料。生成阶段不会执行来自目标仓库的安装命令；启动 pi 时会自动准备依赖。

## 启动 pi

检查材料后执行输出中的启动命令。启动器先自动复用或安装项目依赖，pi 可继续联网准备浏览器等环境：

```bash
bash script_tool/make_bug/output/actualbudget-actual-7738/task/run_pi.sh

# 非交互执行，可指定自己的模型
bash script_tool/make_bug/output/actualbudget-actual-7738/task/run_pi.sh \
  --print --provider anthropic --model '<你配置的模型>'

# pi 不在 PATH 中时
PI_BIN=/absolute/path/to/pi bash /tmp/actual-7738/task/run_pi.sh
```

生成命令加 `--run` 会立即启动 pi，先运行依赖缓存准备，再由 pi 复现；依赖预备失败时把退出码和日志交给 pi；pi 先处理环境，再复现，确实无法继续才输出阻塞报告。脚本原样返回 pi 的退出码。参数透传供准备者选择模型和运行模式；不要用额外参数重新启用网络搜索工具或恢复包含答案的旧会话。

启动脚本禁用自动扩展、技能、提示模板、上下文文件发现及内置 MCP，只启用文件和 bash 等基本工具，避免额外搜索入口；默认启动新会话，监测入口显式指定每轮独立的会话目录，不传入 `--no-session`、`--continue` 或 `--resume`，并把任务规则加入系统提示。不使用 `--offline`，允许正常网络连接。

所有运行默认监测并保存 Pi 会话，位置为任务父目录的 `pi-runs/` 或 `--runs-dir` 指定目录；`result/logs/` 仍是任务日志。single/split、隔离运行、查看统计及限制见下文“Pi 运行监测”。已有任务运行 `update_task.py` 升级后生效，过去未保存的会话不能补回。

网络约定是：**允许联网准备和运行环境，禁止网络搜索和查阅修复答案**。pi 可以下载依赖、浏览器、构建工具和 BUG.md 直接引用的复现附件，连接必要的测试服务；依赖按锁文件或指定版本获取。不得通过搜索引擎、curl、浏览器等变相搜索，不查修复 PR、Issue 评论、补丁或其他版本源码，也不拉取或切换目标仓库版本。这是工具配置加提示约束，不是网络沙箱，无法从技术上阻止 bash 访问搜索网站。

复现任务只要求新增最小测试或脚本，不修改业务实现。最终交付统一放在 `task/result/`：报告为 `REPRO.md`、图片为 `images/`、日志为 `logs/`；报告用相对链接引用证据，复制整个 result 文件夹即可一起查看。生成阶段只创建这些目录，报告和真实图片由 pi 执行后产生。测试脚本留在 repo/，报告记录其路径及重跑命令；result 不是包含代码和依赖的独立运行包。生成成功和 pi 正常退出均不等于 Bug 已复现。

## 给 pi 的提示词

公共提示词在 [resource/prompts/reproduce.md](resource/prompts/reproduce.md)，可以直接编辑，不需要修改 Python。生成器每次读取该文件，原样复制文字并追加准确提交号，写入 `task/TASK.md`。模板不使用变量替换，花括号、命令示例等可正常书写。模板缺失或为空时，在联网前报错，不悄悄使用旧版本。

启动时通过 `--append-system-prompt` 把任务自己的 `TASK.md` 加入系统提示，`BUG.md` 作为文件附件传入；同时传入 SCENARIO.md、ENVIRONMENT.md 和 MONITOR.md，初始指令要求先核查预备状态、必要时恢复环境，然后按 TASK.md 复现。

提示词要求先明确判定条件，再检查环境、执行复现、保留最小测试并写报告。测试断言正常行为，避免把任意报错当作目标 Bug；允许联网安装依赖及必要的本地构建和配置，要求说明所有调整。成本允许时独立运行三次并重置测试数据，不能稳定复现时如实记录偶发性。缺少的材料确实阻止执行或判断时才报告阻塞，不因为某个非必要附件缺失而停下。

报告包括结论、判定依据、代码与环境、重跑方法、实际记录和文件变化；报告、截图和日志统一放在 `task/result/`。界面问题优先保留实际截图，非界面问题不要求凑图片；提示词要求检查实际环境，不能把缓存记录当作可运行的保证。规则要求保留用户已有改动、不修改业务代码或已有测试断言、不提交修复补丁。

修改公共模板只影响之后生成的任务。要调整某个已有任务，直接编辑它的 `task/TASK.md`，保留末尾的“代码基线”，下一次启动即读取新内容，不必重新下载代码或生成目录。已经运行中的 pi 不会自动刷新提示词。

更早生成的任务若缺少依赖或工具准备脚本，若已有 deps-config.json 和 tools-config.json，可用 update_task.py 升级运行脚本；更旧且缺少配置的任务需重新生成。修改提示词不会自动升级脚本。

## 验证工具

```bash
python3 -m unittest discover -s script_tool/make_bug/test -p 'test_*.py' -v
```

测试使用临时 Git 仓库和模拟 GitHub/pi；依赖测试使用真实 Node/npm 和本地 fixture，强制 offline，不访问注册表或调用付费模型。缺少 Node/npm 时依赖测试会标为跳过。

## 独立运行场景

按 [场景交付要求](resource/prompts/scenario.md) 生成 scenario/manifest.json、env.sh、check.sh、run.sh 及观察采集脚本。运行入口不需要失败基线；有无异常都能记录。旧 verify 流程已经删除，没有 --baseline、--prepare 或兼容转换。

```bash
bash /path/to/task/run_scenario.sh
bash /path/to/task/run_scenario.sh --repeat 3 --timeout 300
```

环境需要准备时单独运行任务的 prepare_deps.py 或按 ENVIRONMENT.md 恢复，再执行场景。check.sh 负责环境检查；run.sh 负责操作、采集和解析自动检查结果。正常完成时 run.sh 返回 0，即使断言失败；工具错误必须返回非零，不可将任何测试失败都吞掉。详细 JSON 格式以场景交付要求为准。

每轮在 result/runs/run-* 交付：

- REPORT.md：操作条件、观察事实、自动检查、原始证据链接、代码状态和限制。
- record.json：相同信息的结构化记录，review 初始为 pending；执行器不代替人或 AI 判断。
- observations.json：场景采集的原始观察、检查与证据引用。
- logs/、images/、videos/：原始运行材料，每个文件记录内容摘要；缺失引用或空文件显示警告。
- scenario.tar.gz：本轮声明的场景文件快照，版本由文件内容和模式摘要标识。
- code/source.tar.gz、index.json：Git 跟踪文件及未忽略的新增文件的实际内容、权限、符号链接和缺失记录。归档不是 Git 仓库，不包含被忽略的依赖或外部数据；恢复到空目录，不覆盖当前项目。外部符号链接只记录链接本身。
- code/working-tree.diff：辅助查看本轮相对 HEAD 的已跟踪改动；它不是两轮之间的 diff。新增文件完整内容在源码归档中。

执行状态为 completed / blocked / error / interrupted，分别返回 0 / 2 / 1 / 130。**退出 0 只表示执行并交付了有效结构的记录，不代表 Bug 不存在，也不保证视频或检查内容正确。** 检查结果 passed / failed / inconclusive 单独保存；证据缺失和执行期间文件变化单独列为警告。重复执行遇到非 completed 停止，不对多轮自动宣称稳定。

修改场景后可以直接重跑，新轮次保留新版本，旧记录不覆盖。Pi 在 result/REPRO.md 交付复现报告；人或独立验收 AI 将评审另存于监测目录 review.json 或 Issue，引用运行 ID、视频/事件时间点和文件，不覆盖 Pi 报告及原始记录。参考轮次由评审选择。默认复现模式不判断修复；修复模式的参考对照见下一节。没有自动语义验收或 AI 视频判断器。

## 同一会话修复闭环

这是通用工具能力，已用合成项目和假 Pi 做离线验证；尚未证明真实 Pi 完成修复。当前不启动新的付费实验。具体场景、数据、测试和 FIX.md 必须由 Pi 产出，不能由验收者补写。

新场景使用 schema 2，在 manifest.required_checks 中声明稳定 id、kind（target/regression）和 expected（正常行为预期），每轮 checks 引用相同 ID。缺项、重复、未执行或无有效证据均不能准入。旧 schema 1 仍可运行，但须由 Pi 更新并重跑，经独立审阅后才能进入修复。必要回归属于已确认场景的一部分；其在参考版本应通过，目标检查至少一项失败。第一版不自动豁免已有回归失败。

外层审核者确认原问题覆盖、断言、数据条件和视频/日志后，显式选定运行；以下是后续获准真实运行时的操作示例，不能仅凭脚本接受就代替审阅：

```bash
# 已有任务可先用 update_task.py 更新通用运行脚本；不会转换 Pi 的场景或报告。
python3 script_tool/make_bug/src/update_task.py /path/to/task

# 由验收者记录准入，输出必须位于任务外且不能覆盖已有文件。
python3 /path/to/task/repair.py --task /path/to/task \
  --run '<已审阅的 run-ID>' --reviewer '<验收者>' \
  --evidence '<审阅记录或证据位置与结论依据>' \
  --output /path/to/review/reference.json

# 只启动一个修复会话；该命令会调用模型，须在获准真实运行后执行。
bash /path/to/task/run_pi.sh --purpose repair \
  --reference /path/to/review/reference.json --timeout 3600 --cost-limit '<授权预算>' \
  -- --print --provider '<provider>' --model '<model>'
```

修复只支持 single 和已有已验收任务，不再次 --isolate，避免复制时丢失参考记录或未提交的复现脚本。启动前核对源码与参考一致，环境准备后再次核对。使用 REPAIR.md、ENVIRONMENT.md、MONITOR.md，不加载禁止修改业务代码的 TASK.md/SCENARIO.md。

Pi 可先做局部测试，再用 `bash run_scenario.sh` 正式验证。执行器通过 MAKE_BUG_REPAIR_REFERENCE 自动取得本次参考，也可在独立复验时显式使用 `--reference /path/to/reference.json`。一次调用只运行一次场景，结果立即回到同一 Pi 会话，失败后可在总预算内继续修改。不会退出 Pi 再启动报告阶段，也不会自动追加会话。

修复验证的 record.json.repair.status 分为 checks_passed、checks_failed、blocked、invalid；stdout 提供每项检查、运行编号、错误及完整记录路径。目标/回归仍失败返回 3，完整性或参考错误返回非零（执行错误为 1，完成后对照无效为 4），环境阻塞为 2。checks_passed 返回 0，但仍不等于独立验收通过。默认复现模式保留原有退出语义。`--timeout` 现在约束整轮场景，check/run 共用时限；在修复会话内也受父任务剩余时间及报告预留时间约束。

Pi 应逐轮更新 result/FIX.md，保留 REPRO.md，并引用准确的前后运行编号、证据和必要回归结果。执行器保存 FIX.partial.md。结束后监测目录中的 repair-result.json 核对本次最后一轮正式运行、最终代码、报告及引用；只有完整一致才标记 awaiting_review，否则 incomplete。Pi 进程退出码与修复交付状态分开，调用者必须读取 repair-result.json，不能凭 run_pi.sh 退出 0 宣称交付成功。

awaiting_review 仍须独立检查原因、覆盖、画面、回归充分性和人工介入；不会自动写 accepted。瞬间异常可以视频为主，Pi 要说明时间段与观察点。Actual 的最终真实验收仍需必要独立重跑，日常调试不强制重复执行。

参考记录绑定原始记录、场景及源码归档摘要，交付版本绑定最后验证的源码摘要。源码归档不含被忽略依赖、数据库或外部文件，配置/依赖变化仍需审查；相同用户的任意 shell 可以访问这些材料，因此摘要校验不是安全沙箱。修复耗时/费用单独记录，历史复现成本不会自动消失，也不把修复阶段预算宣称为全流程成本。统计明细包含 purpose，修复任务不计入旧 single/split 复现率或 A/B 配对。

## Pi 运行监测（默认开启）

`run_pi.sh` 现在统一进入 `run_pi.py`。single 和 split 都自动保存记录，不再只依靠 Pi 默认会话目录。当前执行器支持 Linux/WSL；其他平台的子进程回收未实现。它不判断 Bug 是否成立。

```bash
# 日常单会话；有交互终端时保留 Pi 交互界面
bash /path/to/task/run_pi.sh

# 非交互单会话，显式选择同一提供商/模型
bash /path/to/task/run_pi.sh --timeout 3600 \
  -- --print --provider '<provider>' --model '<model>'

# 两个新会话：只准备环境 → 按文件交接后复现
bash /path/to/task/run_pi.sh --workflow split \
  --timeout 3600 --prepare-timeout 900 \
  -- --provider '<provider>' --model '<model>'

# 从干净模板新建隔离副本；目标目录必须不存在
bash /path/to/template/task/run_pi.sh \
  --workflow single --isolate /tmp/keel-experiment/pair-01-A \
  --runs-dir /tmp/keel-experiment/records --pair-id pair-01 \
  -- --print --provider '<provider>' --model '<model>'

# B 仍从同一干净模板开始，不能以 A 的运行目录作输入
bash /path/to/template/task/run_pi.sh \
  --workflow split --isolate /tmp/keel-experiment/pair-01-B \
  --runs-dir /tmp/keel-experiment/records --pair-id pair-01 \
  -- --provider '<provider>' --model '<model>'

# 重算某轮统计；不会启动 Pi
python3 script_tool/make_bug/src/session_stats.py analyze /path/to/pi-runs/run-id

# 汇总目录下的历史运行；失败和未验收尝试也保留
python3 script_tool/make_bug/src/session_stats.py summarize /path/to/pi-runs
```

默认记录在任务父目录的 `pi-runs/<时间-随机ID>/`；隔离运行默认放在隔离目标的父目录 `pi-runs/`。可用 `--runs-dir` 指定，必须位于任务及隔离副本之外。同一任务目录不允许两个 Pi 同时写入，需要并行时创建不同隔离副本。每次新建记录目录，single 一个独立 session-dir，split 两个，不能传 `--no-session`、恢复旧会话、覆盖 session-dir/系统提示或输出模式的参数。模型、提供商、推理档位等 Pi 参数放在 `--` 后。API 密钥走 Pi 的配置，不放命令行。

每轮记录包括：

| 文件/目录 | 用途 |
| --- | --- |
| `run.json` | 运行方式、原/实际任务路径、配对标识、Pi 版本探测、预算、阶段命令/状态、总耗时、退出原因、监测完整性 |
| `current.json`、`live-metrics.json` | 当前阶段和约每两秒刷新的已采集用量；不是可靠的模型调用中途计费器 |
| `sessions/` | Pi 的原始 JSONL 会话，是统计用量的唯一来源 |
| `events/` | 阶段过程日志及起止记录；非交互 Pi 输出为 JSON 事件流，交互模式直接使用终端，不额外录制屏幕 |
| `prompts/` | 每阶段实际提示文件、BUG、启动指令及摘要 |
| `environment.json` | 工具路径和端口等白名单环境变量，不导出全部环境 |
| `snapshots/` | 初始、交接、结束的源码归档/摘要和依赖状态；不包含依赖二进制或数据库 |
| `handoff/` | B1 交接文件副本及结构检查结果 |
| `metrics.json`、`tools.json` | 总计及分阶段用量、估算费用、调用数、压缩数、工具调用与结果关联 |
| `review.json`、`REPORT.md` | 初始为待验收；运行完成不自动变成复现成功 |

所有模型发起的 bash 工具调用由显式加载的 `command_timeout.mjs` 约束：未指定时默认 120 秒，显式申请最多 600 秒；超时由 Pi 终止命令并向模型返回错误，后续可继续诊断。不能用 `--extension/-e` 注入其他扩展覆盖约束。每轮保存扩展副本及摘要。这是运行保护，不是阻止 Agent 绕过的安全沙箱，D 状态等不可中断内核等待仍不能保证立即结束。

默认总时限 1 小时（3600 秒），split 环境阶段最多 15 分钟，提前结束的余额可用于复现。隔离复制、快照、前置脚本和交接计入总耗时；子进程执行到限后终止并保存记录；文件复制、快照和最终归档没有硬中断，可能使实际总耗时超过时限，超出会如实计入，不会再给下一阶段新预算。`--cost-limit <金额>` 按 Pi 已记录的估算费用（通常 USD）设置软上限，合计两个会话；记录有延迟或缺失时不能保证费用硬上限，应同时设置提供商限额。无论失败、超时还是中断都保留已经发生的费用。

执行器结束每阶段时清理它启动的子进程。Linux/WSL 使用子进程回收机制处理另开进程组的 shell/服务；不按服务名或端口杀其他任务。`MAKE_BUG_PORT` 是建议端口，不是已预留端口，Agent 必须确认并记录实际值。通过外部服务管理器、容器守护进程或远程服务创建的资源仍须场景自行清理，不声称能够自动接管。

隔离模式仅复制干净仓库的已跟踪代码以及任务规则/脚本，重写项目缓存和共享工具路径；不带入历史报告、场景、会话或已安装依赖。独立的下载缓存、浏览器目录、临时目录及 XDG 缓存避免正常安装互相污染。可加 `--cache-seed /path/to/downloads`，两组各自复制同一份下载缓存种子（如准备脚本缓存目录下的 downloads），不是共享可写目录。为避免路径损坏，绝对/外部符号链接和 Git 共享对象库被拒绝。

当前隔离入口实现的是从源码重装的条件，**不是“已安装环境就绪”的自动克隆**。要跑设计中的热环境组，应由实验准备者在两份独立副本按同一脚本准备相同环境，核查摘要后在各副本内用 single/split 启动；不要直接复制带绝对路径的 node_modules 或虚拟环境。任务源码内、用户配置或工具自行指定的外部位置仍可被访问；目录隔离不是网络/文件系统沙箱。严格防止跨组读取需要容器或其他沙箱，尚未实现。

B1 接收 PREPARE.md、ENVIRONMENT.md 和 MONITOR.md；B2 接收 TASK.md、SCENARIO.md、ENVIRONMENT.md 和 MONITOR.md，并读取环境交接。B1 退出后停止服务，B2 自行重启；依赖保留。结构检查不执行未审核的交接命令，也不把 ready 当成功。B1 超时、失败或交接缺失时，只要总预算仍有余额且未达到费用上限，B2 仍可尝试补齐；这些情况会保留在阶段记录中。

统计按 session id + entry id 去重，事件流不另计费，包含会话中的压缩、分支摘要和独立 usage。缺失/截断/不支持的记录会报告警告；不完整阶段使总费用未知，同时保留 `known_totals` 作为已知下限。当前解析器按 Pi 会话 v3 的字段实现，已用本机 Pi 1.0.4 / deepseek-v4-pro 的最小真实调用验证：回复 OK，输入 45、输出 1、总计 46 token，费用估算 0.00006336 美元，统计与原始 usage 一致。这只验证了最小调用和会话解析；完整复现、交互终端及中断保存仍需真实验收，也不代表所有 Pi 版本均已验证。同一 pair-id 恰有 single/split 各一次时，汇总保留配对成本差及验收状态；重复或缺少一组时标记不完整。工具调用的 env/reproduce 等语义分类默认 unknown，不凭命令关键词推断重复安装或环境返工。

独立验收后，可填写 `review.json` 的 `outcome`（reproduced / not_reproduced / blocked）、`reviewer` 和非空 `evidence` 引用；汇总只有在 reproduced 且具备判断者、证据引用时才计为已验收复现，程序不替代证据审查。未验收、失败和费用未知均单独保留，不自动宣布 A/B 胜者。

原始会话和日志可能含敏感信息，不会自动脱敏；默认运行目录权限为仅当前用户可进入。分享前另做脱敏副本，原始结果不提交 Git。本工具的 `output/`、`experiments/`、`pi-runs/` 均已忽略；自定义的其他输出位置需要自行配置忽略规则。


命令超时策略可离线测试（不调用模型）。设置安装目录还会使用真实 Pi 的扩展加载器和 bash 工具验证超时、子进程退出及下一条命令恢复：

```bash
node script_tool/make_bug/test/test_command_timeout.mjs
PI_PACKAGE_PATH=/home/elexcfe/.local/lib/node-v24/lib/node_modules/@earendil-works/pi-coding-agent \
  node script_tool/make_bug/test/test_command_timeout.mjs
```

### 收尾与进程清理

监测入口默认预留最多 600 秒收尾（不超过进入该阶段时剩余时间的六分之一），可用 `--wrap-up` 设置正数。时间来自启动器；Pi 的 bash 时限被压到收尾边界，进入收尾后禁止新 bash，仍可用 read/edit/write 保存报告或环境交接。该限制不理解命令语义，因此收尾期间也不能用 bash 整理文件，应提前逐轮保存材料。它是工具边界约束，不是 OS 安全沙箱；未验证模型一定按要求完成报告。

`delivery.json` / `DELIVERY.md` 持续列出必需文件、失效本地链接、未更新旧报告和已有场景记录；`REPRO.partial.md` 保存最近读取的报告文本，不创建虚假的成功结论。最终 `run.json` 单列 delivery，review 仍为 pending。已有场景记录可能包含历史轮次，需按 run_id 核查。费用上限、外部强杀与模型中断可能提前结束收尾，已有检查点保留；SIGKILL 或宿主机故障无法保证执行最后清理。

Pi 与场景执行器在 Linux/WSL 使用同一回收机制：启动前保护已有子进程，启动后回收本阶段进程树及被收养的孤儿，使用 PID 与启动时间复核身份。正常退出、失败、超时和 SIGTERM 均记录清理结果；场景日志旁的 `*.cleanup.json` 与最终 record.json 可核查。每个运行器串行执行阶段，不支持同一 Python 进程并发运行多个清理范围。外部已有服务不按名称或端口查杀。

### 人工诊断材料

2026-10-19 的 Actual 场景、固定账本及观察逻辑由开发 Keel 的 Codex 人工修订，不是 Pi 独立产出。它们已移出正式资源，仅保留在被忽略的 `output/diagnostics/manual-actual-8351-20261019/scenario/`；同目录 `PROVENANCE.json` 记录文件摘要、人工介入和关联任务路径。原任务与独立副本内的人工场景、报告及运行证据也仅作诊断保留，不改写原始材料，不计入 Pi 成功率，不回填历史 A/B 成功。

这些运行只能说明人工场景可以捕捉异常、公共执行器可以保存材料，不能证明 Pi 的场景生成或证据交付能力已提升。后续应改进通用要求与验收，再由 Pi 在干净任务中重新产出；本轮不启动新的付费实验。
