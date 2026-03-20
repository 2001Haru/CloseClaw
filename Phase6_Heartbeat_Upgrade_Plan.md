# Phase 6 Heartbeat 升级计划

## 1. 文档目标

本计划用于指导 CloseClaw 的 Phase 6 建设，目标是在保持轻量和安全的前提下，达到 Nanobot 在 Heartbeat + Cron 机制上的效果。

核心约束：

1. 单路径执行，不复制第二套 Agent 执行栈。
2. 配置默认安全，功能按开关渐进启用。
3. 先保证可观测与可回滚，再扩展能力。
4. 所有新增能力必须有测试门禁和验收标准。

## 2. Phase 6 升级后，Agent 新增/增强能力

Phase 6 完成后，Agent 将掌握以下能力：

1. 周期任务自治能力（Heartbeat）
   - 周期扫描 HEARTBEAT.md。
   - 先做结构化决策（skip/run），再执行任务，减少空转。
2. 显式定时任务能力（Cron）
   - 支持 at/every/cron 三类调度表达。
   - 任务可持久化、可启停、可手动触发。
3. 运维级防扰能力
   - quiet hours / active hours。
   - queue busy guard（队列拥堵时主动跳过）。
4. 路由稳定能力
   - 最近目标 + TTL 固定策略，减少消息投递抖动。
5. 结构化可观测能力
   - 记录 tick_started / tick_skipped / tick_run_started / tick_run_finished。
   - 可追踪 skip 原因、耗时、状态。
6. 诊断与联调能力
   - heartbeat trigger/status 命令可直接验证当前决策与执行链路。

## 3. 是否会带来不必要臃肿（结论）

结论：不会，只要严格按本计划落地。

控制臃肿的机制：

1. 单执行通路
   - Heartbeat 与 Cron 仅作为“触发器”，执行统一走 AgentCore 直连路径。
2. 严格非目标
   - 不做分布式调度、不做监控平台集成、不做 UI 面板。
3. 轻量默认值
   - 所有高级门禁默认关闭，按需开启。
4. 里程碑门禁
   - 每个里程碑都要求可回归、可回滚、可观测。
5. 功能开关
   - heartbeat.enabled 与 cron.enabled 可独立关闭。

## 4. 范围与非范围

### 4.1 本阶段范围

1. Heartbeat Service 实现与网关集成。
2. Cron Service 基线实现与边界定义。
3. 配置扩展（兼容旧配置）。
4. 路由策略、门禁策略、结构化日志。
5. 单测 + 集成测试 + 回归门禁。

### 4.2 明确不做

1. 分布式多实例主从选举。
2. Prometheus/OTEL exporter 全套接入。
3. 复杂 UI 运维面板。
4. 超出单进程目标的大规模调度特性。

## 5. 架构与职责边界

## 5.1 Heartbeat 与 Cron 边界

1. Heartbeat：
   - 周期扫描 HEARTBEAT.md。
   - 基于 LLM 结构化决策 skip/run。
2. Cron：
   - 管理显式调度对象（at/every/cron）。
   - 到点触发执行。
3. 共同点：
   - 统一执行入口：AgentCore 直连执行。
   - 统一投递出口：现有 outbound 路径。

## 5.2 推荐模块落点

1. closeclaw/heartbeat/service.py
2. closeclaw/heartbeat/types.py
3. closeclaw/cron/service.py
4. closeclaw/cron/types.py
5. closeclaw/cron/store.py
6. closeclaw/cli/commands.py（新增 heartbeat/cron 命令）
7. closeclaw/config.py（新增配置字段与校验）

## 6. Heartbeat 两阶段模型（对齐 Nanobot）

## 6.1 阶段一：决策

1. 输入：HEARTBEAT.md 内容。
2. 决策工具契约：
   - action: skip | run
   - tasks: string
3. 兜底规则：
   - 无 tool call 或参数非法，统一 skip。

## 6.2 阶段二：执行与通知

1. action=run：执行 agent.process_direct（或等价统一执行入口）。
2. 执行成功且 notify 可用：投递 outbound。
3. action=skip：写入结构化 skip reason，结束本轮。

## 7. 配置设计

## 7.1 Heartbeat 配置

建议挂载在 gateway 下：

1. heartbeat.enabled: bool = true
2. heartbeat.interval_s: int = 1800
3. heartbeat.quiet_hours:
   - enabled: bool = false
   - timezone: string (IANA)
   - ranges: list["HH:MM-HH:MM"]
4. heartbeat.queue_busy_guard:
   - enabled: bool = false
   - max_queue_size: int = 100
5. heartbeat.routing:
   - target_ttl_s: int = 1800
   - fallback_channel: string = cli
   - fallback_chat_id: string = direct
6. heartbeat.notify.enabled: bool = true

## 7.2 Cron 配置

1. cron.enabled: bool
2. cron.store_file: string = cron_jobs.json
3. cron.default_timezone: string = UTC

## 7.3 兼容规则

1. 新字段缺失时自动回落默认值。
2. 可选字段非法时不应导致启动失败，改为告警并降级。
3. heartbeat/cron 关闭时行为为 no-op，日志可见。

## 8. 里程碑与交付物

## M6-0 设计冻结（0.5 天）

交付：

1. Heartbeat 决策契约冻结。
2. 配置字段与默认值冻结。
3. 事件与日志字段命名冻结。

验收：

1. skip/run 语义无歧义。
2. Heartbeat 与 Cron 边界无冲突。

## M6-1 Heartbeat MVP（2-3 天）

交付：

1. HeartbeatService 基础生命周期（start/stop/_run_loop/_tick）。
2. HEARTBEAT.md 扫描与决策链路。
3. 执行回调和通知回调接入。

验收：

1. start 幂等。
2. 文件缺失/空内容安全跳过。
3. skip/run 基础路径单测通过。

## M6-2 可靠性与重试（1 天）

交付：

1. transient error 重试包装。
2. tick 级异常隔离。
3. trigger_now 立即触发接口。

验收：

1. transient error 可恢复。
2. 单次失败不拖垮主循环。
3. trigger_now 可返回 action 与摘要。

## M6-3 运营门禁（1-2 天）

交付：

1. quiet hours。
2. queue busy guard。
3. skip reason 分类字典。

验收：

1. 各门禁可独立开关。
2. skip 原因稳定可观测。

## M6-4 路由稳定性（1 天）

交付：

1. 最近目标选择策略。
2. target TTL 固定缓存。
3. fallback 路由策略。

验收：

1. TTL 窗口内路由稳定。
2. 无有效目标时 fallback 正常。

## M6-5 Cron 基线（2-3 天）

交付：

1. Cron 类型（at/every/cron）。
2. Store 持久化（json）。
3. 单定时器 + 到点批量扫描调度。
4. on_job 回调接入 Agent 执行。

验收：

1. add/list/remove/enable/disable/run-now 可用。
2. 重启后可恢复 job。

## M6-6 Cron 安全与协同（1 天）

交付：

1. 递归防护（cron 上下文禁止 add）。
2. 时区合法性校验。
3. heartbeat+cron 并行联调。

验收：

1. 递归路径被阻断并返回可读错误。
2. 非法时区拒绝且提示明确。
3. 两套子系统互不阻塞。

## M6-7 CLI 与文档收口（1 天）

交付：

1. closeclaw heartbeat trigger。
2. closeclaw heartbeat status。
3. closeclaw cron 命令组（最小可用）。
4. HEARTBEAT.md 模板与 README 更新。

验收：

1. 不改代码即可完成运维联调。
2. 文档与行为一致。

## 9. 数据契约

## 9.1 Heartbeat 决策载荷

1. action: skip | run
2. tasks: string
3. reason: optional string

## 9.2 Heartbeat 事件模型

1. event_name
2. ts
3. run_id
4. session_key
5. channel
6. chat_id
7. decision_action
8. skip_reason
9. duration_ms
10. status

## 9.3 Cron Job 最小模型

1. id
2. enabled
3. schedule.kind（at/every/cron）
4. schedule 字段（at_ms/every_ms/expr/tz）
5. payload.message
6. payload.delivery（channel/chat_id/deliver）
7. state（next_run_at_ms/last_run_at_ms/last_status/last_error）

## 10. 测试与门禁

## 10.1 单元测试

Heartbeat：

1. start 幂等。
2. disabled no-op。
3. 空文件 skip。
4. 非法决策回落 skip。
5. run 路径可执行。
6. trigger_now skip/run 正确。
7. transient retry 成功。
8. quiet hours skip。
9. queue busy skip。
10. target TTL 稳定性。

Cron：

1. add/list/remove/update。
2. next-run 计算（at/every/cron）。
3. timezone 校验。
4. 持久化读写。
5. 外部修改 reload。
6. recursion guard。

## 10.2 集成测试

1. Heartbeat 与 Cron 同进程并行运行。
2. 两者统一走 outbound 投递路径。
3. 统一复用 Agent 执行入口，无重复编排。

## 10.3 回归门禁

1. 现有 AgentCore 主循环测试全绿。
2. auth/state/context 回归全绿。
3. Phase 6 新增测试在 CI 必须通过。

## 11. 灰度与回滚

## 11.1 Feature Flags

1. gateway.heartbeat.enabled
2. gateway.cron.enabled
3. heartbeat.notify.enabled
4. heartbeat.queue_busy_guard.enabled

## 11.2 灰度步骤

1. 先开 heartbeat（不推送或低频推送）。
2. 再开 cron 的低风险提醒任务。
3. 最后开 quiet hours、queue guard、TTL 稳定路由。

## 11.3 回滚方案

1. heartbeat 与 cron 独立关闭。
2. 保留任务文件与状态文件，不做破坏性迁移。
3. 回退到旧执行路径时无需数据清洗。

## 12. 风险与缓解

1. 风险：LLM 决策波动。
   - 缓解：结构化决策 + 严格 skip 兜底。
2. 风险：通知噪音。
   - 缓解：quiet hours + queue guard + notify 开关。
3. 风险：路由误投递。
   - 缓解：TTL 固定 + fallback + 结构化日志。
4. 风险：Cron 递归膨胀。
   - 缓解：cron 上下文中禁止 add。
5. 风险：功能蔓延导致臃肿。
   - 缓解：坚持单路径执行与明确非目标。

## 13. Phase 6 完成定义（DoD）

满足以下条件即视为完成：

1. Heartbeat 两阶段模型可用且测试通过。
2. Cron 基线调度可用且测试通过。
3. quiet hours / queue guard / TTL 路由稳定能力可用。
4. skip/run/error 结构化可观测。
5. trigger/status 命令可用于运维验证。
6. 全量回归与 Phase 6 新增测试通过。

## 14. 执行清单（可直接施工）

1. 冻结契约与配置键。
2. 实现 heartbeat service + 测试。
3. 实现重试与 trigger_now + 测试。
4. 实现 quiet hours 与 queue guard + 测试。
5. 实现 target 选择与 TTL + 测试。
6. 实现 cron service/store/types + 测试。
7. 实现 cron 递归防护 + 测试。
8. 接入 CLI 命令 + 文档。
9. 跑扩展回归并记录验收报告。

## 15. PR 粒度建议

1. PR-1：heartbeat 骨架 + 基础测试。
2. PR-2：决策重试 + trigger_now + 事件模型。
3. PR-3：quiet hours + queue guard + 路由 TTL。
4. PR-4：cron types/store/service + 调度测试。
5. PR-5：cron 递归防护 + 并行集成。
6. PR-6：CLI 命令 + 文档 + 最终回归报告。

## 16. 与 Nanobot 对齐原则

1. 对齐“行为模式”，不复制实现细节。
2. 复用 CloseClaw 现有安全栈和状态模型。
3. 默认轻量，按需增配。
4. 优先保证确定性（skip/run）和可观测性，不盲目扩功能面。

## 17. 施工版（可直接开工）

本节将 Phase 6 拆为可执行施工单元。每个施工单元包含：目标、改动文件、测试命令、回滚点、预计工时。

### 17.1 施工总顺序与依赖

1. S0 设计冻结与配置键冻结
2. S1 Heartbeat MVP 主链路
3. S2 Heartbeat 可靠性与 trigger
4. S3 运营门禁（quiet hours / queue busy）
5. S4 路由稳定（target TTL）
6. S5 Cron 基线
7. S6 Cron 安全与并行协同
8. S7 CLI 命令与文档收口

依赖关系：

1. S1 依赖 S0
2. S2/S3/S4 依赖 S1
3. S6 依赖 S5
4. S7 依赖 S2/S4/S6

### 17.2 S0 设计冻结（0.5 天）

目标：冻结契约，避免施工过程中来回返工。

改动文件（预期）：

1. closeclaw/config.py
2. config.example.yaml
3. Planning.md
4. Phase6_Heartbeat_Upgrade_Plan.md

输出物：

1. Heartbeat 决策载荷固定（action/tasks/reason）
2. 配置字段和默认值固定
3. 事件字段命名固定

验证命令：

1. 配置解析测试（新增）
2. 现有回归快速集

回滚点：

1. 仅文档与配置变更，可整包回滚，不影响运行时。

### 17.3 S1 Heartbeat MVP（2-3 天）

目标：打通“扫描 -> 决策 -> 执行 -> 通知”最短闭环。

改动文件（预期）：

1. closeclaw/heartbeat/service.py（新增）
2. closeclaw/heartbeat/types.py（新增）
3. closeclaw/cli/commands.py（集成启动/停止）
4. closeclaw/config.py（读取 heartbeat 配置）
5. tests/test_heartbeat_service.py（新增）

实现要点：

1. start/stop 幂等
2. _run_loop 异常隔离
3. _tick 读取 HEARTBEAT.md
4. 无效决策统一 skip

验证命令：

1. pytest tests/test_heartbeat_service.py -q
2. 扩展聚焦回归（后续统一跑）

回滚点：

1. gateway.heartbeat.enabled=false 时应完全 no-op。

### 17.4 S2 可靠性与 trigger（1 天）

目标：补齐 transient retry 与人工触发能力。

改动文件（预期）：

1. closeclaw/heartbeat/service.py
2. closeclaw/providers/*（必要时复用重试封装）
3. closeclaw/cli/commands.py（heartbeat trigger）
4. tests/test_heartbeat_service.py

实现要点：

1. transient error 退避重试
2. trigger_now 返回 action/tasks/result 摘要

验证命令：

1. pytest tests/test_heartbeat_service.py -q

回滚点：

1. 关闭 trigger 命令入口，不影响定时 loop。

### 17.5 S3 运营门禁（1-2 天）

目标：降低扰民和高峰期噪声。

改动文件（预期）：

1. closeclaw/heartbeat/service.py
2. closeclaw/config.py
3. tests/test_heartbeat_service.py

实现要点：

1. quiet hours 命中即 skip
2. queue busy 命中即 skip
3. skip reason 结构化输出

验证命令：

1. pytest tests/test_heartbeat_service.py -q

回滚点：

1. heartbeat.quiet_hours.enabled=false
2. heartbeat.queue_busy_guard.enabled=false

### 17.6 S4 路由稳定（1 天）

目标：减少目标漂移，提升可预测性。

改动文件（预期）：

1. closeclaw/heartbeat/service.py
2. closeclaw/session/*（仅读取会话索引）
3. tests/test_heartbeat_routing.py（新增）

实现要点：

1. 最近目标选择
2. target TTL 固定缓存
3. fallback channel/chat_id

验证命令：

1. pytest tests/test_heartbeat_routing.py -q
2. pytest tests/test_heartbeat_service.py -q

回滚点：

1. heartbeat.routing.target_ttl_s=0（退化为每次重选）。

### 17.7 S5 Cron 基线（2-3 天）

目标：提供最小可用显式调度能力。

改动文件（预期）：

1. closeclaw/cron/types.py（新增）
2. closeclaw/cron/store.py（新增）
3. closeclaw/cron/service.py（新增）
4. closeclaw/config.py
5. tests/test_cron_service.py（新增）

实现要点：

1. at/every/cron 三类 schedule
2. 单 timer + due 扫描
3. json 持久化和重启恢复

验证命令：

1. pytest tests/test_cron_service.py -q

回滚点：

1. gateway.cron.enabled=false。

### 17.8 S6 Cron 安全与协同（1 天）

目标：避免递归膨胀，确认与 heartbeat 并行无阻塞。

改动文件（预期）：

1. closeclaw/cron/service.py
2. closeclaw/agent/tools/*（cron 上下文约束）
3. tests/test_cron_service.py
4. tests/test_heartbeat_cron_integration.py（新增）

实现要点：

1. recursion guard
2. timezone 校验
3. heartbeat + cron 并行启动

验证命令：

1. pytest tests/test_cron_service.py tests/test_heartbeat_cron_integration.py -q

回滚点：

1. 关闭 cron 开关，heartbeat 可单独继续运行。

### 17.9 S7 CLI 与文档收口（1 天）

目标：形成可运维、可交接、可验收的发布状态。

改动文件（预期）：

1. closeclaw/cli/commands.py
2. README.md
3. templates/HEARTBEAT.md（若仓库已有模板目录）
4. Planning.md
5. Phase6_Heartbeat_Upgrade_Plan.md

实现要点：

1. heartbeat trigger/status
2. cron 最小命令组
3. 文档与行为对齐

验证命令：

1. Phase 6 全测试
2. 现有扩展聚焦回归

回滚点：

1. CLI 命令可隐藏开关控制，不影响内核运行。

### 17.10 Phase 6 建议测试命令组合

1. 最小快速回归：
   - pytest tests/test_heartbeat_service.py tests/test_cron_service.py -q
2. 协同回归：
   - pytest tests/test_heartbeat_cron_integration.py -q
3. 主线回归（建议沿用现有扩展聚焦集）：
   - pytest tests/test_runtime_loop_service.py tests/test_state_service.py tests/test_tool_schema_service.py tests/test_context_service.py tests/test_transcript_repair.py tests/test_memory_retrieval_integration.py tests/test_context_interface_cleanup.py tests/test_agent_core.py tests/test_agent_main_loop.py tests/test_phase2_acceptance.py -q

## 18. 开工前待你决策事项（必须）

以下事项建议在 S0 冻结，避免施工返工。

1. 优先级策略（必须）
   - 选项 A：先 Heartbeat 后 Cron（推荐，风险更低）
   - 选项 B：Heartbeat 与 Cron 并行开发
   - 推荐：A

2. Phase 6 与 Phase 5 的并行策略（必须）
   - 选项 A：Phase 5 暂停，Phase 6 单线推进
   - 选项 B：Phase 5/6 双线并行（需严格 feature flag）
   - 推荐：B（但只允许小步 PR）

3. quiet hours 默认策略（必须）
   - 选项 A：默认关闭
   - 选项 B：默认开启（例如 23:00-08:00）
   - 推荐：A

4. queue busy 门槛值（必须）
   - 选项 A：max_queue_size=100
   - 选项 B：max_queue_size=50
   - 推荐：A（保守兼容）

5. target TTL 默认值（必须）
   - 选项 A：1800s
   - 选项 B：900s
   - 推荐：A

6. Cron 时区基准（必须）
   - 选项 A：UTC
   - 选项 B：Asia/Shanghai
   - 推荐：A（跨环境更稳定）

7. 通知策略（建议决策）
   - 选项 A：heartbeat.notify.enabled=true
   - 选项 B：先 false，稳定后再开
   - 推荐：B（先稳后放量）

8. 验收口径（必须）
   - 选项 A：Phase 6 全量完成后一次性验收
   - 选项 B：按 S1/S3/S5/S7 阶段验收
   - 推荐：B

9. 文档语言规范（建议）
   - 选项 A：中文主文档 + 英文术语
   - 选项 B：中英双语并列
   - 推荐：A

## 19. 开工建议（默认决策集）

若你暂时不逐项拍板，可采用以下默认决策集直接开工：

1. 采用先 Heartbeat 后 Cron 的顺序。
2. Phase 5/6 并行，但每次仅合并一个小 PR。
3. quiet hours 默认关闭。
4. queue busy 阈值 100。
5. target TTL 为 1800s。
6. cron 默认时区 UTC。
7. heartbeat 通知默认先关闭，联调后开启。
8. 采用分阶段验收（S1/S3/S5/S7）。
