# Phase5 Rollout and Rollback (P0 Freeze)

更新时间: 2026-03-18

## Rollout

P1 仅支持两段:

1. allowlist 内部会话
2. 全量

不要求 10% / 50% 百分比灰度。

## Runtime Mode

Phase5 Orchestrator 为默认且唯一执行主线。

1. 不再维护旧路径分流。
2. 回滚通过版本回退实现，不再通过运行时开关切换。

## Rollback

满足任一条件立即回滚:

1. same-turn completion 持续低于门禁
2. 出现 tool 后仅占位回复回归
3. 高优错误率显著上升

回滚动作:

1. 回退到上一稳定版本
2. 保留 run_id 样本用于复盘
