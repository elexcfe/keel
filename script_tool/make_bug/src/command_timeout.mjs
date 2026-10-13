// Pi 1.0.4 tool_call 在校验后、执行前允许原地修改 input。
// 无额外 npm 依赖；显式加载，不启用用户或项目扩展发现。
export const DEFAULT_TIMEOUT = 120;
export const MAX_TIMEOUT = 600;

export default function commandTimeout(pi) {
  pi.on('tool_call', (event) => {
    if (event.toolName !== 'bash') return;
    const requested = event.input.timeout;
    event.input.timeout = typeof requested === 'number' && Number.isFinite(requested) && requested > 0
      ? Math.min(requested, MAX_TIMEOUT)
      : DEFAULT_TIMEOUT;
  });
}
