"""Configuration system for CloseClaw."""

import os
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
import yaml

from .memory.workspace_layout import DEFAULT_STATE_FILE_REL, DEFAULT_AUDIT_LOG_REL

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    """LLM provider configuration."""
    provider: str  # "openai", "anthropic", "gemini", "ollama", etc.
    model: str  # e.g., "gpt-4", "claude-3-opus"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 2000
    timeout_seconds: int = 60
    thinking_enabled: Optional[bool] = None
    reasoning_effort: Optional[str] = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "thinking_enabled": self.thinking_enabled,
            "reasoning_effort": self.reasoning_effort,
        }


@dataclass
class ChannelConfig:
    """Channel configuration."""
    type: str  # "telegram", "feishu", "cli"
    enabled: bool = True
    token: Optional[str] = None
    webhook_url: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "enabled": self.enabled,
            "token": self.token,
            "webhook_url": self.webhook_url,
            "metadata": self.metadata,
        }


@dataclass
class SafetyConfig:
    """Safety and permission configuration."""
    admin_user_ids: list[str] = field(default_factory=list)
    # Backward-compatible alias for older tests/configs.
    enable_hitl: bool = True
    default_need_auth: bool = False
    security_mode: str = "supervised"
    consensus_guardian_timeout_seconds: float = 20.0
    consensus_guardian_prompt: Optional[str] = None
    consensus_guardian_provider: Optional[str] = None
    consensus_guardian_model: Optional[str] = None
    consensus_guardian_api_key: Optional[str] = None
    consensus_guardian_base_url: Optional[str] = None
    command_blacklist_enabled: bool = True
    command_policy_profile: str = "balanced"
    custom_blacklist_rules: list[str] = field(default_factory=list)
    os_sandbox_enabled: bool = True
    os_sandbox_fail_closed: bool = False
    os_sandbox_protected_tools: list[str] = field(default_factory=lambda: ["shell"])
    # Backward-compatible alias for older tests/configs.
    enable_audit_log: bool = True
    audit_log_enabled: bool = True
    audit_log_path: str = DEFAULT_AUDIT_LOG_REL
    audit_log_retention_days: int = 90
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "admin_user_ids": self.admin_user_ids,
            "enable_hitl": self.enable_hitl,
            "default_need_auth": self.default_need_auth,
            "security_mode": self.security_mode,
            "consensus_guardian_timeout_seconds": self.consensus_guardian_timeout_seconds,
            "consensus_guardian_prompt": self.consensus_guardian_prompt,
            "consensus_guardian_provider": self.consensus_guardian_provider,
            "consensus_guardian_model": self.consensus_guardian_model,
            "consensus_guardian_api_key": self.consensus_guardian_api_key,
            "consensus_guardian_base_url": self.consensus_guardian_base_url,
            "command_blacklist_enabled": self.command_blacklist_enabled,
            "command_policy_profile": self.command_policy_profile,
            "custom_blacklist_rules": self.custom_blacklist_rules,
            "os_sandbox_enabled": self.os_sandbox_enabled,
            "os_sandbox_fail_closed": self.os_sandbox_fail_closed,
            "os_sandbox_protected_tools": self.os_sandbox_protected_tools,
            "enable_audit_log": self.enable_audit_log,
            "audit_log_enabled": self.audit_log_enabled,
            "audit_log_path": self.audit_log_path,
            "audit_log_retention_days": self.audit_log_retention_days,
        }


@dataclass
class ContextManagementConfig:
    """Context and memory management configuration (Phase 4)."""
    max_tokens: int = 100000
    warning_threshold: float = 0.75
    critical_threshold: float = 0.95
    summarize_window: int = 50
    active_window: int = 10
    chunk_size: int = 5000
    retention_days: int = 90
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "warning_threshold": self.warning_threshold,
            "critical_threshold": self.critical_threshold,
            "summarize_window": self.summarize_window,
            "active_window": self.active_window,
            "chunk_size": self.chunk_size,
            "retention_days": self.retention_days,
        }


@dataclass
class OrchestratorTelemetryConfig:
    """Orchestrator telemetry configuration."""
    enabled: bool = True
    log_actions: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "log_actions": self.log_actions,
        }


@dataclass
class OrchestratorRolloutConfig:
    """Orchestrator rollout configuration (P1 uses allowlist + kill-switch)."""
    mode: str = "session_allowlist"  # off | session_allowlist
    session_allowlist: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "session_allowlist": self.session_allowlist,
        }


@dataclass
class OrchestratorConfig:
    """Orchestrator controls."""
    max_steps: int = 6
    max_tokens_per_run: int = 120000
    max_wall_time_seconds: int = 45
    no_progress_limit: int = 2
    telemetry: OrchestratorTelemetryConfig = field(default_factory=OrchestratorTelemetryConfig)
    rollout: OrchestratorRolloutConfig = field(default_factory=OrchestratorRolloutConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_tokens_per_run": self.max_tokens_per_run,
            "max_wall_time_seconds": self.max_wall_time_seconds,
            "no_progress_limit": self.no_progress_limit,
            "telemetry": self.telemetry.to_dict(),
            "rollout": self.rollout.to_dict(),
        }


@dataclass
class HeartbeatQuietHoursConfig:
    """Heartbeat quiet-hours gate configuration."""
    enabled: bool = False
    timezone: str = "UTC"
    ranges: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "timezone": self.timezone,
            "ranges": self.ranges,
        }


@dataclass
class HeartbeatQueueBusyGuardConfig:
    """Heartbeat queue busy guard configuration."""
    enabled: bool = False
    max_queue_size: int = 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_queue_size": self.max_queue_size,
        }


@dataclass
class HeartbeatRoutingConfig:
    """Heartbeat routing stabilization configuration."""
    target_ttl_s: int = 1800
    fallback_channel: str = "cli"
    fallback_chat_id: str = "direct"

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_ttl_s": self.target_ttl_s,
            "fallback_channel": self.fallback_channel,
            "fallback_chat_id": self.fallback_chat_id,
        }


@dataclass
class HeartbeatNotifyConfig:
    """Heartbeat notify behavior configuration."""
    enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
        }


@dataclass
class HeartbeatConfig:
    """Heartbeat configuration (Phase 6)."""
    enabled: bool = True
    interval_s: int = 1800
    quiet_hours: HeartbeatQuietHoursConfig = field(default_factory=HeartbeatQuietHoursConfig)
    queue_busy_guard: HeartbeatQueueBusyGuardConfig = field(default_factory=HeartbeatQueueBusyGuardConfig)
    routing: HeartbeatRoutingConfig = field(default_factory=HeartbeatRoutingConfig)
    notify: HeartbeatNotifyConfig = field(default_factory=HeartbeatNotifyConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_s": self.interval_s,
            "quiet_hours": self.quiet_hours.to_dict(),
            "queue_busy_guard": self.queue_busy_guard.to_dict(),
            "routing": self.routing.to_dict(),
            "notify": self.notify.to_dict(),
        }


@dataclass
class CronConfig:
    """Cron configuration (Phase 6)."""

    enabled: bool = False
    store_file: str = "cron_jobs.json"
    default_timezone: str = "UTC"

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "store_file": self.store_file,
            "default_timezone": self.default_timezone,
        }


@dataclass
class WebSearchConfig:
    """Web search provider configuration."""

    enabled: bool = False
    provider: str = "brave"
    brave_api_key: Optional[str] = None
    timeout_seconds: int = 30
    duckduckgo_min_interval_seconds: float = 2.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "brave_api_key": self.brave_api_key,
            "timeout_seconds": self.timeout_seconds,
            "duckduckgo_min_interval_seconds": self.duckduckgo_min_interval_seconds,
        }


@dataclass
class MemoryIndexConfig:
    """Lazy memory file indexing configuration."""

    lazy_sync_max_files_per_query: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "lazy_sync_max_files_per_query": self.lazy_sync_max_files_per_query,
        }


@dataclass
class CloseCrawlConfig:
    """Main CloseClaw configuration."""
    agent_id: str = "closeclaw-agent"
    workspace_root: str = "."
    llm: LLMConfig = field(default_factory=lambda: LLMConfig(provider="openai", model="gpt-4"))
    channels: list[ChannelConfig] = field(default_factory=list)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    max_iterations: int = 10
    timeout_seconds: int = 300
    system_prompt: Optional[str] = None
    max_context_tokens: int = 100000  # Default 100k, governs auto-compaction
    work_time_timezone: str = "UTC"
    log_level: str = "INFO"
    state_file: str = DEFAULT_STATE_FILE_REL
    interaction_log_file: str = "interaction.md"
    context_management: ContextManagementConfig = field(default_factory=ContextManagementConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    cron: CronConfig = field(default_factory=CronConfig)
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    memory_index: MemoryIndexConfig = field(default_factory=MemoryIndexConfig)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "workspace_root": self.workspace_root,
            "llm": self.llm.to_dict(),
            "channels": [ch.to_dict() for ch in self.channels],
            "safety": self.safety.to_dict(),
            "max_iterations": self.max_iterations,
            "timeout_seconds": self.timeout_seconds,
            "system_prompt": self.system_prompt,
            "max_context_tokens": self.max_context_tokens,
            "work_time_timezone": self.work_time_timezone,
            "log_level": self.log_level,
            "state_file": self.state_file,
            "interaction_log_file": self.interaction_log_file,
            "context_management": self.context_management.to_dict(),
            "orchestrator": self.orchestrator.to_dict(),
            "heartbeat": self.heartbeat.to_dict(),
            "cron": self.cron.to_dict(),
            "web_search": self.web_search.to_dict(),
            "memory_index": self.memory_index.to_dict(),
        }


class ConfigLoader:
    """Load and validate configuration from YAML."""

    @staticmethod
    def _resolve_state_file(raw_config: dict[str, Any]) -> str:
        """Resolve state file path with backward-compatible upgrade behavior."""
        raw_state_file = raw_config.get("state_file")
        if raw_state_file is None:
            return DEFAULT_STATE_FILE_REL

        state_file = str(raw_state_file).strip()
        legacy_defaults = {"state.json", "./state.json", ".\\state.json"}
        if state_file in legacy_defaults:
            logger.warning(
                "Detected legacy state_file=%s. Upgrading to %s.",
                state_file,
                DEFAULT_STATE_FILE_REL,
            )
            return DEFAULT_STATE_FILE_REL

        return state_file

    @staticmethod
    def _resolve_workspace_root(raw_config: dict[str, Any], config_path: Optional[str] = None) -> str:
        """Resolve workspace_root with explicit-only precedence.

        Precedence:
        1) Explicit config.workspace_root
        2) WORKSPACE_ROOT env var
        """
        workspace_root = raw_config.get("workspace_root") or os.environ.get("WORKSPACE_ROOT")
        if workspace_root:
            return os.path.abspath(workspace_root)
        raise ValueError(
            "workspace_root is required. Please set `workspace_root` in config.yaml "
            "or export WORKSPACE_ROOT."
        )
    
    @staticmethod
    def load(config_path: str) -> CloseCrawlConfig:
        """Load configuration from YAML file.
        
        Args:
            config_path: Path to config.yaml
            
        Returns:
            CloseCrawlConfig instance
        """
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        logger.info(f"Loading configuration from {config_path}")
        
        with open(config_path, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)

        if raw_config is None:
            raw_config = {}
        
        # Replace environment variable placeholders
        raw_config = ConfigLoader._replace_env_vars(raw_config)
        
        # Validate required fields
        ConfigLoader._validate_config(raw_config, config_path=config_path)
        
        # Build config objects
        config = ConfigLoader._build_config(raw_config, config_path=config_path)
        
        logger.info(f"Configuration loaded: agent_id={config.agent_id}")
        return config
    
    @staticmethod
    def _replace_env_vars(config: dict[str, Any]) -> dict[str, Any]:
        """Replace ${ENV_VAR} placeholders with environment variables."""
        config_str = yaml.dump(config)
        
        # Replace all ${VAR_NAME} with env var values
        import re
        def replace_env(match):
            var_name = match.group(1)
            default_value = match.group(2) or ""
            return os.environ.get(var_name, default_value)
        
        config_str = re.sub(r'\$\{([^:}]+)(?::([^}]*))?\}', replace_env, config_str)
        
        return yaml.safe_load(config_str)
    
    @staticmethod
    def _validate_config(raw_config: dict[str, Any], config_path: Optional[str] = None) -> None:
        """Validate required configuration fields."""
        required_fields = ["llm"]
        for field in required_fields:
            if field not in raw_config:
                raise ValueError(f"Missing required config field: {field}")

        workspace = ConfigLoader._resolve_workspace_root(raw_config, config_path=config_path)
        if not os.path.exists(workspace):
            raise ValueError(f"workspace_root does not exist: {workspace}")
        
        # Validate LLM config
        llm = raw_config.get("llm", {})
        if "provider" not in llm or "model" not in llm:
            raise ValueError("LLM config must have 'provider' and 'model'")
    
    @staticmethod
    def _build_config(raw_config: dict[str, Any], config_path: Optional[str] = None) -> CloseCrawlConfig:
        """Build CloseCrawlConfig from raw YAML."""
        
        # LLM config
        llm_raw = raw_config["llm"]
        llm = LLMConfig(
            provider=llm_raw["provider"],
            model=llm_raw["model"],
            api_key=llm_raw.get("api_key"),
            base_url=llm_raw.get("base_url"),
            temperature=llm_raw.get("temperature", 0.0),
            max_tokens=llm_raw.get("max_tokens", 2000),
            timeout_seconds=llm_raw.get("timeout_seconds", 60),
            thinking_enabled=llm_raw.get("thinking_enabled"),
            reasoning_effort=(
                str(llm_raw.get("reasoning_effort")).strip()
                if llm_raw.get("reasoning_effort") is not None
                else None
            ),
        )
        
        # Channels
        channels = []
        channels_raw = raw_config.get("channels", [])
        if isinstance(channels_raw, dict):
            channels_iter = channels_raw.values()
        else:
            channels_iter = channels_raw

        for ch_raw in channels_iter:
            channel = ChannelConfig(
                type=ch_raw["type"],
                enabled=ch_raw.get("enabled", True),
                token=ch_raw.get("token"),
                webhook_url=ch_raw.get("webhook_url"),
                metadata=ch_raw.get("metadata", {}),
            )
            channels.append(channel)
        
        # Safety config
        safety_raw = raw_config.get("safety", {})
        enable_hitl = safety_raw.get("enable_hitl")
        if enable_hitl is None:
            default_need_auth = bool(safety_raw.get("default_need_auth", False))
            enable_hitl = True
        else:
            default_need_auth = bool(safety_raw.get("default_need_auth", enable_hitl))

        enable_audit_log = bool(safety_raw.get("enable_audit_log", safety_raw.get("audit_log_enabled", True)))

        safety = SafetyConfig(
            admin_user_ids=safety_raw.get("admin_user_ids", []),
            enable_hitl=bool(enable_hitl),
            default_need_auth=default_need_auth,
            security_mode=str(safety_raw.get("security_mode", "supervised")),
            consensus_guardian_timeout_seconds=float(safety_raw.get("consensus_guardian_timeout_seconds", 20.0)),
            consensus_guardian_prompt=safety_raw.get("consensus_guardian_prompt"),
            consensus_guardian_provider=(
                str(safety_raw.get("consensus_guardian_provider")).strip()
                if safety_raw.get("consensus_guardian_provider") is not None
                else None
            ),
            consensus_guardian_model=(
                str(safety_raw.get("consensus_guardian_model")).strip()
                if safety_raw.get("consensus_guardian_model") is not None
                else None
            ),
            consensus_guardian_api_key=safety_raw.get("consensus_guardian_api_key"),
            consensus_guardian_base_url=safety_raw.get("consensus_guardian_base_url"),
            command_blacklist_enabled=safety_raw.get("command_blacklist_enabled", True),
            command_policy_profile=str(safety_raw.get("command_policy_profile", "balanced")),
            custom_blacklist_rules=safety_raw.get("custom_blacklist_rules", []),
            os_sandbox_enabled=bool(safety_raw.get("os_sandbox_enabled", True)),
            os_sandbox_fail_closed=bool(safety_raw.get("os_sandbox_fail_closed", False)),
            os_sandbox_protected_tools=[
                str(name).strip()
                for name in safety_raw.get("os_sandbox_protected_tools", ["shell"])
                if str(name).strip()
            ],
            enable_audit_log=enable_audit_log,
            audit_log_enabled=enable_audit_log,
            audit_log_path=safety_raw.get("audit_log_path", DEFAULT_AUDIT_LOG_REL),
            audit_log_retention_days=safety_raw.get("audit_log_retention_days", 90),
        )
        
        # Context management config (Phase 4)
        cm_raw = raw_config.get("context_management", {})
        legacy_max_tokens = raw_config.get("max_context_tokens")

        if legacy_max_tokens is not None and "max_tokens" not in cm_raw:
            # Backward-compatible path: use legacy field when context_management.max_tokens is absent.
            cm_raw = {**cm_raw, "max_tokens": legacy_max_tokens}
        elif legacy_max_tokens is not None and cm_raw.get("max_tokens") != legacy_max_tokens:
            logger.warning(
                "Config has both max_context_tokens and context_management.max_tokens with different values; "
                "using context_management.max_tokens as source of truth."
            )

        context_management = ContextManagementConfig(
            max_tokens=cm_raw.get("max_tokens", 100000),
            warning_threshold=cm_raw.get("warning_threshold", 0.75),
            critical_threshold=cm_raw.get("critical_threshold", 0.95),
            summarize_window=cm_raw.get("summarize_window", 50),
            active_window=cm_raw.get("active_window", 10),
            chunk_size=cm_raw.get("chunk_size", 5000),
            retention_days=cm_raw.get("retention_days", 90),
        )

        # Orchestrator config (legacy key: phase5)
        orchestrator_raw = raw_config.get("orchestrator")
        if orchestrator_raw is None:
            orchestrator_raw = raw_config.get("phase5", {})
            if "phase5" in raw_config:
                logger.warning("Detected legacy config key 'phase5'. Please rename it to 'orchestrator'.")
        orchestrator_telemetry_raw = orchestrator_raw.get("telemetry", {})
        orchestrator_rollout_raw = orchestrator_raw.get("rollout", {})

        orchestrator = OrchestratorConfig(
            max_steps=orchestrator_raw.get("max_steps", 6),
            max_tokens_per_run=orchestrator_raw.get("max_tokens_per_run", 120000),
            max_wall_time_seconds=orchestrator_raw.get("max_wall_time_seconds", 45),
            no_progress_limit=orchestrator_raw.get("no_progress_limit", 2),
            telemetry=OrchestratorTelemetryConfig(
                enabled=orchestrator_telemetry_raw.get("enabled", True),
                log_actions=orchestrator_telemetry_raw.get("log_actions", True),
            ),
            rollout=OrchestratorRolloutConfig(
                mode=orchestrator_rollout_raw.get("mode", "session_allowlist"),
                session_allowlist=orchestrator_rollout_raw.get("session_allowlist", []),
            ),
        )

        hb_raw = raw_config.get("heartbeat", {})
        hb_quiet_raw = hb_raw.get("quiet_hours", {})
        hb_queue_raw = hb_raw.get("queue_busy_guard", {})
        hb_routing_raw = hb_raw.get("routing", {})
        hb_notify_raw = hb_raw.get("notify", {})

        heartbeat = HeartbeatConfig(
            enabled=hb_raw.get("enabled", True),
            interval_s=hb_raw.get("interval_s", 1800),
            quiet_hours=HeartbeatQuietHoursConfig(
                enabled=hb_quiet_raw.get("enabled", False),
                timezone=hb_quiet_raw.get("timezone", "UTC"),
                ranges=hb_quiet_raw.get("ranges", []),
            ),
            queue_busy_guard=HeartbeatQueueBusyGuardConfig(
                enabled=hb_queue_raw.get("enabled", False),
                max_queue_size=hb_queue_raw.get("max_queue_size", 100),
            ),
            routing=HeartbeatRoutingConfig(
                target_ttl_s=hb_routing_raw.get("target_ttl_s", 1800),
                fallback_channel=hb_routing_raw.get("fallback_channel", "cli"),
                fallback_chat_id=hb_routing_raw.get("fallback_chat_id", "direct"),
            ),
            notify=HeartbeatNotifyConfig(
                enabled=hb_notify_raw.get("enabled", False),
            ),
        )

        cron_raw = raw_config.get("cron", {})
        cron = CronConfig(
            enabled=cron_raw.get("enabled", False),
            store_file=cron_raw.get("store_file", "cron_jobs.json"),
            default_timezone=cron_raw.get("default_timezone", "UTC"),
        )

        web_search_raw = raw_config.get("web_search", {})
        web_search = WebSearchConfig(
            enabled=web_search_raw.get("enabled", False),
            provider=web_search_raw.get("provider", "brave"),
            brave_api_key=web_search_raw.get("brave_api_key"),
            timeout_seconds=web_search_raw.get("timeout_seconds", 30),
            duckduckgo_min_interval_seconds=web_search_raw.get("duckduckgo_min_interval_seconds", 2.0),
        )

        memory_index_raw = raw_config.get("memory_index", {})
        memory_index = MemoryIndexConfig(
            lazy_sync_max_files_per_query=max(
                1,
                int(memory_index_raw.get("lazy_sync_max_files_per_query", 3)),
            ),
        )
        
        # Main config
        agent_raw = raw_config.get("agent", {})
        resolved_workspace_root = ConfigLoader._resolve_workspace_root(raw_config, config_path=config_path)

        config = CloseCrawlConfig(
            agent_id=raw_config.get("agent_id", agent_raw.get("id", "closeclaw-agent")),
            workspace_root=resolved_workspace_root,
            llm=llm,
            channels=channels,
            safety=safety,
            max_iterations=raw_config.get("max_iterations", agent_raw.get("max_iterations", 10)),
            timeout_seconds=raw_config.get("timeout_seconds", agent_raw.get("timeout_seconds", 300)),
            system_prompt=raw_config.get("system_prompt"),
            max_context_tokens=context_management.max_tokens,
            work_time_timezone=raw_config.get("work_time_timezone", "UTC"),
            log_level=raw_config.get("log_level", "INFO"),
            state_file=ConfigLoader._resolve_state_file(raw_config),
            interaction_log_file=raw_config.get("interaction_log_file", "interaction.md"),
            context_management=context_management,
            orchestrator=orchestrator,
            heartbeat=heartbeat,
            cron=cron,
            web_search=web_search,
            memory_index=memory_index,
        )
        
        return config
    
    @staticmethod
    def create_example_config(output_path: str) -> None:
        """Create an example config.yaml template."""
        example_config = """# CloseClaw Configuration
# Refer to this template to configure your CloseClaw agent

# Agent identifier
agent_id: "closeclaw-001"

# Workspace root directory (all file operations restricted to this)
workspace_root: "/path/to/workspace"

# LLM Configuration
# Supported providers: openai, anthropic, gemini, ollama, openai-compatible
llm:
  provider: "openai"  
  model: "gpt-4"
  api_key: ${OPENAI_API_KEY}  # Use environment variable
  temperature: 0.0
  max_tokens: 2000
  timeout_seconds: 60

# Optional web search provider settings
web_search:
    enabled: false
    provider: "brave"
    brave_api_key: ${BRAVE_SEARCH_API_KEY}
    timeout_seconds: 30
    duckduckgo_min_interval_seconds: 2.0

# Channels configuration (optional)
channels:
  - type: "telegram"
    enabled: true
    token: ${TELEGRAM_TOKEN}
    
  - type: "feishu"
    enabled: false
    token: ${FEISHU_TOKEN}
    webhook_url: ${FEISHU_WEBHOOK}
    
  - type: "cli"
    enabled: true

# Safety and permission controls
safety:
    # User IDs permitted to approve sensitive operations
  admin_user_ids:
    - "user_id_here"

    # New model: whether tools require authorization by default
    default_need_auth: false
  
  # Enable command blacklist for shell operations
  command_blacklist_enabled: true
  command_policy_profile: balanced

  # Optional guardian-only LLM override in consensus mode.
  # If missing/invalid, guardian falls back to the main llm settings.
  # consensus_guardian_provider: "gemini"
  # consensus_guardian_model: "gemini-3-flash"
  # consensus_guardian_api_key: ""
  # consensus_guardian_base_url: ""
  
  # Custom regex patterns to block (e.g., additional dangerous commands)
  custom_blacklist_rules: []
  # OS-level sandbox execution for selected tools
  os_sandbox_enabled: true
  os_sandbox_fail_closed: false
  os_sandbox_protected_tools: ["shell"]
  
  # Audit logging
  audit_log_enabled: true
    audit_log_path: "CloseClaw Memory/audit.log"

# Agent loop configuration
max_iterations: 10
timeout_seconds: 300

# System prompt (optional, overrides default)
system_prompt: |
  You are CloseClaw, a safe and precise AI assistant.
  Follow all security guidelines.
    Before answering questions that depend on past decisions, preferences, TODOs, or constraints,
    use retrieve_memory first and ground your answer in retrieved results.
    If memory is uncertain, say so clearly and ask a follow-up question.

# Logging
log_level: "INFO"  # DEBUG, INFO, WARNING, ERROR

# State persistence
state_file: "CloseClaw Memory/state.json"
interaction_log_file: "interaction.md"
"""
        
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(example_config)
            logger.info(f"Example config created at {output_path}")
        except Exception as e:
            logger.error(f"Failed to create example config: {e}")


