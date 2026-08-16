# Keel 靶场：Actual Budget 真实 bug 清单

> 用途：MVP 第一周手工循环的弹药（对应 `keel-project-plan.md` Phase 0 Spike 1）。
> 靶场：[actualbudget/actual](https://github.com/actualbudget/actual) — 开源记账应用，React + TypeScript，纯 DOM 界面（适合 Playwright 断言），本地一条命令启动。
> 每个 bug 均已确认：复现步骤清晰 + 官方修复 PR 已合入（= 自带标准答案）。
> 日期：2026-07-14

---

## 环境准备

```bash
git clone https://github.com/actualbudget/actual.git
cd actual
yarn install
yarn start        # 浏览器打开 http://localhost:3001
```

- 首次进入选 "Don't use a server"（本地模式，无需后端），创建测试账本。
- **切换中文界面**：设置 (Settings) → Language → 简体中文（覆盖约 65%，主流程已翻译）。
- 每打一个 bug 前，checkout 到该 bug 修复前的 commit：`git checkout <修复commit>^`，然后重新 `yarn install && yarn start`。

## 手工循环流程（每个 bug 重复一遍）

1. **只看下面的 bug 描述**（先别看官方修复），在 bug 存在的 commit 上写 Playwright 脚本，让它**连续 3 次确定性失败**；
2. 把 bug 描述喂给 Claude Code 让它修——顺便记录它的 plan / tool calls 里能拿到什么"声明意图"（Spike 4 的免费副产品）；
3. 原样重跑脚本，看是否翻转为通过；跑项目已有测试确认不倒退；
4. `git diff <修复commit>^ <修复commit>` 对照官方真实修复，给 AI 的修复打分；
5. 记录：每步耗时、复现脚本需要哪些 bug 描述里没有的信息（这决定复现引擎 v0 的接口设计）。

---

## Bug 1（最简单，从这里开始）：拆分交易弹窗布局错乱

- Issue: [#7738](https://github.com/actualbudget/actual/issues/7738) ｜ 修复 PR: [#7814](https://github.com/actualbudget/actual/pull/7814)（仅 +10/-4 行）
- 修复 commit：`ee82a16026` → 打靶时 `git checkout ee82a16026^`

**复现步骤（中文）**：
1. 进入"所有账户"（All accounts）；
2. 选择任意一笔交易；
3. 点击分类列中的"拆分交易"（split transaction）按钮；
4. 观察弹出的拆分弹窗：出现多余滚动条、按钮被遮挡显示不全。

**备注**：德语等长文本语言下更严重，英文界面也有多余滚动条。4K/高缩放下更明显。
**验证方式**：截图对比 / 断言弹窗内按钮全部可见、无溢出滚动条。

---

## Bug 2（断言最干净）："追加到备注"规则在已有备注时不生效

- Issue: [#7303](https://github.com/actualbudget/actual/issues/7303) ｜ 修复 PR: [#8300](https://github.com/actualbudget/actual/pull/8300)
- 修复 commit：`98c096a3d0` → `git checkout 98c096a3d0^`

**复现步骤（中文）**：
1. 创建一条规则：条件为"分类是 Restaurants 且收款人是 Coffee House"，动作为"追加到备注：Appended Text"；
2. 新建一笔交易，按顺序填写：收款人 → **备注（先填上内容，如 "Coffee and cake"）** → 分类 → 金额；
3. 预期：备注变为 "Coffee and cake Appended Text"；实际：规则文本没有被追加。

**验证方式**：Playwright 读取交易行备注单元格文本，断言包含追加文本。

---

## Bug 3（数字断言）：收款人页面的规则计数含已完成日程

- Issue: [#8134](https://github.com/actualbudget/actual/issues/8134) ｜ 修复 PR: [#8281](https://github.com/actualbudget/actual/pull/8281)
- 修复 commit：`208283a517` → `git checkout 208283a517^`

**复现步骤（中文）**：
1. 创建一个收款人（payee），为其创建一个日程（schedule，会自动生成关联规则）；
2. 让该日程完成（completed）；
3. 打开"管理收款人"页面，查看该收款人的"关联规则"计数；
4. 预期：已完成日程的规则不计入；实际：计数包含了已完成日程，点进去却看不到对应规则（计数与列表不一致）。

**验证方式**：断言计数数字 == 实际展示的规则条数。

---

## Bug 4（最难，验证"不重挂载"）：报表页改筛选条件就整页刷新

- Issue: [#8351](https://github.com/actualbudget/actual/issues/8351) ｜ 修复 PR: [#8352](https://github.com/actualbudget/actual/pull/8352)
- 修复 commit：`c64c0aa049` → `git checkout c64c0aa049^`

**复现步骤（中文）**：
1. 进入报表（Reports）区，打开 Crossover Point 报表；
2. 在侧边栏取消勾选一个或多个支出分类（任何输入修改都会触发）；
3. 预期：只有图表原地更新，布局和滚动位置保持；
4. 实际：整页闪烁、布局卸载重挂载、出现全页 loading、滚动位置被重置。

**验证方式**：先滚动页面到某位置 → 修改筛选 → 断言滚动位置未重置 / 全页 loading 指示器未出现。这是四个中最考验复现脚本设计的一个。

---

## Bug 5（备用池）

以下候选未逐个核实修复 PR 合入状态，打完前 4 个后从这里补：

- [#3041](https://github.com/actualbudget/actual/issues/3041) 按收款人排序时转账交易不参与排序（关联 PR #8314 未合入，需再查实际修复）；
- [#8438](https://github.com/actualbudget/actual/issues/8438) 复制的定期转账改日期后不同步到对方账户（修复 PR 尚未合入——可当"活 bug"练手，但没有标准答案）；
- 仓库共有 **717 个**"已关闭 + 关联 PR"的 bug 可挖：
  `https://github.com/actualbudget/actual/issues?q=is%3Aissue+is%3Aclosed+label%3Abug+linked%3Apr`

---

## 附注

- **这个仓库大量修复 PR 标着 [AI]**：维护者已在用 AI 修 bug，且同一 issue 常有多个 AI PR 被拒、仅一个合入（如 #7303 有 5 个 PR 只合入 1 个）——被拒的 PR 是"AI 修不对、需要人验证"的活证据，正是 Keel 的存在理由，试点叙事可直接引用。
- 备选靶场 **Excalidraw**（383 个候选 bug，最易启动），但是 canvas 应用，无法 DOM 断言，复现难度高一档，先缓。
- 中文备选 **NocoBase**（全中文界面 + 中文 issue），但规范关联 PR 的 bug 仅 23 个，且低码平台领域概念复杂，暂不推荐作为第一靶场。
