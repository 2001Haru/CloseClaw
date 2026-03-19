"""Configuration system for CloseClaw."""

import os
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
import yaml

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
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
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
    require_auth_for_zones: list[str] = field(default_factory=lambda: ["C"])
    command_blacklist_enabled: bool = True
    custom_blacklist_rules: list[str] = field(default_factory=list)
    audit_log_enabled: bool = True
    audit_log_path: str = "audit.log"
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "admin_user_ids": self.admin_user_ids,
            "require_auth_for_zones": self.require_auth_for_zones,
            "command_blacklist_enabled": self.command_blacklist_enabled,
            "custom_blacklist_rules": self.custom_blacklist_rules,
            "audit_log_enabled": self.audit_log_enabled,
            "audit_log_path": self.audit_log_path,
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
class Phase5TelemetryConfig:
    """Phase 5 telemetry configuration."""
    enabled: bool = True
    log_actions: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "log_actions": self.log_actions,
        }


@dataclass
class Phase5RolloutConfig:
    """Phase 5 rollout configuration (P1 uses allowlist + kill-switch)."""
    mode: str = "session_allowlist"  # off | session_allowlist
    session_allowlist: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "session_allowlist": self.session_allowlist,
        }


@dataclass
class Phase5Config:
    """Phase 5 orchestrator controls."""
    max_steps: int = 6
    max_tokens_per_run: int = 120000
    max_wall_time_seconds: int = 45
    no_progress_limit: int = 2
    telemetry: Phase5TelemetryConfig = field(default_factory=Phase5TelemetryConfig)
    rollout: Phase5RolloutConfig = field(default_factory=Phase5RolloutConfig)

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
class CloseCrawlConfig:
    """Main CloseClaw configuration."""
    agent_id: str
    workspace_root: str
    llm: LLMConfig
    channels: list[ChannelConfig] = field(default_factory=list)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    max_iterations: int = 10
    timeout_seconds: int = 300
    system_prompt: Optional[str] = None
    max_context_tokens: int = 100000  # Default 100k, governs auto-compaction
    log_level: str = "INFO"
    state_file: str = "state.json"
    interaction_log_file: str = "interaction.md"
    context_management: ContextManagementConfig = field(default_factory=ContextManagementConfig)
    phase5: Phase5Config = field(default_factory=Phase5Config)
    
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
            "log_level": self.log_level,
            "state_file": self.state_file,
            "interaction_log_file": self.interaction_log_file,
            "context_management": self.context_management.to_dict(),
            "phase5": self.phase5.to_dict(),
        }


class ConfigLoader:
    """Load and validate configuration from YAML."""
    
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
        
        # Replace environment variable placeholders
        raw_config = ConfigLoader._replace_env_vars(raw_config)
        
        # Validate required fields
        ConfigLoader._validate_config(raw_config)
        
        # Build config objects
        config = ConfigLoader._build_config(raw_config)
        
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
    def _validate_config(raw_config: dict[str, Any]) -> None:
        """Validate required configuration fields."""
        required_fields = ["agent_id", "workspace_root", "llm"]
        for field in required_fields:
            if field not in raw_config:
                raise ValueError(f"Missing required config field: {field}")
        
        # Validate workspace_root exists
        workspace = raw_config.get("workspace_root")
        if not os.path.exists(workspace):
            raise ValueError(f"workspace_root does not exist: {workspace}")
        
        # Validate LLM config
        llm = raw_config.get("llm", {})
        if "provider" not in llm or "model" not in llm:
            raise ValueError("LLM config must have 'provider' and 'model'")
    
    @staticmethod
    def _build_config(raw_config: dict[str, Any]) -> CloseCrawlConfig:
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
        )
        
        # Channels
        channels = []
        for ch_raw in raw_config.get("channels", []):
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
        safety = SafetyConfig(
            admin_user_ids=safety_raw.get("admin_user_ids", []),
            require_auth_for_zones=safety_raw.get("require_auth_for_zones", ["C"]),
            command_blacklist_enabled=safety_raw.get("command_blacklist_enabled", True),
            custom_blacklist_rules=safety_raw.get("custom_blacklist_rules", []),
            audit_log_enabled=safety_raw.get("audit_log_enabled", True),
            audit_log_path=safety_raw.get("audit_log_path", "audit.log"),
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

        # Phase 5 config
        p5_raw = raw_config.get("phase5", {})
        p5_telemetry_raw = p5_raw.get("telemetry", {})
        p5_rollout_raw = p5_raw.get("rollout", {})

        phase5 = Phase5Config(
            max_steps=p5_raw.get("max_steps", 6),
            max_tokens_per_run=p5_raw.get("max_tokens_per_run", 120000),
            max_wall_time_seconds=p5_raw.get("max_wall_time_seconds", 45),
            no_progress_limit=p5_raw.get("no_progress_limit", 2),
            telemetry=Phase5TelemetryConfig(
                enabled=p5_telemetry_raw.get("enabled", True),
                log_actions=p5_telemetry_raw.get("log_actions", True),
            ),
            rollout=Phase5RolloutConfig(
                mode=p5_rollout_raw.get("mode", "session_allowlist"),
                session_allowlist=p5_rollout_raw.get("session_allowlist", []),
            ),
        )
        
        # Main config
        config = CloseCrawlConfig(
            agent_id=raw_config["agent_id"],
            workspace_root=raw_config["workspace_root"],
            llm=llm,
            channels=channels,
            safety=safety,
            max_iterations=raw_config.get("max_iterations", 10),
            timeout_seconds=raw_config.get("timeout_seconds", 300),
            system_prompt=raw_config.get("system_prompt"),
            max_context_tokens=context_management.max_tokens,
            log_level=raw_config.get("log_level", "INFO"),
            state_file=raw_config.get("state_file", "state.json"),
            interaction_log_file=raw_config.get("interaction_log_file", "interaction.md"),
            context_management=context_management,
            phase5=phase5,
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
  # User IDs permitted to approve Zone C operations
  admin_user_ids:
    - "user_id_here"
  
  # Which zones require authorization [A, B, C]
  require_auth_for_zones:
    - "C"
  
  # Enable command blacklist for shell operations
  command_blacklist_enabled: true
  
  # Custom regex patterns to block (e.g., additional dangerous commands)
  custom_blacklist_rules: []
  
  # Audit logging
  audit_log_enabled: true
  audit_log_path: "audit.log"

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
state_file: "state.json"
interaction_log_file: "interaction.md"
"""
        
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(example_config)
            logger.info(f"Example config created at {output_path}")
        except Exception as e:
            logger.error(f"Failed to create example config: {e}")
