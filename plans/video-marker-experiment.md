# 画面编号最小实验

关联 [Issue #13](https://github.com/elexcfe/keel/issues/13)。2026-10-21 用户授权先运行真实 Chromium / FFmpeg 实验，结果出来后再决定完整接入。

## 范围和来源

仅新增可复跑的合成诊断实验，不接正式运行器，不调用 Pi，不改已有 Pi 场景、报告或媒体。实验页面和记录由 Keel 开发者编写，不能算作 Pi 独立复现成果，也不能代表 Actual 等真实项目。

## 预先确定的设计

- 固定 Playwright 版本、Chromium、800×600 页面和同尺寸视频，固定设备像素比 1。
- 三组：只录屏（baseline）；录屏加 rAF 状态采样（sample）；同样采样并绘制编号（marker）。
- 每组分别运行正常和主线程负载场景；每种组合 6 次，组内顺序交替，串行执行。
- 每轮包含 10/50/200 ms 短暂卸载、正常操作，以及 4 次受回调时序影响的竞争条件。实际开始、恢复时刻以页面测量为准，不把 setTimeout 参数当成实测持续时间。
- 页面用相同的操作阶段指示器关联视频与用例；编号标记独立，包含运行 token、文档号、采样序号及校验码。页面日志、原始视频各自保留。
- FFmpeg 真实解码画面；ffprobe 读取帧时间，检查解码帧数一致。解析标记后独立读取图表区域颜色，不能把编号或日志直接当成拍到异常。
- 记录可读率、错配、遗漏、重复编号、采样状态与画面不一致、短暂异常捕获率、异常实际时长、主线程开销及布局指标。
- 错运行 token、校验破坏、缺失编号必须拒绝匹配。无编号帧和缺日志不能编造时间。

## 判断标准

- 身份匹配：正确读回的编号必须对应同一运行记录；错配为 0，损坏标记不得假装有效。报告有效帧覆盖率，不用“部分成功”冒充全覆盖。
- 视觉对应：独立检查真实像素，分别报告能定位相关帧与异常实际被视频捕获；两者不能合并。
- 干扰：按配对结果报告 bug 触发、视频捕获和时间指标差异；不以小样本的“无显著差异”宣称无干扰。
- 如果加标记改变时序敏感用例的触发、捕获结果或明显增加工作量，则不推荐默认完整接入。只有合成样例通过也不能推断真实项目不受影响。
- 真实浏览器/解码失败属于未完成；不靠 mock 或跳过测试交付成功。

## 实施步骤

- [x] 准备隔离的真实解码依赖，记录版本和来源。
- [x] 编写合成页面、三组采集和独立解码程序。
- [x] 先做小规模冒烟，再按预定矩阵运行并保存全部原始材料。
- [x] 检查失配和异常帧，记录结果、限制及接入建议。
- [x] 整理复跑入口和源码范围；提交与推送状态由 #13 的附提交进展记录，不自动完整接入。

## 实验入口

- [fixture.html](../script_tool/make_bug/test/video_marker/fixture.html)：合成应用、操作阶段指示器、独立的卸载/恢复记录；正常和负载组使用同一页面。
- [marker.mjs](../script_tool/make_bug/test/video_marker/marker.mjs)：采样及 128×64 标记；16 字节包含 magic、运行 token、文档号、序号、CRC16 和尾标记。
- [run.mjs](../script_tool/make_bug/test/video_marker/run.mjs)：均衡的六种组内顺序、真实浏览器录屏、CDP 主线程指标、原文件 SHA256。
- [analyze.py](../script_tool/make_bug/test/video_marker/analyze.py)：真实 FFmpeg 解码，独立读取标记与图表区域颜色，保留原帧和逐帧对应记录。

原始材料写入被忽略的 `script_tool/make_bug/output/diagnostics/video-marker-20261021-*`；不提交视频、日志、依赖二进制或缓存。输出路径必须不存在，不能覆盖旧实验。完整参数和结果在本文件记录。

## 环境准备与初步检查

- 从已配置的 Ubuntu noble 软件源用 `apt-get download` 下载并用 `dpkg-deb -x` 解压到被忽略的 `script_tool/make_bug/tools/diagnostics/ffmpeg-noble`。没有执行系统安装；107 个包的名称和 SHA256 保存在该目录 `packages.json`。
- 首次启动缺少 NSS/NSPR，第二次启动受执行沙箱的系统调用限制，两次失败的 `captures.json` 均保留。补齐隔离依赖后，经执行授权在沙箱外运行本地合成 Chromium 页面。
- `smoke3` 完成六段冒烟录屏及真实解码；编号能够读回，但出现采样状态与实际画面不一致，正式矩阵继续核查。
- `python3 -m unittest discover -s script_tool/make_bug/test -p 'test_video_evidence.py' -v` 在隔离 FFmpeg 的 PATH/LD_LIBRARY_PATH 下运行：13 项全部通过，包含真实视频集成测试，无跳过。未改正式视频工具。

## 正式结果：不建议默认接入逐帧画面编号

2026-10-21 00:14:20–00:16:43（Asia/Hong_Kong）完成 36 段录屏、288 次操作。版本：Playwright 1.55.0、Chromium headless shell 140.0.7339.16、Node v24.19.0、Ubuntu FFmpeg/ffprobe 6.1.1-3ubuntu5。解码 3,719 帧，操作区间内没有无法识别图表颜色的帧。所有原视频和事件日志共 72 个 SHA256 在分析后保持一致。

### 编号能读回，但不是画面状态的原子快照

12 段 marker 视频的操作区间内共有 1,098 帧，全部正确读回编号并找到同一运行的采样记录，未出现缺记录、校验失败或运行身份错配。这个分母只包括阶段编号 1–8 的操作区间，不能写成所有浏览器启动画面或所有视频规格都成功。

每段 marker 视频还用真实解码后的像素执行三项负向检查：错误运行 token、翻转一位标记但不更新 CRC、删除对应日志记录，均拒绝匹配。位翻转仅发生在内存测试副本，原视频未修改。

其中 **25 帧的画面状态与采样值不一致**（正常负载 9 帧、忙碌负载 16 帧）。典型例子：

- `normal-1-marker` 视频第 73 帧，PTS 2.920 秒，画面实际是红色（图表已卸载），编号 152。
- 编号 152 对应采样时间 2567.8 ms，采样值 `present=true`。
- 应用自身日志在 2568.0 ms 记录卸载，在 2570.6 ms 记录恢复。采样发生在卸载前，而浏览器随后画出了卸载状态；下一次采样时已经恢复。
- 原帧 `analysis/normal-1-marker-mismatch-frame-73.png` 已人工查看。它没有被拼接、重绘或后加标记。

因此编号可以定位一张相关原帧，不能把该编号的 DOM 采样值直接当成那张画面的内容。普通 rAF 采样也不能保证发现两个采样之间的短暂卸载。

### 开启编号后，短暂异常更容易漏拍

下表合并正常与忙碌组，数字为“视频中实际拍到图表消失的次数 / 操作次数”。由图表区域独立像素检查得出，不根据 DOM 日志推断。

| 用例 | 只录屏 | 加采样 | 加采样和编号 |
| --- | ---: | ---: | ---: |
| 名义 10 ms 卸载 | 12/12 | 12/12 | **4/12** |
| 名义 50 ms 卸载 | 12/12 | 12/12 | 12/12 |
| 名义 200 ms 卸载 | 12/12 | 12/12 | 12/12 |
| 时序敏感卸载 | 48/48 | 48/48 | **25/48** |
| 正常操作误报 | 0/12 | 0/12 | 0/12 |

所有预定卸载均被应用日志记录到；本轮未观察到三组之间“是否触发卸载”的次数变化，观察到的是视频捕获率变化。名义 10 ms 的实际卸载时长中位数：正常组约 11.9–12.2 ms，忙碌组约 16.2–19.8 ms。时序敏感用例实际中位时长为正常组约 0.45–1.30 ms、忙碌组约 8.5–8.7 ms。视频出现一帧不代表异常实际持续了完整的 40 ms。

持续绘制标记改变渲染及录屏采样节奏，是需要后续底层跟踪确认的解释；本实验没有把它当作已证明的具体内部机制。但捕获率下降在本次均衡对照中已经直接观察到，足以否决“逐帧标记不会影响证据采集”的默认假设。

### 主线程开销与单点时间偏移

CDP `TaskDuration` 差值除以观测墙钟秒数，取六轮中位数。这里只测浏览器页面主线程任务时间，不含整个机器、GPU 或 FFmpeg 编码进程的总 CPU。

| 负载 | 只录屏（ms/s） | 加采样（ms/s） | 加采样和编号（ms/s） |
| --- | ---: | ---: | ---: |
| 正常 | 21.41 | 29.18 | 43.85 |
| 忙碌 | 341.16 | 347.75 | 362.58 |

按同轮配对计算，marker 相比 baseline 每秒额外任务时间的中位数为正常 22.52 ms、忙碌 21.30 ms；相比 sample 为 14.45 ms、14.53 ms。单次采样函数自身的 p95 约 0.2 ms，不能据此忽略渲染和录屏带来的额外开销。

对不同的可读编号，`视频 PTS - 对应页面采样时间` 在单段内的极差为约 413.5–436.7 ms。这不是“编号解码误差”，而是说明不能只测一次起点偏移就把整段页面时间线当成视频时间线；重复帧、帧调度等都会影响这种换算。画面标记仍可直接找到录到该编号的原帧，但不能保证精确的事件发生时刻。

## 结论与下一步边界

- 最小实验已完成；编号在本规格内可解析，原帧定位有用。
- 逐帧绘制会增加开销，本次还降低了短暂异常的录制覆盖；不推荐作为默认方案完整接入。
- 不修改现有正式录屏和运行器。推荐下一轮仅研究低频动作标记及带不确定范围的区间导航，仍用相同对照验证；本次没有启动这项后续工作。
- 这只是单机、小样本、合成页面实验；未验证真实 Actual Bug 的触发概率、页面导航、多标签页、缩放、其他编码质量或其他浏览器。不能宣称任意项目无干扰，也没有验证 Pi 自动使用或节省审核时间。

## 复跑方法与材料

在仓库根目录执行。下列依赖路径是本机已验证的缓存；其他机器应把 `--playwright` 和 FFmpeg 路径替换为实际安装位置。每次使用新的输出目录，生成和分析都拒绝覆盖旧结果。Chromium 需要允许浏览器正常启动的执行环境。

```bash
video_deps="$PWD/script_tool/make_bug/tools/diagnostics/ffmpeg-noble"
export LD_LIBRARY_PATH="$video_deps/usr/lib/x86_64-linux-gnu:$video_deps/usr/lib/x86_64-linux-gnu/blas:$video_deps/usr/lib/x86_64-linux-gnu/lapack:$video_deps/usr/lib/x86_64-linux-gnu/pulseaudio"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/script_tool/make_bug/tools/shared/browsers/linux-x86_64/playwright"
experiment="$PWD/script_tool/make_bug/output/diagnostics/video-marker-rerun"

timeout 600s node script_tool/make_bug/test/video_marker/run.mjs \
  --playwright "$PWD/script_tool/make_bug/tools/shared/node/playwright/1.55.0-linux-x86_64-v24.19.0/node_modules/playwright" \
  --out "$experiment" --rounds 6

python3 script_tool/make_bug/test/video_marker/analyze.py "$experiment" \
  --ffmpeg "$video_deps/usr/bin/ffmpeg" --ffprobe "$video_deps/usr/bin/ffprobe"
```

本机正式材料目录：`script_tool/make_bug/output/diagnostics/video-marker-20261021-main/`。

- `captures.json`：运行顺序、版本、实验源码 SHA256、原视频与事件日志 SHA256。
- 每轮的 `events.json` 和 `.webm`：独立应用事件、采样、CDP 数据与原视频。
- `analysis/summary.json`：逐轮和分组结果；SHA256 为 `1f90a22aee5b7f58264d9832242f8142178c16801e4d27e185b2fc22ef749a89`。
- `analysis/frames.jsonl`：3,719 帧的实际 PTS、阶段、像素状态、编号及匹配状态。
- `analysis/commands.json`、各命令日志和 probe JSON：真实编解码调用依据。
- `analysis/verification.json`：额外核查 36 轮、288 次操作、72 个原文件摘要、12 组负向检查及未知像素数，全部通过。

这些原始材料保留在本机，不随源码推送。仓库交付的是实验代码、复跑方法和结果报告，不冒充 GitHub 上已经托管了本机原视频。
