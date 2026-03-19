# Phase5 Orchestrator Spec (P0 Freeze)

更新时间: 2026-03-18

## 1. Scope

P1 仅实现单会话、单主循环的 PLAN -> ACT -> OBSERVE -> DECIDE 编排。

## 2. Non-goals

1. 不实现子任务执行
2. 不实现并行主循环
3. 不实现百分比灰度

## 3. State Machine

1. PLAN: 生成 Action
2. ACT: 执行 Action
3. OBSERVE: 归一化执行结果
4. DECIDE: 继续或终止

终止条件:

1. final_answer 可交付
2. auth_required
3. 超过 max_steps

## 4. MVP Action Set

1. tool_call
2. final_answer
3. plan_update

扩展 Action 进入 Phase5+，本轮不承诺。

## 5. Safety Constraints

1. 禁止 tool_call 后直接返回占位文本
2. 任何 auth_required 立即终止当前 run 并返回授权信息
3. 保留旧路径，由 feature flag 控制切换
