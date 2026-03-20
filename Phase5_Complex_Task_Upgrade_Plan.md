# Phase5 Complex Task Upgrade Plan (CloseClaw)

生成时间: 2026-03-18  
适用仓库: CloseClaw/CloseClaw  
目标版本: Phase5  
文档类型: 实施指导基线（Architecture + Execution + Gate）

当前执行状态（2026-03-18）：

1. P0 设计冻结：已完成
2. P1 Orchestrator MVP：已完成
3. P2-A Guard/Hook 骨架：已完成（2026-03-19）
4. P2-B 触发迁移（Context->Guard + run 幂等）：已完成（2026-03-19）
5. P3-A Progress/Todo 基础设施：已完成（2026-03-19）
6. P3-B no-progress 强制 replan（先 plan_update 再终止）：已完成（2026-03-19）
7. P4 子任务接口预留（types + registry + docs + tests）：已完成（2026-03-19）

P2/P3/P4 开工判定（2026-03-19）：

1. 结论：**可以开工**，建议按 P2 -> P3 -> P4 顺序推进。
2. 理由：
  - 单主循环已稳定（P1/P1.5 完成）。
  - 关键回归（同轮收束、授权续跑、记忆压缩路径）已有覆盖。
  - 最新 CRITICAL 硬截断与 compact memory 强化已落地，可作为 P2 统一 Guard 触发的稳定基线。
3. 开工前硬门禁（建议 0.5 天内完成核验）：
  - 所有 Phase5 相关测试在当前分支全绿。
  - 运行时无双路径触发 flush/compact（日志抽样确认）。
  - 回滚入口保持可用（版本回退脚本或稳定 tag 可直接恢复）。

---

## 0. 文档目的与适用范围

本计划是 Phase5 建设的执行蓝图，用于指导以下活动：

1. 架构设计冻结
2. 开发任务拆解与排期
3. 测试与灰度门禁
4. 上线和回滚策略

适用对象：

1. Core 开发
2. QA
3. 发布/运维

非目标：

1. 本文不定义 UI 样式变更
2. 本文不引入多机分布式调度
3. 本文不在 Phase5 内实现完整多代理自治网络

---

## 1. 背景与问题定义

CloseClaw 当前已具备：

1. 工具调用基础能力
2. 上下文阈值检测、压缩与 flush
3. 长期记忆存储与检索

但复杂任务路径仍存在结构性问题：

1. 多步执行分支散落在 `process_message` 及周边流程
2. 工具调用后缺少统一收束机制（表现为先工具、再 OK、再追问才总结）
3. 若直接叠加 todo/子任务能力，会形成“多循环并存”的维护风险

Phase5 必须解决的核心矛盾：

1. 保持现有稳定性
2. 同时引入复杂任务执行能力
3. 不制造第二套主循环

---

## 2. 目标与量化指标

## 2.1 业务目标

1. 单轮任务完成率提升
2. 复杂任务可追踪、可恢复、可终止
3. 错误场景可解释、可回放

## 2.2 技术目标

1. 引入唯一 Orchestrator 主循环
2. 统一 Action 协议
3. Guard/Hook 化 token 压力与记忆策略

## 2.3 KPI（发布门禁）

1. Same-turn completion rate（含工具调用）>= 95%
2. “OK 占位回复”占比 < 1%
3. 复杂任务平均轮次下降 >= 20%
4. 无进展死循环事件下降 >= 80%
5. 新路径异常回滚时长 <= 10 分钟

---

## 3. 设计原则（借鉴 Moltbot）

1. 单主干原则
- 只有一个主执行循环，禁止新增平行主循环。

2. 分层职责原则
- Plan/Act/Observe/Decide 分离，避免在任一层夹杂其他层职责。

3. 策略外置原则
- Retry、Recall、Compaction、Flush、Budget 等由策略层注入。

4. 可观测性优先
- 每步必须具备 run_id、step_id、action_id、decision_reason。

5. 可灰度与可回滚
- 全流程受 feature flag 控制，支持会话级灰度。

---

## 4. 目标架构

## 4.1 Orchestrator 状态机

状态：

1. PLAN
- 读取上下文与策略，生成下一步 Action。

2. ACT
- 执行 Action（工具/回答/计划更新）。

3. OBSERVE
- 标准化执行结果并写入运行上下文。

4. DECIDE
- 判断继续、终止、重规划、升级授权。

状态转移：

1. PLAN -> ACT
2. ACT -> OBSERVE
3. OBSERVE -> DECIDE
4. DECIDE -> PLAN 或 FINISH

## 4.2 终止条件（硬门禁）

满足任一条件立即终止：

1. 生成可交付用户回复（final_answer）
2. 触发人工授权（auth_required）
3. 达到 budget 上限（steps/tokens/time 任一）
4. no_progress 触发阈值
5. fatal_error 不可恢复

## 4.3 Action 协议

MVP Action（本轮仅保留 3 个）：

1. tool_call
2. final_answer
3. plan_update

扩展 Action（Phase5+，不进入本轮开发承诺）：

1. spawn_subtask
2. wait_result
3. escalate_auth

建议 Action 数据结构：

```python
@dataclass
class Action:
    type: Literal[
        "tool_call",
        "final_answer",
        "plan_update",
    ]
    payload: dict[str, Any]
    reason: str
    confidence: float
```

约束说明：

1. 本轮如出现扩展 Action 需求，一律转化为 `plan_update` 并返回人工决策。
2. 实现层可灵活组织文件与模块，但对外 Action 契约必须保持最小集合。

## 4.4 Guard 与 Hook

Guard（阻断/修正）:

1. PreActBudgetGuard: 步数、token、时长预算
2. PreActContextGuard: token 压力检测，必要时 flush/compact
3. PostActSafetyGuard: transcript 修复、工具结果过滤
4. ProgressGuard: no_progress 检测

Hook（增强）:

1. BeforePlanHook: recall policy、可用工具、历史摘要注入
2. BeforeActHook: Action 参数标准化
3. AfterObserveHook: 结构化 telemetry 上报
4. AfterFinishHook: 用户回复后处理与审计记录

---

## 5. 与当前痛点映射

Case-001（必须通过）：

问题：用户问历史决策时，调用 retrieve_memory 后只回复 OK。  
目标：同轮完成“检索 + 总结答复”。

标准流程：

1. PLAN 生成 tool_call(retrieve_memory)
2. ACT 执行 tool_call
3. OBSERVE 写入检索结果
4. DECIDE 继续
5. PLAN 生成 final_answer（基于 OBSERVE 结果）
6. FINISH 返回用户

禁止行为：

1. tool_call 后直接 return 占位文本
2. 跳过 OBSERVE 直接二次调用工具

---

## 6. 代码改造蓝图（文件级）

## 6.1 新增模块

1. `closeclaw/orchestrator/types.py`
- 定义 Action、Observation、Decision、RunBudget、RunState

2. `closeclaw/orchestrator/engine.py`
- 状态机主循环与 step 驱动

3. `closeclaw/orchestrator/policies.py`
- PlanPolicy、RetryPolicy、StopPolicy、ProgressPolicy

4. `closeclaw/orchestrator/guards.py`
- BudgetGuard、ContextGuard、SafetyGuard

5. `closeclaw/orchestrator/hooks.py`
- BeforePlan/BeforeAct/AfterObserve/AfterFinish hook 协议

6. `closeclaw/orchestrator/telemetry.py`
- run/step/action 结构化日志与统计

## 6.2 改造现有模块

1. `closeclaw/agents/core.py`
- 新增 orchestrator 入口
- `process_message` 变为 orchestrator 驱动壳层
- 保留旧流程分支（feature flag 关闭时）

2. `closeclaw/context/manager.py`
- 补充统一 `ContextPressure` 输出结构

3. `closeclaw/memory/memory_flush.py`
- 由 Guard 驱动触发，避免主流程多点触发

4. `closeclaw/config.py`
- 新增 phase5 配置结构（预算、日志级别）

---

## 7. 配置设计（建议）

新增配置段：

```yaml
phase5:
  max_steps: 6
  max_tokens_per_run: 120000
  max_wall_time_seconds: 45
  no_progress_limit: 2
  telemetry:
    enabled: true
    log_actions: true
  rollout:
    mode: session_allowlist   # off | session_allowlist
    session_allowlist: []
```

说明：

1. Orchestrator 为默认且唯一主线，无运行时开关。
2. `rollout.mode`: 灰度策略
3. `no_progress_limit`: 连续无进展上限
4. 本轮不要求 `percent` 配置项

---

## 8. 日志与可观测性规范

每个 step 必填字段（本轮仅保留 4 个）：

1. run_id
2. step_id
3. action_type
4. decision

可选字段（问题定位需要时再开启）：

1. action_reason
2. token_before / token_after
3. elapsed_ms

建议事件：

1. `phase5.run.start`
2. `phase5.step.plan`
3. `phase5.step.act`
4. `phase5.step.observe`
5. `phase5.step.decide`
6. `phase5.run.finish`
7. `phase5.run.abort`

---

## 9. 分阶段执行与里程碑

## P0 设计冻结（1-2 天） ✅ 已完成

目标：

1. 冻结状态机、Action、Guard、Hook 契约
2. 明确兼容和回滚策略

交付：

1. `docs/phase5_orchestrator_spec.md`
2. `docs/phase5_action_schema.md`
3. `docs/phase5_rollout_and_rollback.md`

Gate：

1. 评审通过
2. 不存在双主循环设计

完成记录：

1. 三份 P0 冻结文档已落库。
2. Action 范围已冻结为 3 个（tool_call/final_answer/plan_update）。
3. 灰度策略冻结为 allowlist + kill-switch。

## P1 Orchestrator MVP（3-5 天） ✅ 已完成

目标：

1. 单会话 Orchestrator 落地
2. 支持 tool_call/final_answer/plan_update
3. 跑通 Case-001

交付：

1. 新增 orchestrator 模块
2. `core.py` 接入开关
3. MVP 测试通过

Gate：

1. Case-001 通过
2. auth_required 行为不回归
3. 关键路径无异常抛升

完成记录：

1. 已新增 `closeclaw/orchestrator/{types.py, engine.py, policies.py}`。
2. `core.py` 已接入 feature flag 路由与 `_process_message_v2_orchestrated()`。
3. 新增 `tests/test_phase5_recall_same_turn.py`，验证 Case-001 同轮收束。

## P1.5 授权与回复协同修正（紧急插入） ✅ 已完成

背景：

1. 复合请求（先读后写）在同轮只执行了读取，写入需用户二次追问才触发。
2. Zone C 授权请求与普通回复存在显示/时序冲突，用户感知为“授权提示掩盖回复”。

目标：

1. 同轮支持“多动作顺序执行，直到首个 auth_required 为止”。
2. `requires_auth=true` 时，同时保留 assistant 正常回复与 auth_request，不再二选一覆盖。
3. 授权通过后自动回到 Orchestrator，完成本轮收束回复，避免再次追问“写上了吗？”。

设计约束（不扩大 Action 集）：

1. Action 类型仍保持 `tool_call/final_answer/plan_update` 三类。
2. 多动作通过 `pending_actions` 队列实现，不引入新 Action 类型。
3. 保持单主循环，不新增并行控制流。

交付：

1. 输出通道拆分为两类事件：
  - `assistant_message`（普通文本）
  - `auth_request`（授权申请）
2. 引入 `pending_actions` 顺序执行机制（执行到首个 `auth_required` 即暂停）。
3. 授权回调后写回 Observation，并自动触发下一次 PLAN/DECIDE 生成最终答复。

Gate：

1. 单轮“先读后写”场景无需用户二次追问即可触发授权。
2. 授权提示出现时，不丢失 assistant 同轮文本。
3. 授权通过后自动给出最终收束回复。

完成记录：

1. Orchestrator 已支持 `pending_actions` 队列，同轮顺序执行至首个 `auth_required`。
2. 运行层已支持先发 `assistant_message` 再发 `auth_request`，避免授权申请掩盖普通回复。
3. 授权通过后已接入自动续跑与收束回复（无需再次追问）。
4. 新增测试并通过：
  - `tests/test_phase5_auth_flow.py::test_phase5_read_then_write_requires_auth_same_turn`
  - `tests/test_phase5_auth_flow.py::test_phase5_auth_request_keeps_visible_assistant_message`
  - `tests/test_phase5_auth_flow.py::test_phase5_auth_approve_auto_finalize`

## P2 Guard/Hook 融合（2-4 天）

目标：

1. flush/compact/recall 归位到 Guard/Hook
2. 删除旧路径中的并行触发点

交付：

1. Guard/Hook 接入
2. 触发路径单一化

Gate：

1. token 压力场景一致性通过
2. 无重复 flush/compact 触发

## P3 复杂任务增强（4-7 天）

目标：

1. plan_update 结构化
2. progress/no-progress 机制
3. 重规划策略

交付：

1. `todo_store.py`、`progress.py`
2. 复杂任务回放日志

Gate：

1. 连续失败可重规划
2. 无进展场景可终止

## P4 子任务接口预留（后续）

目标：

1. 定义 spawn/wait 接口
2. 建立最小 lifecycle registry

范围约束：

1. 仅保留文档占位与接口草案。
2. 不进入本轮排期，不作为 Phase5 完成条件。

Gate：

1. 接口稳定
2. 不影响主循环整洁性

---

## 10. 测试计划

## 10.1 单元测试

建议新增：

1. `tests/test_phase5_orchestrator_basic.py`
2. `tests/test_phase5_action_contract.py`
3. `tests/test_phase5_budget_guard.py`
4. `tests/test_phase5_progress_guard.py`

## 10.2 集成测试

建议新增：

1. `tests/test_phase5_recall_same_turn.py`
2. `tests/test_phase5_multi_tool_same_turn.py`
3. `tests/test_phase5_auth_interrupt.py`
4. `tests/test_phase5_context_pressure_flow.py`
5. `tests/test_phase5_read_then_write_same_turn.py`
6. `tests/test_phase5_auth_with_visible_assistant_message.py`
7. `tests/test_phase5_auth_approve_auto_finalize.py`

## 10.3 回归测试

1. 现有 context/memory/tool 测试全通过
2. 旧路径开关关闭时行为不变

## 10.4 压测与稳定性测试

1. 连续 100 轮复杂任务
2. 高频工具失败场景
3. token 逼近阈值场景

---

## 11. 灰度与发布策略

## 11.1 灰度阶段

1. 阶段 A: 内部会话 allowlist（必做）
2. 阶段 B: 全量（A 阶段门禁通过后）

说明：

1. 本轮不引入百分比灰度（10%/50%）作为硬要求。
2. 如后续规模扩大，可在 Phase5+ 再补 percent 策略。

## 11.2 灰度门禁

每阶段需满足：

1. 错误率未显著上升
2. Same-turn completion 达标
3. 无新增高优先级阻塞问题

## 11.3 回滚策略

1. 回退到上一稳定版本
2. 记录回滚 run_id 样本用于复盘

回滚触发条件（任一满足立即执行）：

1. same-turn completion 连续低于门禁阈值
2. 出现“tool 后仅占位回复”回归
3. 高优先级错误率显著上升

---

## 12. 风险清单与缓解

1. 风险: 状态机复杂度引发实现延迟
- 缓解: P1 严格限制 Action 数量

2. 风险: 与旧 flush/compact 逻辑冲突
- 缓解: P2 做唯一触发源改造

3. 风险: 观测不足导致定位困难
- 缓解: 强制结构化 telemetry

4. 风险: 灰度期间行为不一致
- 缓解: run 级别打点并按开关分桶分析

---

## 13. 角色分工（建议）

1. Architect
- P0 规范冻结、评审把关

2. Core Dev
- P1/P2 主实现

3. QA
- 用例矩阵、回归与压测

4. Release Owner
- 灰度推进、门禁检查、回滚执行

---

## 14. 周计划（建议）

Week 1:

1. P0 完成
2. P1 骨架与 Case-001 打通

Week 2:

1. P1 收敛
2. P2 接入与回归

Week 3:

1. P3 能力增强
2. 阶段 A/B 灰度

Week 4:

1. 阶段 C/D 灰度
2. 文档收尾与复盘

---

## 15. Done 定义

Phase5 视为完成需同时满足：

1. 架构层: 单主循环落地，平行循环清零
2. 功能层: Case-001 和复杂工具链同轮收束通过
3. 稳定层: 预算保护、no-progress、防死循环机制生效
4. 质量层: 单测/集成/回归全部通过
5. 发布层: 灰度门禁通过并完成全量

---

## 16. 附录 A: Case-001 验收脚本（示意）

输入：

1. “你还记得我们上次关于 F1 的决定吗？”

期望：

1. 本轮执行 retrieve_memory
2. 本轮输出总结答案
3. 不出现纯“OK/Executed tools.”占位回复

失败判定：

1. 仅工具执行回执，无内容总结
2. 需用户二次追问才完成回答

---

## 17. 附录 B: 主循环伪代码

```python
state = init_run_state(message)

while True:
		action = planner.plan(state)
		guard.pre_act(state, action)

		obs = actor.execute(action, state)
		guard.post_act(state, obs)

		state = observer.merge(state, action, obs)
		decision = decider.decide(state)

		if decision.stop:
				return decision.output
```

---

## 18. 实施任务看板（文件/函数级）

以下任务按“可直接开工”粒度定义，未完成项不得跨阶段跳过。

## 18.1 P1 任务看板

1. `closeclaw/agents/core.py`
- 新增 `_process_message_v2_orchestrated()` 入口函数。
- `process_message()` 统一走 orchestrator 单路径。
- 新路径禁止直接 `llm_response or "OK"` 作为工具后终态。

2. `closeclaw/orchestrator/types.py`
- 定义 `RunState`、`Action`、`Observation`、`Decision`、`RunBudget`。
- `Action.type` 最少支持 `tool_call/final_answer/plan_update`。

3. `closeclaw/orchestrator/engine.py`
- 实现 `run_orchestrator(message, session_ctx)`。
- 内部强制使用 PLAN->ACT->OBSERVE->DECIDE 顺序。
- 设置 `max_steps` 默认值和防死循环保护。

4. `closeclaw/orchestrator/policies.py`
- 实现 `plan_next_action()`。
- 支持“工具后必须进入一次总结候选生成”的策略。
- 注明：以上策略因为架构矛盾已被废弃

5. `tests/test_phase5_recall_same_turn.py`
- 用固定 mock 场景验证 Case-001。
- 断言：一次用户请求内返回总结，不出现占位回复。

## 18.2 P2 任务看板

1. `closeclaw/context/manager.py`
- 新增 `build_context_pressure()`，输出统一结构：
	- `status`
	- `usage_ratio`
	- `should_soft_flush`
	- `should_hard_compact`

2. `closeclaw/memory/memory_flush.py`
- 改为由 Guard 触发，不直接耦合消息主流程。
- 增加“本轮已触发”标记，防止重复执行。

3. `closeclaw/orchestrator/guards.py`
- 实现 `PreActContextGuard` 对接 flush/compact。
- 统一 error code：`context_flush_triggered`, `context_compacted`。

4. `tests/test_phase5_context_pressure_flow.py`
- 覆盖 soft/hard 两类阈值路径。

## 18.3 P3 任务看板

1. `closeclaw/orchestrator/todo_store.py`
- 定义 todo item 结构：`id/title/status/updated_at/source_step`。
- 提供增删改查和快照导出。

2. `closeclaw/orchestrator/progress.py`
- 定义 progress 评估：
	- `progress_made`
	- `stagnation_count`
	- `replan_required`

3. `closeclaw/orchestrator/policies.py`
- 加入 no-progress 重规划策略：
	- 连续 N 步无进展 -> 强制 plan_update

4. `tests/test_phase5_progress_guard.py`
- 验证无进展触发重规划和终止门禁。

## 18.4 P4 任务看板（接口预留，不做执行引擎）

1. `closeclaw/orchestrator/subtask_types.py`
- 定义 `SubtaskSpec`、`SubtaskHandle`、`SubtaskStatus`、`SubtaskResult`。
- 明确最小字段：`subtask_id/parent_run_id/intent/input_payload/status/created_at/updated_at`。

2. `closeclaw/orchestrator/subtask_registry.py`
- 提供内存态 registry（注册、查询、状态迁移、结果写回）。
- 仅单进程内可用，不引入跨进程/分布式语义。

3. `docs/phase5_subtask_interface.md`
- 冻结 spawn/wait/cancel 接口语义和错误码。
- 定义与主循环边界：P4 不直接改变 ACT 执行器。

4. `tests/test_phase5_subtask_registry.py`
- 覆盖状态流转与错误场景（不存在 subtask、重复完成、非法迁移）。

---

## 21. P2/P3/P4 详细建设计划（可直接执行）

## 21.1 总体节奏与并行策略

1. 顺序建议：P2 完成后再进入 P3，P3 稳定后再做 P4。
2. 并行边界：
- 可并行：文档、测试脚手架、Telemetry 字段预埋。
- 禁止并行：P2 Guard 改造与 P3 决策逻辑重构（避免互相污染基线）。
3. 每阶段结束必须满足：
- 单测与集成测试通过。
- 与 P1.5 关键行为不回归（同轮收束、授权后自动续跑）。

## 21.2 P2 详细计划（Guard/Hook 融合，预计 3-5 天）

目标：将 context pressure、flush、compact、recall 的触发权统一收敛到 Orchestrator Guard/Hook。

### 交付拆分

1. D1-D2：接口冻结
- 新增 `closeclaw/orchestrator/guards.py`：
  - `PrePlanContextGuard`
  - `PreActBudgetGuard`
  - `PostActSafetyGuard`
- 新增 `closeclaw/orchestrator/hooks.py`：
  - `BeforePlanHook`
  - `AfterObserveHook`

2. D2-D3：触发迁移
- `closeclaw/agents/core.py` 中将 context 检测与 flush 触发改为 guard 调用。
- 保留兼容桥（短期），并增加日志标识 `guard_path=true|false`。

3. D3-D4：重复触发消除
- 增加 run 级幂等标记：`flush_triggered_in_run`、`compact_triggered_in_run`。
- CRITICAL 路径保持硬规则：仅保留最近 10 轮。

4. D4-D5：测试与回归
- 新增：
  - `tests/test_phase5_context_pressure_flow.py`
  - `tests/test_phase5_guard_idempotency.py`
  - `tests/test_phase5_guard_hook_order.py`

### P2 验收门禁

1. WARNING 阶段最多触发一次 flush（同 run 内）。
2. CRITICAL 阶段无条件触发硬截断并输出用户可见 warning。
3. 不出现“旧路径触发 + Guard 再触发”的双触发日志。

## 21.3 P3 详细计划（复杂任务增强，预计 5-8 天）

目标：让长链路任务在同一主循环里可追踪、可判定进展、可重规划、可终止。

### 交付拆分

1. D1-D2：结构化 plan_update
- 定义 `plan_update` payload schema：
  - `goal`
  - `current_step`
  - `remaining_steps`
  - `done_criteria`
  - `risk`

2. D2-D4：Progress 引擎
- 新增 `closeclaw/orchestrator/progress.py`：
  - `compute_progress_delta(prev_state, new_observation)`
  - `stagnation_count` 累加规则
  - `replan_required` 判定

3. D4-D5：Todo 状态存储
- 新增 `closeclaw/orchestrator/todo_store.py`：
  - run 级 todo 生命周期
  - 与 `RunState.metadata` 对齐

4. D5-D6：策略接入
- `closeclaw/orchestrator/policies.py` 增加：
  - no-progress -> 强制 `plan_update`
  - 连续超限 -> 安全终止并给出用户解释

5. D6-D8：测试与压测
- 新增：
  - `tests/test_phase5_progress_guard.py`
  - `tests/test_phase5_plan_update_schema.py`
  - `tests/test_phase5_no_progress_replan.py`
  - `tests/test_phase5_long_run_stability.py`

### P3 验收门禁

1. 连续 N 步无进展（默认 N=2）时必须触发重规划或终止。
2. 复杂任务日志可回放（可还原每步 action/observation/decision）。
3. 不引入新的平行主循环。

## 21.4 P4 详细计划（子任务接口预留，预计 2-3 天）

目标：只冻结接口与状态机，不引入真正的并发子任务执行。

### 交付拆分

1. D1：接口定义
- `spawn_subtask(spec)`
- `wait_subtask(handle)`
- `cancel_subtask(handle)`

2. D1-D2：registry 落地（内存态）
- 提供最小生命周期：`created -> running -> completed|failed|cancelled`。
- 校验非法迁移并输出统一错误码。

3. D2-D3：文档与测试
- 新增接口文档与兼容性约束。
- 增加 registry 单测，不改动当前 ACT 执行路径。

### P4 验收门禁

1. 接口契约稳定，不影响 P1/P2/P3 行为。
2. Registry 生命周期与错误码测试全通过。
3. 主循环仍保持单线程串行语义。

## 21.5 风险与止损机制（P2-P4）

1. 风险：Guard 改造导致线上触发顺序变化。
- 止损：保留阶段性兼容桥 + 日志分桶对比（guard_path）。

2. 风险：P3 进展判定误杀正常任务。
- 止损：阈值可配置，先在 allowlist 会话灰度。

3. 风险：P4 接口过早侵入执行器。
- 止损：P4 明确“不接入执行引擎”，仅 registry + spec。

## 21.6 建议 PR 切分（对应 P2-P4）

1. PR-6（P2-A）：guards/hooks 基础骨架 + 空实现 + 单测脚手架。
2. PR-7（P2-B）：context pressure 迁移 + flush/compact 幂等。
3. PR-8（P3-A）：plan_update schema + progress 引擎。
4. PR-9（P3-B）：todo_store + no-progress 重规划策略。
5. PR-10（P4）：subtask interface spec + registry + 单测。

---

## 19. PR 切分计划（建议）

PR-1: Orchestrator 契约与骨架

1. 新增 `types.py` + `engine.py` 空骨架
2. 不改旧行为，仅加 feature flag

PR-2: Case-001 跑通

1. `core.py` 新路径接入
2. `tool_call -> summary` 同轮收束
3. 新增 `test_phase5_recall_same_turn.py`

PR-2.5: Zone C 协同修正（P1.5）

1. `pending_actions` 顺序执行到首个 `auth_required`
2. 输出事件拆分：`assistant_message + auth_request` 同轮并发可见
3. auth approve 后自动回流到 orchestrator 完成 final_answer
4. 新增三类集成测试覆盖“读后写/可见回复/授权后自动收束”

PR-3: Guard/Hook 接入

1. `guards.py` + `hooks.py`
2. context pressure 统一触发

PR-4: progress/todo 增强

1. `todo_store.py` + `progress.py`
2. no-progress 重规划

PR-5: Telemetry 与灰度

1. `telemetry.py`
2. rollout 先支持 allowlist + kill-switch
3. 仅保障 4 个必填字段（run_id/step_id/action_type/decision）

每个 PR 的合并门禁：

1. 单测通过
2. 回归通过
3. 关键日志字段齐全
4. 文档同步更新

---

本计划书是 CloseClaw Phase5 的正式执行基线。后续所有复杂任务能力（todo、子任务、跨会话协作）必须在该 Orchestrator 主干内扩展，不再新增平行主循环或临时分叉流程。

## 20. 与 P2/P3/P4 的兼容性说明

是否冲突：

1. 与 P2 不冲突：P1.5 处理的是输出语义与授权时序，P2 处理的是 context/flush 触发归位；两者边界清晰。
2. 与 P3 不冲突：P1.5 的 `pending_actions` 可直接作为 P3 progress/no-progress 的输入观测。
3. 与 P4 不冲突：P1.5 不引入子任务模型，仅优化单会话串行动作，对 P4 的 spawn/wait 预留无破坏。

为后续准备的调整：

1. 统一 Observation 扩展字段：`event_type`, `visible_to_user`, `requires_auth`, `resume_token`。
2. 在 P2 预先定义 Hook 接口中的输出钩子：`AfterObserveOutputHook`，避免 P3 再次改消息出口。
3. 在 P3 进度模型中加入 `pending_actions_len`、`auth_pause_count`，为 no-progress 判定提供信号。
4. 在 P4 接口草案中约定：子任务返回结果也必须走 `assistant_message/auth_request` 双事件规范。