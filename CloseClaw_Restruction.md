# CloseClaw 兼容内核化落地实施文档

## 1. 文档目标

本实施文档用于把重构建议转化为可执行工程计划，满足以下目标:

1. 保持 CloseClaw 轻量且易读。
2. 兼容 Moltbot/OpenClaw 生态（工具与 skills）。
3. 引入 MCP 协议能力且不膨胀 core.py。
4. 将权限模型外显简化为 need_auth，同时保留内部安全可控性。

## 2. 设计原则（硬约束）

1. 单一内核契约: AgentCore/Runtime 只处理 ToolSpec v2，不处理外部协议原生结构。
2. 复杂度外置: OpenClaw/MCP 差异只能进入 adapter/bridge 层。
3. 最小侵入: 先兼容、后替换；先并行路径、后切主路径。
4. 向后兼容: 保留 Zone 读取能力，提供自动迁移到 need_auth。
5. 可观测优先: 新链路必须带 run_id/session_id/tool_name/source。

## 3. 目标目录结构（建议）

以下目录在不破坏现有模块的前提下新增:

```text
closeclaw/
  compatibility/
    __init__.py
    toolspec_v2.py
    source_types.py
    mappers/
      __init__.py
      zone_mapper.py
      schema_mapper.py
    adapters/
      __init__.py
      openclaw_adapter.py
      native_adapter.py
    skills/
      __init__.py
      openclaw_skill_loader.py
  mcp/
    __init__.py
    bridge.py
    client_pool.py
    transport/
      __init__.py
      stdio_client.py
      http_client.py
      sse_client.py
    projection/
      __init__.py
      tool_projector.py
      resource_projector.py
      prompt_projector.py
  services/
    __init__.py
    planning_service.py
    tool_execution_service.py
    auth_service.py
    context_service.py
```

说明:

1. compatibility 处理“来源兼容”问题。
2. mcp 处理“协议桥接”问题。
3. services 处理 core.py 解耦问题。

## 4. ToolSpec v2 统一契约

## 4.1 数据模型

```python
from dataclasses import dataclass, field
from typing import Any, Literal

RiskTag = Literal["filesystem", "network", "exec", "system_path", "external_api"]
SourceType = Literal["native", "openclaw", "mcp"]

@dataclass
class ToolSpecV2:
    name: str
    description: str
    input_schema: dict[str, Any]
    need_auth: bool
    tool_type: str  # file/websearch/shell/custom
    capability_tags: list[str] = field(default_factory=list)
    risk_tags: list[RiskTag] = field(default_factory=list)
    source: SourceType = "native"
    source_ref: str | None = None  # 外部来源唯一标识（可选）
    metadata: dict[str, Any] = field(default_factory=dict)
```

## 4.2 与现有 Tool 的关系

现有模型来源: closeclaw/types/models.py 的 Tool。

迁移策略:

1. 保留现有 Tool（旧契约）用于兼容路径。
2. 新增 ToolSpecV2（新契约）用于统一执行路径。
3. 在 ToolExecutionService 内部先执行 normalize(old/new/external) -> ToolSpecV2。

## 4.3 Zone 到 need_auth 映射（默认策略）

1. Zone.A -> need_auth = false
2. Zone.B -> need_auth = false
3. Zone.C -> need_auth = true

注意:

1. 该映射只作为默认迁移规则。
2. 实际执行仍可根据 risk_tags 附加策略（比如 deny 或二次确认）。

## 5. OpenClaw/Moltbot 兼容适配

## 5.1 适配范围（第一批）

建议优先支持高频能力:

1. 文件读写类工具。
2. Shell 执行类工具。
3. Web/检索类工具。
4. 基础 skills 加载（指令模板 + 工具声明）。

## 5.2 适配器接口

```python
from typing import Protocol

class ExternalToolAdapter(Protocol):
    def can_handle(self, source_obj: object) -> bool: ...
    def to_toolspec_v2(self, source_obj: object) -> ToolSpecV2: ...
```

OpenClawCompatibilityAdapter 核心职责:

1. 解析上游工具描述结构。
2. 映射参数 schema 到 input_schema。
3. 依据工具特征填充 need_auth/risk_tags/capability_tags。
4. 生成 source="openclaw" 与 source_ref。

## 5.3 兼容性边界（必须明确）

1. 兼容“协议语义”，不兼容上游内部私有实现。
2. 不复制上游运行时全栈，仅接入工具和 skills 契约。
3. 对不支持字段写入 metadata.compat_warnings 并输出启动告警。

## 6. MCP Bridge 设计

## 6.1 组件边界

1. Transport 层: stdio/http/sse 连接与收发。
2. Session 层: server 生命周期、重连、超时、健康检查。
3. Projection 层: MCP tool/resource/prompt 投影为 ToolSpecV2。
4. Execution 接入: 投影后的 ToolSpecV2 与 native/openclaw 工具统一走 ToolExecutionService。

## 6.2 最小配置示例

```yaml
mcp:
  enabled: true
  servers:
    - id: local_fs
      transport: stdio
      command: "python"
      args: ["-m", "my_mcp_server"]
      timeout_seconds: 20
    - id: remote_search
      transport: http
      url: "http://127.0.0.1:9000/mcp"
      timeout_seconds: 15
```

## 6.3 调用策略

1. 启动时拉取 server capabilities，建立本地投影缓存。
2. 每次调用先命中缓存，再按 source_ref 路由到对应 server。
3. 失败重试采用指数退避（最多 2 次）。
4. MCP 工具调用前统一进入 need_auth 审核链。

## 7. core.py 解耦实施

现状参考: closeclaw/agents/core.py。

拆分目标:

1. AgentRuntimeKernel: 只管理状态机与 orchestrator 驱动。
2. PlanningService: LLM 交互与 Action 计划。
3. ToolExecutionService: 工具标准化、路由、执行、回写。
4. AuthService: 授权请求、恢复、超时。
5. ContextService: 压缩、flush、memory retrieval 编排。

## 7.1 迁移顺序（低风险）

1. 第一步: 抽纯函数
   - 从 core.py 抽出格式化函数、构建消息函数、结果序列化函数。
2. 第二步: 抽状态无关服务
   - 先抽 ToolExecutionService 与 PlanningService。
3. 第三步: 抽状态相关服务
   - 再抽 AuthService 与 ContextService。
4. 第四步: Core 收口
   - core.py 仅保留流程编排与依赖装配。

## 7.2 建议门禁指标

1. core.py 行数目标: 作为参考指标而非硬性约束；优先保证职责边界清晰与行为稳定。
2. 核心 public 方法圈复杂度: 每个 <= 10
3. service 单测覆盖率: >= 80%

## 7.3 进一步拆分下沉计划（职责优先）

目标说明:

1. 本阶段不以行数阈值作为唯一目标。
2. 以“core 仅负责编排与依赖装配”为收口标准。
3. 所有拆分采用“薄切 + 行为等价回归”方式推进。

### 7.3.1 当前保留在 core 的高耦合块

1. Run 主循环与 auth 等待/中断处理（run + message_input_fn/message_output_fn 协调）。
2. Tool schema 格式化（_format_tools_for_llm）。
3. 状态持久化与恢复（_save_state/_restore_state/load_state_from_disk）。
4. Orchestrator 内部局部构建函数（planner/actor/observer/decider 闭包）。

### 7.3.2 下一阶段拆分包（推荐顺序）

1. 包 A: Tool schema 下沉（低风险）
   - 新增 `ToolSchemaService`（或 `ToolFormatterService`）。
   - 承接 `_format_tools_for_llm` 的 JSON Schema 归一化逻辑。
   - core 中仅保留 `self.tool_schema_service.format_tools(self.tools)` 调用。

2. 包 B: 状态持久化下沉（中风险）
   - 新增 `StateService`（save/load/restore 统一入口）。
   - 承接 `_save_state`、`load_state_from_disk`、`_restore_state` 的序列化与落盘。
   - 明确原子写策略（tmp + replace）与 workspace_root 限界不变。

3. 包 C: 主循环下沉（中高风险）
   - 新增 `RuntimeLoopService`，负责:
     - 轮询后台任务
     - auth 等待与新消息打断竞争
     - 输出事件组装（response/auth_request/error/task_completed）
   - core 保留:
     - 依赖注入
     - 状态切换入口
     - process_message/approve_auth_request 等域行为调用

4. 包 D: Orchestrator 构建器下沉（中风险）
   - 新增 `OrchestratorRunService`（或 `RunOrchestrationService`）。
   - 承接 `_process_message_v2_orchestrated` 内部闭包组装与 run_state 初始化模板。
   - core 仅保留 `run_service.execute_turn(message)` 式调用与结果落盘。

### 7.3.3 每包统一验收标准

1. 行为契约不变:
   - `process_message` 返回字段不变（response/tool_calls/tool_results/requires_auth 等）。
   - auth_required 恢复链路不变。
2. 回归门禁:
   - 必跑: context/auth/memory/agent_core 相关聚焦测试集。
   - 建议补充: 新 service 的单测覆盖率 >= 80%。
3. 可观测性不下降:
   - 日志关键字段持续包含 run_id/session_id/tool_name/source（适用处）。
4. 代码边界检查:
   - core 中新增逻辑必须是编排调用，不再引入新业务分支。

### 7.3.4 明确不做项（避免返工）

1. 不在本阶段改动对外 CLI/Channel 协议。
2. 不在本阶段重写 OrchestratorEngine 本体。
3. 不将兼容层（legacy config/zone alias）一次性移除，仅做标注与渐进退役。

## 8. 权限模型迁移（Zone -> need_auth）

## 8.1 迁移策略

1. 配置读取阶段兼容 zone 与 need_auth 同时存在。
2. 若仅有 zone，按映射规则自动填充 need_auth。
3. 若二者同时存在，以 need_auth 为准，zone 仅用于兼容告警。
4. vNext 文档中将 zone 标为 deprecated。

## 8.2 示例迁移脚本逻辑（伪代码）

```python
def migrate_tool_config(tool_cfg: dict) -> dict:
    if "need_auth" in tool_cfg:
        return tool_cfg

    zone = tool_cfg.get("zone", "C")
    tool_cfg["need_auth"] = zone == "C"
    tool_cfg.setdefault("metadata", {})
    tool_cfg["metadata"]["zone_migrated_from"] = zone
    return tool_cfg
```

## 9. 测试与验收矩阵

## 9.1 合同测试（Contract Tests）

1. 同一工具经 Native/OpenClaw/MCP 三来源规范化后字段一致性。
2. need_auth 判定一致性（含 zone 迁移场景）。
3. 失败语义一致性（tool not found/timeout/auth required）。

## 9.2 集成测试

1. OpenClaw 适配工具可被注册并执行。
2. MCP server 断连重连后工具仍可恢复调用。
3. core.py 拆分后 Phase5 回归路径不变（PLAN/ACT/OBSERVE/DECIDE）。

## 9.3 回归测试（必须保留）

1. 上下文 WARNING/CRITICAL 触发与提示行为。
2. no-progress replan-stop 行为。
3. auth_required 输出契约与恢复链路。

## 10. 里程碑排期（可执行版本）

0. P0.5（第 0-1 周，立即执行）
   - 仓库收口稳定化（编码、文案、资产落盘路径）
   - 修复 memory.sqlite 双落盘风险（仅允许写入 workspace_root/memory）
   - 验收: 全量测试通过，仓库根目录无运行时数据库落盘
1. M1（第 1-2 周）
   - ToolSpecV2 + NativeAdapter + ZoneMigration 兼容读取
   - 验收: 旧工具零改动可运行
   - 当前状态: 已完成（统一工具入口 ToolExecutionService 已接入 AgentCore，旧直连执行路径已收口）
2. M2（第 3-4 周）
   - OpenClawCompatibilityAdapter（高频工具）
   - 验收: 至少 10 个高频工具完成映射并通过合同测试
   - 附加门禁: 合同测试覆盖 native/openclaw/mcp 三来源字段一致性
   - 状态调整（2026-03-20）: 暂缓执行。原因是 Moltbot/OpenClaw 原生 tools 以运行时工厂与宿主依赖注入为主，简单 adapter 难以低风险接入。
3. M3（第 5-6 周）
   - MCP Bridge MVP（stdio + http）
   - 验收: 至少 2 个 MCP server 连通并可调用
4. M4（第 7-8 周）
   - core.py 服务化拆分
   - 验收: 核心回归通过，core.py 行数与复杂度达标

## 11. 风险与缓解

1. 风险: 兼容范围膨胀
   - 缓解: 分批白名单接入（先高频、后长尾）
2. 风险: MCP 不稳定影响主流程
   - 缓解: Bridge 隔离 + 熔断 + fallback
3. 风险: 拆 core 引入回归
   - 缓解: 先契约测试，再分步迁移，保持行为等价

## 12. 立即可执行的首批任务

1. 新增 ToolSpecV2 与 NativeAdapter（不改旧注册器接口）。
2. 在 ToolExecutionService 增加 normalize_to_v2()。
3. 增加 zone->need_auth 迁移器与配置告警。
4. 编写 3 组合同测试（native/openclaw/mcp 占位）。

完成以上 4 项后，CloseClaw 就具备了“保持轻量内核前提下扩生态”的真正起点。

## 13. 最新进展与下一步计划（2026-03-20）

### 13.1 本轮已完成

1. 全量回归通过:
   - `pytest -q` 结果: 306 passed, 1 skipped。
2. 遗留接口清理:
   - 移除 `AuthPermissionMiddleware` 中无效兼容参数 `admin_user_id`。
   - 同步修复测试调用与无用导入。
3. 时间模型迁移（测试层）:
   - 将测试代码中的 `datetime.utcnow()` 迁移为 timezone-aware 写法。
   - warning 从 510 降至 343（功能回归保持全绿）。

### 13.2 策略调整与判断

1. 调整结论: 先做 M3/M4，暂缓 M2。
2. 原因:
    - Moltbot/OpenClaw tools 不是纯静态 schema 映射，而是大量 `createXxxTool` 工厂 + 配置/会话/网关依赖（例如 channel 插件、gateway call、memory manager 等）。
    - 直接做“薄 adapter”会把上游运行时耦合带入内核，违反“复杂度外置、最小侵入”的硬约束。
3. 原则:
    - 先建设协议桥与内核服务边界（M3/M4），再回头做 M2 时以稳定边界承接，降低返工。

### 13.3 下一步执行计划（按新优先级）

1. M3-1（第一阶段）: MCP Bridge 骨架落地
    - 交付:
       - `closeclaw/mcp/bridge.py`
       - `closeclaw/mcp/client_pool.py`
       - `closeclaw/mcp/projection/tool_projector.py`
       - `closeclaw/mcp/transport/stdio_client.py`
       - `closeclaw/mcp/transport/http_client.py`
    - 范围:
       - 仅打通 tool 投影，不做 resource/prompt 执行。
    - 验收:
       - 1 个 mock MCP server 工具可注册、可执行、可回写 ToolResult。

2. M3-2（第二阶段）: MCP 与统一执行链合流
    - 交付:
       - 将 MCP 投影工具接入 `ToolExecutionService.normalize_to_v2()` 流程。
       - 增加 source=`mcp` 的统一 metadata 与审计字段。
    - 验收:
       - 合同测试覆盖 native/mcp 双来源一致性（字段、错误语义、need_auth 逻辑）。

3. M4-1（并行）: core.py 服务化拆分第一刀
    - 交付:
       - `PlanningService`（先抽消息构建与 LLM 调用）
       - `AuthService`（先抽授权请求持久化与恢复）
    - 验收:
       - 行为等价回归通过；`core.py` 只保留编排与依赖装配。

4. M4-2（收口）: ContextService 拆分与主循环收束
    - 交付:
       - `ContextService`（压缩、flush、memory retrieval 编排）
    - 验收:
       - core.py 行数与复杂度达到门禁目标；Phase5 路径回归通过。

5. M2 重启条件（延后触发）
    - 前提:
       - M3 tool 投影稳定。
       - M4 边界稳定（core 服务化完成）。
    - 启动方式:
       - 先做“OpenClaw 最小能力集”而非全量工具（仅 file/shell/web 高频子集）。

1. M2 启动: OpenClawCompatibilityAdapter（高优先级）
   - 目标: 打通 openclaw 来源工具到 ToolSpecV2 的标准化映射。
   - 交付:
     - `closeclaw/compatibility/adapters/openclaw_adapter.py`

### 13.4 M4 后续收尾计划（2026-03-20 更新）

1. M4-3: Tool schema 完整下沉（已完成）
   - 范围: `_format_tools_for_llm` -> `ToolSchemaService`。
   - 结果: core 不再包含参数 schema 归一化分支；对应 service 单测已补齐并通过。

2. M4-4: State 持久化服务化（已完成）
   - 范围: `_save_state` / `_restore_state` / `load_state_from_disk` -> `StateService`。
   - 结果: 状态落盘与恢复在 service 内闭环；pending auth 与 compact memory 恢复语义已下沉。

3. M4-5: Runtime 主循环服务化（本轮收尾完成）
   - 范围: `run` 内 auth wait + new message interrupt + output assembly -> `RuntimeLoopService`。
   - 结果:
     - 已下沉: 输出事件组装、auth/new-message wait race、auth approved/rejected/timeout/interrupted 输出分发。
     - 已补齐: `RuntimeLoopService` 对应单测与分支测试。
     - 回归: 扩展聚焦测试集 69 passed（warnings 为既有 utcnow 弃用告警）。

4. M4 关闭标准（修订）
   - 以职责清晰和行为稳定为主，不将 core 行数作为硬性门槛。
   - 满足以下条件即可关闭:
     - core 仅保留编排与依赖装配职责。
     - 核心回归稳定通过。
     - 新增 services 具备可独立单测与清晰边界。
   - 当前判定:
     - M4-3/M4-4/M4-5 已按“职责下沉 + 行为等价”完成本阶段目标。
     - 后续以常规增量重构维持边界，不再阻塞后续里程碑推进。

2. 合同测试补齐: 三来源一致性（native/openclaw/mcp 占位）
   - 目标: 固化“统一内核契约”防回归。
   - 交付:
     - `tests/test_toolspec_contract.py`（建议文件名）
     - 覆盖点: normalize 后字段一致、need_auth 判定一致、timeout/not-found/auth-required 语义一致。

3. M3 预备: MCP Bridge 最小骨架
   - 目标: 在不影响主流程前提下预留可运行桥接入口。
   - 交付:
     - `closeclaw/mcp/bridge.py`
     - `closeclaw/mcp/client_pool.py`
     - `closeclaw/mcp/projection/tool_projector.py`
   - 验收:
     - 至少 1 个本地 mock MCP server 工具可投影并调用。

4. warning 继续压降（并行进行）
   - 目标: 将剩余 `<string>:6/7/8` 类 `utcnow` 警告定位并消除。
   - 策略:
     - 优先定位动态构造消息/脚本路径中的 `utcnow`。
     - 保持“零行为变更”原则，只做时间 API 等价迁移。