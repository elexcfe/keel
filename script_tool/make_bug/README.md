# 为 pi 准备 Bug 复现任务

输入 GitHub Issue 链接，生成问题描述、Agent 要求、指定版本的代码，以及 pi 启动脚本。不改动现有仓库，不覆盖已生成任务。

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

首次下载仓库和增量 fetch 会实时显示 Git 的接收对象、字节数、速度及各阶段百分比（以 Git 实际输出为准，阶段 100% 不代表整个流程完成）。项目依赖安装实时显示包管理器的解析、下载、链接或构建输出，同时保留日志；run_pi.sh 不再等安装结束才显示内容，verify.sh --prepare 也会实时显示。

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

日志和复用状态在 `task/result/logs/dependencies.log`（发生安装时）与 `dependencies.json`，状态文件为 `task/deps-state.json`。已有成功且匹配的依赖直接复用；同一失败安装可自动续装。未匹配或旧版未记录的依赖默认保留，确认是本次安装产物后可用 `python3 prepare_deps.py --retry` 重跑包管理器（npm ci 会重建 node_modules，Yarn 会继续处理链接和构建），不需要重新生成项目。要绕过自动准备可执行：

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

升级会备份旧运行脚本到任务的 `runner-backups/`，更新启动器、依赖与工具准备脚本（含 setup_tools.py）、验证入口、ENVIRONMENT.md 和 VERIFICATION.md；保留 TASK.md、BUG.md、项目代码、依赖、任务配置与缓存。之后仍运行原来的 `bash run_pi.sh`。只是升级脚本不会自动执行安装或启动 pi。

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

启动器先运行 `prepare_tools.py`，创建目录并输出工具清单 `result/logs/tools.json`，然后准备项目依赖、启动 pi。它设置 `MAKE_BUG_SHARED_TOOLS`、`MAKE_BUG_TASK_TOOLS`、`PLAYWRIGHT_BROWSERS_PATH`、`PUPPETEER_CACHE_DIR`、`npm_config_cache`、`PIP_CACHE_DIR`，共享工具和项目自己的浏览器安装器都使用这些路径。Playwright 缓存关闭自动旧版本回收，避免任务之间清掉彼此需要的 revision；长期不用的版本由准备者检查后清理。目录存在不等于工具已安装或能启动。

pi 根据 Issue、项目依赖与实际运行错误自行确定所需工具、精确版本和缓存价值，用户无需预先提供清单。通用工具和高成本下载优先跨 Issue 复用；任务专用脚本、不能安全迁移的安装产物保持独立，但仍可共享下载缓存。具体约定见 [环境要求](resource/prompts/environment.md)。决策记录写入任务的 `result/logs/tool-decisions.json`，这是 pi 执行时的交付要求，不是启动脚本自动生成的结果。

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
    ├── verify.sh        # 修复后独立验证，不调用 pi
    ├── verify.py        # 验证执行器
    ├── VERIFICATION.md  # 复现包交付接口
    ├── repro/           # pi 生成固定测试入口、环境配置、基线记录
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
        ├── runs/        # 每次基线/验证的独立日志、截图和 summary.json
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

启动脚本禁用自动扩展、技能、提示模板、上下文文件发现及内置 MCP，只启用文件和 bash 等基本工具，避免额外搜索入口；默认启动新会话并保留 pi 自身的会话保存行为，不传入 `--no-session`、`--continue` 或 `--resume`，并把 TASK.md 加入系统提示。不使用 `--offline`，允许正常网络连接。

开发阶段保留 pi 会话，便于回看对话和工具调用、分析失败原因。启动器不覆盖 pi 的会话存储目录，由实际安装版本与用户配置决定；`result/logs/` 仍是环境和测试日志，不是 pi 会话目录。恢复保存从下次启动生效，之前用 `--no-session` 运行的会话不会因此补回。已有任务使用上文 `update_task.py` 升级启动器即可。

网络约定是：**允许联网准备和运行环境，禁止网络搜索和查阅修复答案**。pi 可以下载依赖、浏览器、构建工具和 BUG.md 直接引用的复现附件，连接必要的测试服务；依赖按锁文件或指定版本获取。不得通过搜索引擎、curl、浏览器等变相搜索，不查修复 PR、Issue 评论、补丁或其他版本源码，也不拉取或切换目标仓库版本。这是工具配置加提示约束，不是网络沙箱，无法从技术上阻止 bash 访问搜索网站。

复现任务只要求新增最小测试或脚本，不修改业务实现。最终交付统一放在 `task/result/`：报告为 `REPRO.md`、图片为 `images/`、日志为 `logs/`；报告用相对链接引用证据，复制整个 result 文件夹即可一起查看。生成阶段只创建这些目录，报告和真实图片由 pi 执行后产生。测试脚本留在 repo/，报告记录其路径及重跑命令；result 不是包含代码和依赖的独立运行包。生成成功和 pi 正常退出均不等于 Bug 已复现。

## 给 pi 的提示词

公共提示词在 [resource/prompts/reproduce.md](resource/prompts/reproduce.md)，可以直接编辑，不需要修改 Python。生成器每次读取该文件，原样复制文字并追加准确提交号，写入 `task/TASK.md`。模板不使用变量替换，花括号、命令示例等可正常书写。模板缺失或为空时，在联网前报错，不悄悄使用旧版本。

启动时通过 `--append-system-prompt` 把任务自己的 `TASK.md` 加入系统提示，`BUG.md` 作为文件附件传入；同时传入 ENVIRONMENT.md，初始指令要求先核查预备状态、必要时恢复环境，然后按 TASK.md 复现。

提示词要求先明确判定条件，再检查环境、执行复现、保留最小测试并写报告。测试断言正常行为，避免把任意报错当作目标 Bug；允许联网安装依赖及必要的本地构建和配置，要求说明所有调整。成本允许时独立运行三次并重置测试数据，不能稳定复现时如实记录偶发性。缺少的材料确实阻止执行或判断时才报告阻塞，不因为某个非必要附件缺失而停下。

报告包括结论、判定依据、代码与环境、重跑方法、实际记录和文件变化；报告、截图和日志统一放在 `task/result/`。界面问题优先保留实际截图，非界面问题不要求凑图片；提示词要求检查实际环境，不能把缓存记录当作可运行的保证。规则要求保留用户已有改动、不修改业务代码或已有测试断言、不提交修复补丁。

修改公共模板只影响之后生成的任务。要调整某个已有任务，直接编辑它的 `task/TASK.md`，保留末尾的“代码基线”，下一次启动即读取新内容，不必重新下载代码或生成目录。已经运行中的 pi 不会自动刷新提示词。

更早生成的任务若缺少依赖或工具准备脚本，若已有 deps-config.json 和 tools-config.json，可用 update_task.py 升级运行脚本；更旧且缺少配置的任务需重新生成。修改提示词不会自动升级脚本。

## 验证工具

```bash
python3 -m unittest discover -s script_tool/make_bug/test -p 'test_*.py' -v
```

测试使用临时 Git 仓库和模拟 GitHub/pi；依赖测试使用真实 Node/npm 和本地 fixture，强制 offline，不访问注册表或调用付费模型。缺少 Node/npm 时依赖测试会标为跳过。

## 修复后直接验证

首次 pi 探索必须按 [验证交付要求](resource/prompts/verification.md) 生成 `repro/manifest.json`、`env.sh`、`check.sh`、`run.sh` 及测试。任务生成时只提供验证器，不会预造测试或声称已复现。已有任务升级会保留自定义 TASK.md 和 repro；缺少复现包时 verify 明确阻塞，需要先由 pi 补齐。

从 task 目录执行：

```bash
# pi 在问题代码上完成交付时执行，三个独立数据起点
bash verify.sh --baseline --repeat 3

# 修改 repo 中业务代码后，沿用同一套断言
bash verify.sh

# 只有依赖输入或环境变更且需要安装时才显式执行
bash verify.sh --prepare
```

默认不调用 pi、不安装依赖；先检查工具与环境，然后执行固化测试。`--prepare` 会运行依赖安装（npm ci 会重建 node_modules），不自动安装系统库或浏览器；这些缺失时 check.sh 应给出明确准备方法。`--timeout` 默认每个检查/测试步骤 300 秒；`--repeat` 支持 1–10 次。

首次目标断言失败写入 repro/baseline.json，校验测试和配置文件；修复后若断言或测试辅助文件被修改则停止比较，业务文件不在封存列表中。清单必须完整列出自定义辅助文件，校验不是防篡改沙箱。需要改测试时先保留旧复现包与证据，在明确记录原因后重新建立基线。升级工具不会自动替换 pi 已生成的测试。

每次独立写入 result/runs/baseline-时间-随机值 或 verify-时间-随机值，包含 summary.json、outcome.json、logs、images；不覆盖旧报告。记录当前 HEAD、Git 状态、业务差异、代码内容摘要、测试校验值、覆盖范围和结果。未跟踪文件记录在内容摘要中，不自动打包其完整源码；需要迁移复现时保留 task/repo、repro 和必要工具，result 只是证据包。

退出码：0 为已覆盖场景通过，1 为结构化结果确认的目标断言失败，2 为环境/执行阻塞。基线未触发目标失败也视为无法建立基线。普通测试退出 1、pi 正常退出或旧报告存在都不能证明复现成功。批量执行遇到阻塞立即停止，避免机械重试；返回最严重状态（阻塞优先）；逐次结果见 summary.json，出现混合结果不能称稳定。服务应使用独立端口并拒绝复用旧进程，测试结束清理本次进程组。同任务使用文件锁避免并发验证。
