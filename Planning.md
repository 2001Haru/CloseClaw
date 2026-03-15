# CloseClaw项目计划书

### 我想要什么：A lightweight and safer Python Implementation of OpenClaw

#### 原因：我希望这个项目首先用易读的Python书写，这是AI领域最广泛的语言；其次是轻量化模块化，方便未来的研究与魔改；最后是安全且严谨的权限控制，我不希望closeclaw出现openclaw那样因为agent幻觉删光邮件的情况。

### 快速实现方式：参考OpenXJavis进行轻量化重构
*采用"外科手术式提取"——参考其架构但全新编写代码，不克隆遗留仓库*

### 实现计划

#### 重要原则
重点参考仓库CloseClaw/OpenXJavis，其次可以借鉴Nanobot/nanobot。只允许修改CloseClaw/CloseClaw仓库内容，不允许对其他仓库做任何修改！（如特殊情况必须提出请求）

#### 概述
基于 OpenXJavis 的架构，重构为轻量级、安全的 CloseClaw。保留核心功能（多渠道集成、Agent 循环、工具调用），但简化复杂性，增强安全控制。目标：代码量减少 50%，内存占用降低 30%，权限控制更严谨且轻量。

#### 核心组件重构
1. **Agent 核心**：
   - 简化 pi_agent 的循环引擎，移除复杂事件流，采用同步循环。
   - 保留工具调用，但增加权限检查层。
   - 集成信任域（Trust Zones）：Zone A (Safe) 自动执行，Zone B (Internal) 静默执行+日志，Zone C (Dangerous) 用户确认。
   - **实现方式**：使用装饰器（@tool(zone=Zone.C)）进行静态定义，结合 Middleware 进行运行时拦截。
   - **HITL + Diff 预览**：Zone C 操作触发两步流程 → (1) 生成结构化 Diff 预览（文件路径、操作类型、删除/新增行摘要），(2) 向用户发送 Inline Button 确认，用户审查后点击 Yes/No

2. **渠道集成**：
   - 保留 **Telegram**（国际标准）、**Feishu**（国内协同）、**本地 CLI**（开发者模式）。
   - 移除其他所有渠道以减轻包体积。
   - 简化媒体处理，聚焦文本消息。
   - **本地 CLI 实现**：嵌入式 CLI 驱动，与 Server 共享同一个 AgentCore 实例，通过 asyncio.gather 同时启动 Server 和 CLI 循环。

3. **LLM 提供商**：
   - 保留 OpenAI, Gemini, Claude 以及 OpenAI Compatible 的第三方 API，移除复杂聚合器。
   - 统一 API 接口，简化配置。
   - **YAML 配置方式**：支持多 LLM 配置，运行时切换，支持 API key 环境变量注入

4. **工具系统**：
   - 精简工具：文件 (读写查), WebSearch, Shell (原生执行)。
   - 移除一切具有"全局破坏性"的第三方 API 工具（如 Mail 插件）。
   - 工具按信任域分类。
   - 工具调用前进行权限预检，减少无效尝试。

5. **配置与工作区**：
   - 单仓库结构，无需依赖 pi-mono-python。
   - 使用标准 Python 包管理（pip），移除 uv 依赖。
   - **配置格式**：单 YAML 文件（支持注释，人类友好）。
   - **工作区定义**：用户在 config.yaml 中指定 `workspace_root`，所有文件操作强制限制在该目录内。
   - **目标平台**：Windows (优先) + Linux，原生执行，通过三层防护保障安全。
   - **零额外依赖部署**：无需 Docker、WSL2 等，用户开箱即用。

6. **安全与权限 - 三层防护**：
   - **第一道锁 - HITL（Human-in-the-Loop）**：任何系统变动操作（Zone C）必须挂起，向用户发送 Telegram Inline Button 或 CLI 提示，用户手动确认后才执行。采用"阻塞等待 + 按钮回调"机制，Agent 处于 WAITING_FOR_AUTH 状态直到收到特定 User ID 的确认。
   - **第二道锁 - 路径沙箱（Path Sandboxing）**：所有文件操作强制将相对路径转换为绝对路径，检查其是否以 `workspace_root` 开头。防止路径穿越攻击（如 `../../etc/passwd`）。
   - **第三道锁 - 命令黑名单（Instruction Blacklist）**：内置 Windows 高危命令库（如 `del /s`、`format`、`net user` 等），Shell 执行前进行正则扫描，发现风险关键词立刻中止。可扩展，支持用户自定义规则。
   - **审计日志**：所有操作（成功/失败/被拦截）记录到 audit.log，便于事后追踪。

#### 实施步骤
1. **Phase 1: 基础架构**（1-2 周）
   - **外科手术式提取**：不克隆 OpenXJavis 完整仓库（避免 Git 历史负担），参考关键文件：
     - 参考 `OpenXJavis/agents/runtime.py`：学习 Agent 循环引擎模式
     - 参考 `OpenXJavis/channels/telegram.py`：学习 Telegram 回调处理模式
     - 在 CloseClaw 仓库中**从零开始编写**全新代码（无历史包袱、无复制粘贴）
   - 设置单仓库结构（无需 pi-mono-python）。
   - 基础目录结构：`closeclaw/{agents, channels, tools, safety, config.py}`
   - 部署目标：Windows + Linux 原生支持，无 Docker 硬依赖。

2. **Phase 2: Agent 核心重构**（2-3 周）
   - **核心循环设计**：同步主循环 + 异步后台任务管理（混合模式）
     - 主循环同步运行，调试友好，易于维护
     - 长耗时操作（如网页爬虫、文件大量I/O）通过 `asyncio.create_task()` 丢到后台
     - 工具立即返回 task_id，Agent立即恢复响应用户
   - **TaskManager**：后台任务生命周期管理
     - 创建阶段：`create_task(tool_name, params)` → 返回 task_id（格式：#001、#002 等）
     - 轮询阶段：主循环每次迭代调用 `poll_results()`，检查是否有任务完成
     - 完成阶段：任务完成时，Agent自动通知用户结果（主动推送）
     - 持久化：state.json 保存活跃任务列表，Agent重启后可恢复
   - **HITL 确认流程**（立即确认模式）
     - Zone A/B：立即执行，异步返回结果，无需用户确认
     - Zone C：立即发送确认请求给用户（不等工具结果，因为结果可能已造成破坏）
       - 用户点击 Yes/No → 由 Telegram/Feishu/CLI 直接驱动执行
       - 一旦确认，操作立即下发到工具系统
   - **实现装饰器系统**：@tool(zone=Zone.A|B|C) 标注工具安全等级
   - **集成权限检查 Middleware**：运行时拦截，实现 Zone 分级执行策略
   - **Diff Preview 机制**（Zone C文件操作）：生成结构化 Diff 再请求确认
     - 格式：操作类型 | 文件路径 | 行号 | 删除/添加内容摘要（最多5行上下文）

3. **Phase 3: 渠道与工具**（2 周）
   - 实现精简渠道：Telegram + Feishu + 本地 CLI。
   - 本地 CLI 采用嵌入式驱动模式，与 Server 共享 AgentCore，避免网络延迟。
   - 重构工具系统（文件、WebSearch、Shell），集成三层防护机制。
   - Shell 工具：内置 Windows 命令黑名单，执行前正则扫描，拦截危险指令。
   - 文件工具：路径沙箱检查，强制限制在 workspace_root 内。

4. **Phase 4: 测试与优化**（1 周）
   - 端到端测试：每个信任域级别验证。
   - 性能优化：内存占用目标 < 50MB（不含容器），异步非阻塞设计。
   - 审计日志实现：所有操作记录到 audit.log。

5. **Phase 5: 文档与发布**（1 周）
   - 编写精简文档：README < 500 行，快速开始 + FAQ。
   - 提供示例配置文件（config.yaml 模板）。
   - 发布初始版本（pip 可安装）。

#### 核心设计决策总结：三层防护 + 零 Docker 部署

**第一道锁 - HITL（Human-in-the-Loop）+ Diff 预览**
- 所有 Zone C（危险）操作必须暂停，发送确认请求到 Telegram/Feishu/CLI
- 用户手动点击 Yes/No，特定 User ID 验证
- **文件修改 Diff 预览**：输出结构化预览（操作、路径、行号、删除/添加摘要），防止用户盲目确认
- 只有确认后才继续执行

**第二道锁 - 路径沙箱（Path Sandboxing）**
- 文件操作强制限制在 `workspace_root` 内
- 防止 `../../etc/passwd` 等路径穿越攻击

**第三道锁 - 命令黑名单（Instruction Blacklist）** 
- 内置 Windows 危险命令库（`del /s`、`format`、`net user` etc）
- Bash/Cmd 执行前正则扫描，拦截黑名单指令
- 可扩展，用户可自定义规则

**透明存储**
- `state.json`：机器可读状态
- `interaction.md`：人类可读的完整交互记录
- CLI `review` 命令：查看历史

**零 Docker 优势**
- 开箱即用，无需 Docker/WSL2
- 跨平台原生支持
- 性能更好，依赖更少

#### 轻量化实现检查清单

为了实现 CloseClaw 的轻量化目标（50% 代码缩减，30% 内存降低），需要严格执行以下检查清单：

**依赖管理**
- [x] 移除 pi-mono-python 依赖，自实现精简 Agent 循环引擎（不复用任何第三方 Agent 框架）
- [x] 最小依赖集：pydantic、httpx、fastapi（可选）、python-yaml
- [x] 避免重型框架（如 Celery、Kafka），使用原生 asyncio + asyncio.Queue
- [ ] TaskManager 基于原生 asyncio.create_task()，无需额外任务队列库 ⏳ Phase 2
- [x] 移除 TypeScript 编译依赖，纯 Python 实现
- [x] Shell 工具不依赖 docker-py（除非用户显式启用 Docker 模式，否则原生执行）

**代码组织 & 模块化**
- [x] Agent 核心循环：< 500 行代码（同步循环，不含TaskManager）
- [ ] TaskManager：< 300 行代码（任务创建、轮询、持久化） ⏳ Phase 2 实现
  - [ ] 源码位置：closeclaw/agents/task_manager.py ⏳ Phase 2
  - [ ] 关键方法：create_task()、poll_results()、get_status()、load_from_state()、save_to_state() ⏳ Phase 2
- [x] 工具系统：装饰器 + 元数据，模块化设计，只加载启用的工具
- [ ] 工具适配层：检测长耗时操作，自动转发给 TaskManager（vs 直接执行） ⏳ Phase 2
- [x] 渠道集成：Telegram/Feishu/CLI，每个 < 300 行代码
- [x] Middleware 系统：权限检查、日志记录、SafetyGuard（共 < 200 行）
- [ ] Diff 生成模块：Zone C 文件操作时，生成结构化 Diff 预览（操作类型、路径、行号、上下文摘要） ⏳ Phase 2
- [x] 避免继承链深度过大（< 3 层），使用 Protocol（鸭子类型）而非 ABC

**性能与资源**
- [x] 异步非阻塞设计：所有 I/O 操作 async/await
- [ ] 消息队列使用内存队列（asyncio.Queue），可选持久化 ⏳ Phase 2
- [x] 工具调用前进行权限预检，减少无效尝试
- [x] 定期 gc.collect() 清理循环引用，避免内存泄漏
- [x] 单实例内存目标：< 50MB（不含容器）

**功能精简**
- [x] 移除专属 Web UI，仅保留 FastAPI /docs 监控页
- [x] 状态持久化：Agent 重启后可恢复对话历史（state.json）
- [ ] 消息队列实现：内存队列 + 文件持久化，支持 Agent 重启恢复 ⏳ Phase 2

**安全防护 (三层模型)**
- [x] **第一层 HITL**：Zone C 操作暂停，发送确认请求到 Telegram/Feishu/CLI 
  - [x] 实现：asyncio 阻塞等待 + callback_query 响应，特定 User ID 验证
  - [x] 状态：Agent 处于 WAITING_FOR_AUTH 状态
  - [ ] **Diff 预览**：文件修改操作输出结构化 Diff（操作类型、文件路径、行号、删除/添加内容摘要，最多 5 行上下文） ⏳ Phase 2
- [x] **第二层路径沙箱**：所有文件操作相对路径 → 绝对路径 → workspace_root 校验
  - [x] 防止路径穿越（`../../etc/passwd`）
  - [x] 初始化验证：启动时检查 workspace_root 存在且可访问
- [x] **第三层命令黑名单**：内置 Windows 危险命令库，Shell 执行前正则扫描
  - [x] 黑名单示例：`del /s`, `format`, `net user`, `reg delete` 等
  - [x] 可扩展，支持用户自定义规则
  - [x] 日志记录：所有拦截事件到 audit.log

**透明存储与审计**
- [x] 状态持久化：`state.json`（机器可读，Agent 状态 + 消息历史）
  - [ ] 特别地：记录所有 active_tasks（task_id、tool、params、created_at、expires_after） ⏳ Phase 2
  - [ ] Agent 重启时通过 `task_manager.load_from_state()` 恢复 ⏳ Phase 2
- [x] 交互记录：`interaction.md`（人类可读，完整交互日志 + 确认流程）
  - [ ] 后台任务完成时自动追加到interaction.md ⏳ Phase 2
- [x] 审计日志：`audit.log`（所有操作记录：成功/失败/被拦截）
- [ ] CLI 命令扩展： ⏳ Phase 2
  - [ ] `closeclaw review` 查看历史记录 ⏳ Phase 2
  - [ ] `closeclaw tasks` 列出活跃后台任务及进度 ⏳ Phase 2
  - [ ] `closeclaw task <task_id>` 查询单个任务详情 ⏳ Phase 2

**构建与发布**
- [x] pyproject.toml（比 setup.py 轻量）
- [x] 排除测试文件、文档、示例在打包中
- [x] PEP 517-compliant 构建（pip/build 支持）
- [x] 最终包大小目标：< 5MB（含依赖 < 50MB）

**配置与部署**
- [x] 单 config.yaml 文件（< 100 行）
- [x] 所有敏感信息使用环境变量
- [ ] 提供默认配置模板，无需复杂 onboarding ⏳ 待补齐
- [x] CLI 快速启动：`closeclaw start` 或 `closeclaw --config config.yaml`
- [x] Windows + Linux 原生支持（无 Docker 硬依赖）

**文档**
- [x] README：精简版本（快速开始 + FAQ）
- [x] 无生成式 API 文档（依赖代码注释）
- [x] 常见问题集中在 Planning.md
- [ ] 配置示例：config.example.yaml ⏳ 待补齐

#### Phase 1 完成状态 ✅

| 类别 | 完成度 | 备注 |
|------|--------|------|
| 类型系统 | ✅ 100% | 已包含BackgroundTask类型供Phase 2使用 |
| Agent框架 | ✅ 95% | core.py已预留TaskManager集成接口 |
| 三层安全 | ✅ 100% | SafetyGuard, PathSandbox, ZoneBasedPermission完整 |
| 工具系统 | ✅ 90% | 装饰器、注册表完整；工具实现待Phase 2优化 |
| 配置系统 | ✅ 95% | 完整实现；示例文件待补齐 |
| 审计日志 | ✅ 100% | AuditLogger完整实现 |
| **总体** | ✅ **94%** | **Phase 2可顺利启动** |

#### Phase 1 → Phase 2 过渡准备 ✅

**已为TaskManager预留的接口：**
- [x] `BackgroundTask` 类型定义（包含status、result、metadata等）
- [x] `TaskStatus` 枚举（pending/running/completed/failed/cancelled）
- [x] `AgentCore.set_task_manager()` 集成接口
- [x] `AgentCore.poll_background_tasks()` 轮询接口
- [x] `AgentCore.create_background_task()` 创建接口
- [x] `AgentCore.run()` 主循环框架（期待Phase 2实现）

**Phase 2第一步（4小时内）：**
1. 创建 `closeclaw/agents/task_manager.py`
2. 实现 TaskManager 类（create_task, poll_results, load/save_from_state）
3. 在 Agent.run() 中集成 TaskManager
4. 验证后台任务创建和完成通知流程

#### 关键实现决策

**Agent 循环架构：同步主循环 + TaskManager 异步管理**
- **设计核心**：混合模式充分结合两个优势
  - 同步主循环：调试友好，易于理解和维护，避免事件链复杂度
  - TaskManager：处理长耗时任务，通过 `asyncio.create_task()` 后台执行
- **执行流程**：
  ```
  用户输入 → Agent同步处理 → 检测耗时工具调用
      ↓
  工具不直接执行 → 交由TaskManager → asyncio.create_task()
      ↓
  工具立即返回 task_id（如"#001") → Agent继续循环
      ↓
  主循环每轮调用 poll_results() → 检查后台任务完成情况
      ↓
  任务完成 → Agent主动推送结果到用户
  ```
- **HITL 时序**（三个用户决策）
  - **决策1：立即确认模式**：Zone C 不等结果就请求用户
    - 理由：Zone C 命令都很危险，输出结果可能来不及反悔
    - 实现：操作下发前立即挂起 → WAITING_FOR_AUTH → 等待用户回调
  - **决策2：任务超时处理**：保留任务，让用户自己关心
    - 理由：假设用户会自己管理长耗时任务
    - 实现：不自动杀死，记录在 state.json，支持用户查询进度
  - **决策3：任务持久化**：完整持久化活跃任务列表
    - 理由：稳健性（Agent重启后恢复任务表）
    - 实现：state.json 包含所有活跃任务 + 完成结果缓存

**Python 版本支持**
- 目标：**Python 3.10+**
- 理由：Type hints 语法清晰，足够覆盖大部分用户

**Docker 依赖**
- **完全可选**（不是硬依赖）
- 默认本地原生执行
- 通过三层防护（HITL + 路径沙箱 + 命令黑名单）提供等同于容器隔离的安全性，甚至更严谨（CloseClaw > OpenClaw）
- 高级用户可选择启用 Docker 模式获得额外隔离

**实施时间表**
- **直接启动 Phase 1-3**（核心功能优先）
- 预计 4-5 周完成可用版本（MVP）
- Phase 4-5 作为后期优化迭代

#### 风险与缓解策略

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 重构引入新 Bug | 功能不稳定 | 逐步迁移，保持兼容测试，每 Phase 端到端验证 |
| HITL 确认降低自动化 | 用户体验 | Zone A 自动执行，减少频繁确认，CLI 快捷批准 |
| Diff 预览单次超大 | 审计信息爆炸 | 限制上下文行数（最多 5 行），超大文件操作强制拆分 |
| 用户审计疲劳 | 安全失效 | 提供" Diff 摘要"而非完整内容，关键数据突显，支持"批量审查" |
| 从零编写遗漏架构 | 功能缺陷 | 详细参考文档（agents/runtime.py、channels/telegram.py），Phase 1 设计评审 |
| 黑名单维护成本高 | 长期可维护性 | 内置常见黑名单（Windows），支持用户扩展，社区贡献 |
| 路径沙箱误伤 | 功能受限 | 详细文档 + symlink 处理 + 显式白名单机制 |
| 多渠道消息同步 | 状态一致性 | 中央消息队列，分布式锁确保原子操作 |
| 审计日志爆炸 | 性能和存储 | 日志轮换（日级），定期归档，可配置日志级别 |

---

## 📋 关键澄清与常见问题

### "重构 vs 外科手术式提取"有什么区别？
- **重构（Refactor）传统含义**：在现有代码基础上改造
- **CloseClaw 做法**：采用"外科手术式提取"
  - 只**参考** OpenXJavis 的 `agents/runtime.py` 和 `channels/telegram.py` 来理解架构
  - 在 CloseClaw 仓库中**从零编写全新代码**（不复制粘贴）
  - **优势**：干净的 Git 历史，无遗留负担，设计更为刻意

### "同步循环容易被卡住"怎么解决？
- **问题**：某个工具耗时 100s，Agent主循环被阻塞
- **OpenXJavis 解决**：事件流 + 异步，但调试复杂
- **CloseClaw 解决**：**混合异步模型**
  - 主循环保持同步（简单调试）
  - TaskManager 使用 `asyncio.create_task()` 后台执行长耗时任务
  - Agent立即返回 task_id（"#001"），继续处理其他消息
  - 主循环每轮调用 `poll_results()` 检查后台完成，发现后主动推送结果
  - **效果**：同步的易调试性 + 异步的不阻塞

### "任务超时怎么处理"为什么不自动杀死？
- **用户决策**：保留任务，让用户自己关心
- **理由**：
  - 某些合理的长耗时任务（如大数据处理），用户可能希望继续等待
  - 自动杀死违反了"透明"原则，用户不知道为什么任务消失
  - 提供查询界面（`closeclaw tasks`），让用户可随时查看进度
- **支持语义**：用户可自己调用 `cancel_task(#001)` 来中止

### "活跃任务列表为什么要持久化"？
- **用户决策**：完整持久化，确保稳健性
- **场景**：
  - Agent 进程不慎崩溃 / Agent 主动重启
  - 用户期望后台爬虫继续运行，重启后能查看进度
- **实现**：state.json 嵌入 active_tasks 字段，启动时 `load_from_state()` 恢复
- **优势**：
  - Agent 是可重启的 ✅
  - 用户不会丢失任务进度 ✅
  - 符合"透明且稳健"的设计哲学 ✅

### Diff Preview 何时触发？
- **触发条件**：任何 Zone C（危险）的**文件修改**操作
- **显示时机**：生成 Diff 后，**立即发送给用户**，用户审查后点击 Yes/No
- **实现阶段**：**Phase 2**（Agent 核心重构时集成入 HITL 机制）
- **格式示例**：
  ```
  文件：config.yaml  |  操作：修改
  ─────────────────────
  - old_value = "xxx"
  + new_value = "yyy"
  ─────────────────────
  确认？ [是] [否]
  ```

### Phase 1 参考文件是完整参考吗？
- **主要参考**：
  - `OpenXJavis/agents/runtime.py` → 学习 Agent 循环模式
  - `OpenXJavis/channels/telegram.py` → 学习 Telegram 回调处理
- **其他文件**：可视需要参考，但不强制
- **关键原则**：**理解而非复制**，CloseClaw 代码应当独立、易读、轻量

### 为什么移除了"会话迁移"和"历史压缩"？
- 这两项在前面核心需求中未提及，被判定为"超出 MVP 范围"
- **替换方案**：使用简单的 JSON 状态持久化 + Markdown 交互记录足以满足需求
- **未来可选**：待 MVP 验证后，作为 Phase 4+ 的优化迭代

### Phase 时间表是紧张吗？
- **4-5 周 = MVP 目标**（Phase 1-3，包括基本的 HITL + Diff Preview）
- **Phase 4-5 为可选后续**（性能优化、文档完善）
- **每 Phase 有 1-2 天缓冲**，用于修复意外问题

### "同步主循环 + 异步后台"能真的解决Agent被卡的问题吗？

**问题背景**：  
- 纯同步循环：耗时工具（爬虫、大文件处理）会导致Agent被阻塞，无法处理新消息
- 纯事件流：架构复杂，调试困难，容易产生竞态条件

**CloseClaw 解决方案**：混合异步模型
```
用户输入 → Agent同步处理 → 检测耗时 
  ↓
TaskManager.create_task() → asyncio.create_task() ← 后台异步运行
  ↓
Agent立即恢复 → 立即响应用户 → 继续处理其他消息
  ↓
每循环调用poll_results() → 检查任务完成 → 主动推送结果
```

**验证**：  
- ✅ Agent不被阻塞（主循环保持同步）
- ✅ 后台任务并发运行（asyncio原生支持）
- ✅ 调试简单（同步主循环易理解）
- ✅ 扩展灵活（asyncio.create_task()是标准异步模式）

**局限性说明**：  
- 如果有 1000 个并发任务，内存可能会紧张 → 但实际使用中不太可能
- 如果后台任务间有复杂依赖 → 需要TaskManager增强调度逻辑
- 都属于"优化而非重设计"范围

### "任务保留而不自动杀死超时"会不会导致僵尸进程爆炸？

**用户决策**：保留任务，让用户自己管理

**保障措施**：
- ✅ 持久化active_tasks：state.json记录所有active任务
- ✅ 提供管理界面：`closeclaw tasks` 查看进度、`closeclaw cancel <id>` 中止任务
- ✅ 审计日志：audit.log记录所有任务状态变更
- ✅ 用户透明：用户始终知道哪些任务在后台运行

**为什么这样设计**：
- 大数据处理可能需要 1 小时+ → 不能自动杀死
- 用户希望能选择"让它跑着"还是"立即中止"
- 符合"透明"设计哲学

### "立即确认"为什么比"结果后确认"更安全？

**两种方案对比**：

| 对比维度 | 立即确认 | 结果后确认 |
|---------|--------|----------|
| Zone C 文件删除 | ⚠️ Agent: "我要删 /data/config.yaml，确认吗？" → 用户Yes/No | ⚠️ Agent: "已删除 /data/config.yaml" → 用户: "不行！" |
| 安全性 | 🟢 用户在操作前决策 | 🔴 来不及反悔 |
| 用户体验 | 🟡 需要频繁确认 | 🟢 提前看到结果更有依据 |
| 关键数据删除 | ✅ 可以拦截 | ❌ 已经删除，无法恢复 |

**推荐**：Zone C采用**立即确认**
- 理由：Zone C = 危险操作（文件删除、命令执行等），无法"事后恢复"
- Diff Preview补偿：生成详细的"删除内容摘要"，让用户充分了解

**可选增强**：
- Zone B 可考虑"结果后日志确认"（记录下来让用户事后查看）
- Zone A 完全不需要确认

### "状态持久化"具体怎么实现和恢复？

**持久化策略**：
```bash
开启Agent → 启动前加载state.json → 恢复所有active_tasks
  ↓
main_loop:
  1. poll_results() → 检查完成的任务
  2. 有任务完成 → 推送给用户
  3. 新建任务 → TaskManager.create_task()
  4. 循环末尾：save_to_state() → 持久化当前状态
  ↓
Agent关闭 → state.json已包含所有活跃任务
  ↓
下次启动 → 恢复这些任务 → 继续处理
```

**state.json 结构**：
```json
{
  "version": "0.1",
  "agent_state": "running",
  "last_save_time": "2026-03-15T10:30:00Z",
  "active_tasks": {
    "#001": {
      "tool": "web_search",
      "params": {"query": "weather in NYC"},
      "created_at": "2026-03-15T10:00:00Z",
      "expires_after": 3600
    }
  },
  "message_history": [...],
  "completed_results": {...}
}
```

**恢复流程**：
- 启动时：`task_manager.load_from_state()` → 重建 active_tasks 字典
- 检查任务状态：已完成 → 从active移到completed；正在运行 → 继续后台执行
- 新消息基于"最后一条消息ID"继续

### "Phase 2 工作顺序"为什么要优先TaskManager？

**优先级排序**：

| 优先级 | 组件 | 理由 |
|--------|------|------|
| ⚡ 最高 | TaskManager | 是后台处理的基础，其他都依赖它 |
| 🔥 高 | Agent主循环 | TaskManager创建好后要立即集成 |
| 📌 中 | 工具适配 | 检测耗时、转发给TaskManager |
| 🔧 中 | 状态持久化 | 确保任务不丢失 |
| 🛠️ 低 | CLI扩展 | UI层，用户管理任务的界面 |
| ✅ 最后 | 测试验证 | 前面都完后再全量测试 |

**总工时估算**：18 小时（2-3 天）
