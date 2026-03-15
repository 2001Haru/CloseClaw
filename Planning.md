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
   - 简化循环引擎：从异步事件流改为同步循环（< 500 行代码）。
   - 实现装饰器系统：@tool(zone=Zone.A|B|C) 标注工具安全等级。
   - 集成权限检查 Middleware：运行时拦截，实现 Zone 分级执行策略。
   - 实现 HITL 确认流程：Zone C 操作发送确认消息到 Telegram/Feishu/CLI，等待用户响应。
   - **实现 Diff Preview 机制**：Zone C 文件修改时，生成结构化 Diff 预览再请求确认（关键安全特性）。

3. **Phase 3: 渠道与工具**（2 周）
   - 实现精简渠道：Telegram + Feishu + 本地 CLI（每个 < 300 行代码）。
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
- [ ] 移除 pi-mono-python 依赖，自实现精简 Agent 循环引擎（不复用任何第三方 Agent 框架）
- [ ] 最小依赖集：pydantic、httpx、fastapi（可选）、python-yaml
- [ ] 避免重型框架（如 Celery、Kafka），使用原生 asyncio + asyncio.Queue
- [ ] 移除 TypeScript 编译依赖，纯 Python 实现
- [ ] Shell 工具不依赖 docker-py（除非用户显式启用 Docker 模式，否则原生执行）

**代码组织 & 模块化**
- [ ] Agent 核心循环：< 500 行代码（同步循环优于异步）
- [ ] 工具系统：装饰器 + 元数据，模块化设计，只加载启用的工具
- [ ] 渠道集成：Telegram/Feishu/CLI，每个 < 300 行代码
- [ ] Middleware 系统：权限检查、日志记录、SafetyGuard（共 < 200 行）
- [ ] Diff 生成模块：Zone C 文件操作时，生成结构化 Diff 预览（操作类型、路径、行号、上下文摘要）
- [ ] 避免继承链深度过大（< 3 层），使用 Protocol（鸭子类型）而非 ABC

**性能与资源**
- [ ] 异步非阻塞设计：所有 I/O 操作 async/await
- [ ] 消息队列使用内存队列（asyncio.Queue），可选持久化
- [ ] 工具调用前进行权限预检，减少无效尝试
- [ ] 定期 gc.collect() 清理循环引用，避免内存泄漏
- [ ] 单实例内存目标：< 50MB（不含容器）

**功能精简**
- [ ] 移除专属 Web UI，仅保留 FastAPI /docs 监控页
- [ ] 状态持久化：Agent 重启后可恢复对话历史（state.json）
- [ ] 消息队列实现：内存队列 + 文件持久化，支持 Agent 重启恢复

**安全防护 (三层模型)**
- [ ] **第一层 HITL**：Zone C 操作暂停，发送确认请求到 Telegram/Feishu/CLI 
  - 实现：asyncio 阻塞等待 + callback_query 响应，特定 User ID 验证
  - 状态：Agent 处于 WAITING_FOR_AUTH 状态
  - **Diff 预览**：文件修改操作输出结构化 Diff（操作类型、文件路径、行号、删除/添加内容摘要，最多 5 行上下文）
- [ ] **第二层路径沙箱**：所有文件操作相对路径 → 绝对路径 → workspace_root 校验
  - 防止路径穿越（`../../etc/passwd`）
  - 初始化验证：启动时检查 workspace_root 存在且可访问
- [ ] **第三层命令黑名单**：内置 Windows 危险命令库，Shell 执行前正则扫描
  - 黑名单示例：`del /s`, `format`, `net user`, `reg delete` 等
  - 可扩展，支持用户自定义规则
  - 日志记录：所有拦截事件到 audit.log

**透明存储与审计**
- [ ] 状态持久化：`state.json`（机器可读，Agent 状态 + 消息历史）
- [ ] 交互记录：`interaction.md`（人类可读，完整交互日志 + 确认流程）
- [ ] 审计日志：`audit.log`（所有操作记录：成功/失败/被拦截）
- [ ] CLI 命令：`closeclaw review` 查看历史记录

**构建与发布**
- [ ] pyproject.toml（比 setup.py 轻量）
- [ ] 排除测试文件、文档、示例在打包中
- [ ] PEP 517-compliant 构建（pip/build 支持）
- [ ] 最终包大小目标：< 5MB（含依赖 < 50MB）

**配置与部署**
- [ ] 单 config.yaml 文件（< 100 行）
- [ ] 所有敏感信息使用环境变量
- [ ] 提供默认配置模板，无需复杂 onboarding
- [ ] CLI 快速启动：`closeclaw start` 或 `closeclaw --config config.yaml`
- [ ] Windows + Linux 原生支持（无 Docker 硬依赖）

**文档**
- [ ] README：< 500 行（快速开始 + FAQ）
- [ ] 无生成式 API 文档（依赖代码注释）
- [ ] 常见问题集中在 FAQ.md
- [ ] 配置示例：config.example.yaml

#### 关键实现决策

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
