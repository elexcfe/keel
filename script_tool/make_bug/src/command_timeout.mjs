// Pi 1.0.4 tool_call 在校验后、执行前允许原地修改 input。
// 无额外 npm 依赖；显式加载，不启用用户或项目扩展发现。
export const DEFAULT_TIMEOUT = 120;
export const MAX_TIMEOUT = 600;

export default function commandTimeout(pi) {
  pi.on('tool_call', (event) => {
    if (event.toolName !== 'bash') return;
    const deadline = Number(process.env.MAKE_BUG_DEADLINE_MS);
    const reserve = Number(process.env.MAKE_BUG_WRAP_UP_MS);
    const remaining = Number.isFinite(deadline) && deadline > 0 ? (deadline - Date.now()) / 1000 : Infinity;
    if (remaining <= (Number.isFinite(reserve) ? reserve / 1000 : 0)) {
      return { block: true, reason: '已进入报告收尾阶段，禁止新 bash 命令。使用 read/edit/write 保存报告、证据路径、重跑方式及缺失项；运行器负责退出清理。' };
    }
    const requested = event.input.timeout;
    event.input.timeout = typeof requested === 'number' && Number.isFinite(requested) && requested > 0
      ? Math.min(requested, MAX_TIMEOUT)
      : DEFAULT_TIMEOUT;
    event.input.timeout = Math.min(event.input.timeout, Math.max(.001, remaining - (Number.isFinite(reserve) ? reserve / 1000 : 0)));
  });
}
