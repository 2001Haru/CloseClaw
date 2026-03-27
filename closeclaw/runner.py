"""Multi-channel agent runner.

Starts AgentCore with all enabled channels concurrently via asyncio.gather.
Each channel gets its own AgentCore.run() loop, sharing the same AgentCore instance
for state consistency.

Usage:
    python -m closeclaw.runner --config config.yaml
"""

import argparse
import asyncio
import logging
import sys
import time
from typing import Any, Literal, Optional
from pathlib import Path
import yaml

from .config import ConfigLoader, CloseCrawlConfig, ChannelConfig
from .agents import AgentCore
from .agents.task_manager import TaskManager
from .providers import create_llm_provider
from .channels import (
    CLIChannel,
    get_telegram_channel,
    get_feishu_channel,
    get_discord_channel,
    get_whatsapp_channel,
    get_qq_channel,
)
from .channels.base import BaseChannel
from .middleware import MiddlewareChain, SafetyGuard, PathSandbox, AuthPermissionMiddleware
from .safety import SecurityMode, normalize_security_mode, ConsensusGuardian
from .heartbeat import HeartbeatService
from .cron import CronService, set_runtime_cron_service
from .subagent import SubagentManager, set_runtime_subagent_manager
from .mcp import MCPBridge, MCPClientPool
from .mcp.transport import MCPHttpClient, MCPStdioClient
from .tools.base import get_registered_tools
from .tools.web_tools import configure_web_search
from .tools.shell_tools import configure_shell_sandbox
from .types import AgentConfig, ContextManagementSettings, LLMSettings, Message

logger = logging.getLogger(__name__)

RunMode = Literal["all", "agent", "gateway"]


def _is_channel_allowed_for_mode(channel_type: str, run_mode: RunMode) -> bool:
    """Check whether a channel type is allowed under startup mode."""
    normalized_type = channel_type.lower()

    if run_mode == "agent":
        return normalized_type == "cli"

    if run_mode == "gateway":
        return normalized_type != "cli"

    return True


def _build_gateway_startup_summary(channels: list[ChannelConfig]) -> list[str]:
    """Build user-facing startup summary for gateway mode."""
    if not channels:
        return [
            "[CloseClaw] Gateway mode started.",
            "[CloseClaw] Enabled channels: (none)",
        ]

    enabled_names = [ch.type.lower() for ch in channels]
    lines = [
        "[CloseClaw] Gateway mode started.",
        f"[CloseClaw] Enabled channels: {', '.join(enabled_names)}",
    ]

    for ch in channels:
        ch_type = ch.type.lower()
        metadata = ch.metadata or {}

        if ch_type == "feishu":
            host = str(metadata.get("webhook_host", "0.0.0.0"))
            port = int(metadata.get("webhook_port", 9000))
            lines.append(f"[CloseClaw] Feishu webhook: http://{host}:{port}")
        elif ch_type == "whatsapp":
            bridge_url = metadata.get("bridge_url") or ch.webhook_url
            if bridge_url:
                lines.append(f"[CloseClaw] WhatsApp bridge: {bridge_url}")
        elif ch_type == "telegram":
            lines.append("[CloseClaw] Telegram bot polling started.")
        elif ch_type == "discord":
            lines.append("[CloseClaw] Discord bot gateway connection started.")
        elif ch_type == "qq":
            lines.append("[CloseClaw] QQ bot gateway connection started.")

    return lines


def create_channel(ch_config: ChannelConfig, 
                   config: CloseCrawlConfig) -> BaseChannel:
    """Create a channel instance from config.
    
    Args:
        ch_config: Channel-specific configuration
        config: Full CloseClaw config (for admin_user_ids, etc.)
    
    Returns:
        Initialized BaseChannel instance
    """
    channel_type = ch_config.type.lower()
    admin_ids = config.safety.admin_user_ids
    
    if channel_type == "cli":
        return CLIChannel(
            user_id=admin_ids[0] if admin_ids else "cli_user",
            user_name="Local User",
            config=ch_config.to_dict(),
        )
    
    elif channel_type == "telegram":
        token = ch_config.token
        if not token:
            raise ValueError("Telegram channel requires 'token' in config")
        TelegramChannel = get_telegram_channel()
        return TelegramChannel(
            token=token,
            admin_user_ids=admin_ids,
            config=ch_config.to_dict(),
        )
    
    elif channel_type == "feishu":
        FeishuChannel = get_feishu_channel()
        app_id = ch_config.token  # Reuse token field for app_id
        app_secret = ch_config.webhook_url  # Reuse webhook_url for app_secret
        if not app_id or not app_secret:
            raise ValueError("Feishu channel requires 'token' (app_id) and 'webhook_url' (app_secret)")
        
        metadata = ch_config.metadata or {}
        return FeishuChannel(
            app_id=app_id,
            app_secret=app_secret,
            admin_user_ids=admin_ids,
            verification_token=metadata.get("verification_token", ""),
            webhook_port=metadata.get("webhook_port", 9000),
            config=ch_config.to_dict(),
        )

    elif channel_type == "discord":
        token = ch_config.token
        if not token:
            raise ValueError("Discord channel requires 'token' in config")

        DiscordChannel = get_discord_channel()
        return DiscordChannel(
            token=token,
            admin_user_ids=admin_ids,
            config=ch_config.to_dict(),
        )

    elif channel_type == "whatsapp":
        metadata = ch_config.metadata or {}
        bridge_url = metadata.get("bridge_url") or ch_config.webhook_url
        if not bridge_url:
            raise ValueError("WhatsApp channel requires metadata.bridge_url or webhook_url in config")

        WhatsAppChannel = get_whatsapp_channel()
        return WhatsAppChannel(
            bridge_url=bridge_url,
            admin_user_ids=admin_ids,
            bridge_token=metadata.get("bridge_token"),
            config=ch_config.to_dict(),
        )

    elif channel_type == "qq":
        app_id = ch_config.token
        app_secret = ch_config.webhook_url
        if not app_id or not app_secret:
            raise ValueError("QQ channel requires 'token' (app_id) and 'webhook_url' (app_secret)")

        QQChannel = get_qq_channel()
        return QQChannel(
            app_id=app_id,
            app_secret=app_secret,
            admin_user_ids=admin_ids,
            config=ch_config.to_dict(),
        )
    
    else:
        raise ValueError(f"Unknown channel type: {channel_type}")


def create_agent(config: CloseCrawlConfig, 
                 llm_provider: Any = None) -> AgentCore:
    """Create AgentCore with middleware, tools, and TaskManager.
    
    Args:
        config: Full CloseClaw config
        llm_provider: LLM provider instance (if None, auto-creates from config)
    
    Returns:
        Fully configured AgentCore
    """
    # Create AgentConfig
    llm_settings = LLMSettings(
        model=config.llm.model,
        provider=config.llm.provider,
        api_key=config.llm.api_key,
        base_url=config.llm.base_url,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
        timeout_seconds=config.llm.timeout_seconds,
    )
    
    context_mgmt_settings = ContextManagementSettings(
        max_tokens=config.context_management.max_tokens,
        warning_threshold=config.context_management.warning_threshold,
        critical_threshold=config.context_management.critical_threshold,
        summarize_window=config.context_management.summarize_window,
        active_window=config.context_management.active_window,
        chunk_size=config.context_management.chunk_size,
        retention_days=config.context_management.retention_days,
    )
    
    agent_config = AgentConfig(
        model=config.llm.model,
        max_iterations=config.max_iterations,
        timeout_seconds=config.timeout_seconds,
        temperature=config.llm.temperature,
        system_prompt=config.system_prompt,
        max_context_tokens=config.max_context_tokens,
        work_time_timezone=config.work_time_timezone,
        llm=llm_settings,
        context_management=context_mgmt_settings,
    )

    # Orchestrator config is carried via metadata to preserve AgentConfig compatibility.
    agent_config.metadata["orchestrator"] = {
        "max_steps": config.orchestrator.max_steps,
        "max_tokens_per_run": config.orchestrator.max_tokens_per_run,
        "max_wall_time_seconds": config.orchestrator.max_wall_time_seconds,
        "no_progress_limit": config.orchestrator.no_progress_limit,
        "telemetry": config.orchestrator.telemetry.to_dict(),
        "rollout": config.orchestrator.rollout.to_dict(),
    }
    
    # Auto-create LLM provider from config if not provided
    if llm_provider is None:
        if config.llm.api_key:
            llm_provider = create_llm_provider(
                provider=config.llm.provider,
                model=config.llm.model,
                api_key=config.llm.api_key,
                base_url=config.llm.base_url or "",
                temperature=config.llm.temperature,
                max_tokens=config.llm.max_tokens,
                timeout_seconds=config.llm.timeout_seconds,
            )
            logger.info(f"LLM provider created: {config.llm.provider}/{config.llm.model} "
                       f"(base_url={config.llm.base_url or 'default'})")
        else:
            logger.warning("No LLM api_key configured. Using placeholder LLM (echo mode).")
            llm_provider = _PlaceholderLLM()
    
    # Create agent
    agent = AgentCore(
        agent_id=config.agent_id,
        llm_provider=llm_provider,
        config=agent_config,
        workspace_root=config.workspace_root,
        admin_user_id=config.safety.admin_user_ids[0] if config.safety.admin_user_ids else None,
        state_file=config.state_file,
    )
    
    # Setup middleware chain (three-layer security)
    middleware_chain = MiddlewareChain()
    
    if config.safety.command_blacklist_enabled:
        middleware_chain.add_middleware(
            SafetyGuard(custom_rules=config.safety.custom_blacklist_rules)
        )
    
    middleware_chain.add_middleware(PathSandbox(workspace_root=config.workspace_root))
    
    security_mode = normalize_security_mode(config.safety.security_mode)
    consensus_guardian = None
    if security_mode == SecurityMode.CONSENSUS:
        consensus_guardian = ConsensusGuardian(
            llm_provider=llm_provider,
            prompt=config.safety.consensus_guardian_prompt,
            timeout_seconds=config.safety.consensus_guardian_timeout_seconds,
        )

    middleware_chain.add_middleware(
        AuthPermissionMiddleware(
            default_need_auth=config.safety.default_need_auth,
            security_mode=security_mode,
            consensus_guardian=consensus_guardian,
        )
    )
    
    agent.set_middleware_chain(middleware_chain)

    configure_web_search(
        enabled=config.web_search.enabled,
        provider=config.web_search.provider,
        brave_api_key=config.web_search.brave_api_key,
        timeout_seconds=config.web_search.timeout_seconds,
        duckduckgo_min_interval_seconds=config.web_search.duckduckgo_min_interval_seconds,
    )
    
    configure_shell_sandbox(
        workspace_root=config.workspace_root
    )
    
    # Register all tools
    for tool in get_registered_tools():
        agent.register_tool(tool)
    
    # Setup TaskManager
    task_manager = TaskManager(state_file=config.state_file)
    agent.set_task_manager(task_manager)

    # Setup runtime subagent manager used by spawn tool.
    subagent_manager = SubagentManager(
        task_manager=task_manager,
        llm_provider=llm_provider,
        tools_provider=agent._format_tools_for_llm,
        tool_executor=agent._process_tool_call,
        system_prompt_provider=agent._build_system_prompt,
    )
    set_runtime_subagent_manager(subagent_manager)
    
    return agent


async def run_channel(agent: AgentCore, 
                      channel: BaseChannel,
                      config: CloseCrawlConfig,
                      wake_queue: Optional[asyncio.Queue[Message]] = None) -> None:
    """Run a single channel with the shared AgentCore.
    
    Args:
        agent: Shared AgentCore instance
        channel: Channel to run
        config: Config for session parameters
    """
    try:
        await channel.start()
        
        # Create session ID for this channel
        session_id = f"{config.agent_id}_{channel.channel_type.value}"
        user_id = config.safety.admin_user_ids[0] if config.safety.admin_user_ids else "default"
        
        # Track the last received message's metadata for response routing
        # This is needed because AgentCore is channel-agnostic and doesn't
        # carry platform-specific metadata (like chat_id) through responses.
        last_message_metadata: dict[str, Any] = {}

        wake_bridge_task: Optional[asyncio.Task] = None

        if wake_queue is not None and hasattr(channel, "inject_message"):
            async def _bridge_wake_queue() -> None:
                while True:
                    wake_msg = await wake_queue.get()
                    try:
                        await channel.inject_message(wake_msg)  # type: ignore[attr-defined]
                    except Exception:
                        logger.exception("Failed to inject wake message into channel")

            wake_bridge_task = asyncio.create_task(_bridge_wake_queue())
        
        async def input_fn():
            """Wrap channel.receive_message to capture metadata."""
            nonlocal last_message_metadata
            # For channels that do not support inject_message, wake_queue must be
            # awaited concurrently with channel input; otherwise cron events can
            # be starved while receive_message is blocking.
            if wake_queue is not None and not hasattr(channel, "inject_message"):
                wake_task = asyncio.create_task(wake_queue.get())
                channel_task = asyncio.create_task(channel.receive_message())
                done, pending = await asyncio.wait(
                    [wake_task, channel_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                msg = next(iter(done)).result()
            else:
                msg = await channel.receive_message()

            if msg and hasattr(msg, "metadata"):
                incoming_metadata = dict(msg.metadata or {})
                incoming_chat_id = incoming_metadata.get("_chat_id")

                # Preserve the last known valid chat target when injected system
                # events (e.g. cron direct wake-ups) don't carry a concrete chat id.
                if incoming_chat_id in {None, "", "direct"}:
                    incoming_metadata.pop("_chat_id", None)

                if incoming_metadata:
                    last_message_metadata.update(incoming_metadata)
            return msg
        
        async def output_fn(response: dict[str, Any]) -> None:
            """Inject platform-specific metadata into response before sending."""
            # Inject _chat_id from last received message
            if "_chat_id" not in response and "_chat_id" in last_message_metadata:
                candidate_chat_id = last_message_metadata["_chat_id"]
                if candidate_chat_id not in {None, "", "direct"}:
                    response["_chat_id"] = candidate_chat_id
            try:
                await channel.send_response(response)
            except Exception as exc:
                # Output transport errors should not terminate the whole agent loop.
                logger.exception(
                    "Channel send_response failed (channel=%s): %s",
                    channel.channel_type.value,
                    exc,
                )
        
        async def auth_fn(auth_request_id: str, timeout: float):
            """Wait for user auth response via channel (e.g. Telegram inline button)."""
            auth_resp = await channel.wait_for_auth_response(auth_request_id, timeout)
            # If the channel provided a specific chat_id for this response, use it
            if auth_resp and hasattr(auth_resp, "metadata") and "_chat_id" in auth_resp.metadata:
                last_message_metadata["_chat_id"] = auth_resp.metadata["_chat_id"]
            return auth_resp
        
        await agent.run(
            session_id=session_id,
            user_id=user_id,
            channel_type=channel.channel_type.value,
            message_input_fn=input_fn,
            message_output_fn=output_fn,
            auth_response_fn=auth_fn,
        )

        if wake_bridge_task:
            wake_bridge_task.cancel()
            try:
                await wake_bridge_task
            except asyncio.CancelledError:
                pass
        
    except Exception as e:
        logger.error(f"Channel {channel.channel_type.value} error: {e}", exc_info=True)
    finally:
        if 'wake_bridge_task' in locals() and wake_bridge_task:
            wake_bridge_task.cancel()
            try:
                await wake_bridge_task
            except asyncio.CancelledError:
                pass
        await channel.stop()


async def _enqueue_cron_wake_message(
    *,
    wake_queues: dict[str, asyncio.Queue[Message]],
    job: Any,
) -> dict[str, Any]:
    """Inject a cron wake message into channel input queue for normal main-loop processing."""
    if not wake_queues:
        return {"queued": False, "reason": "no_active_channel_queue"}

    target_channel = str(getattr(job, "channel", "") or "").lower()
    if target_channel in wake_queues:
        route_channel = target_channel
    elif "cli" in wake_queues:
        route_channel = "cli"
    else:
        route_channel = next(iter(wake_queues.keys()))

    message = Message(
        id=f"cron_{getattr(job, 'id', 'job')}_{int(time.time() * 1000)}",
        channel_type=route_channel,
        sender_id="system",
        sender_name="System",
        content=str(getattr(job, "message", "wake_agent")),
        metadata={
            "role": "system",
            "source": "cron",
        },
    )

    target_to = str(getattr(job, "to", "")).strip()
    if target_to not in {"", "direct"}:
        message.metadata["_chat_id"] = target_to

    await wake_queues[route_channel].put(message)
    return {
        "queued": True,
        "channel": route_channel,
        "message_id": message.id,
    }


async def run_agent(
    config_path: str,
    llm_provider: Any = None,
    run_mode: RunMode = "all",
) -> None:
    """Main entry point: load config, create agent, start all enabled channels.
    
    Args:
        config_path: Path to config.yaml
        llm_provider: Optional LLM provider instance
    """
    # Load config
    config = ConfigLoader.load(config_path)
    
    # Setup logging
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    
    normalized_mode = (run_mode or "all").lower()
    if normalized_mode not in {"all", "agent", "gateway"}:
        raise ValueError(f"Invalid run_mode: {run_mode}")

    logger.info(f"Starting CloseClaw agent: {config.agent_id} (mode={normalized_mode})")
    
    # Create shared agent
    agent = create_agent(config, llm_provider)

    # Bootstrap MCP servers from config into runtime tool execution path.
    await _bootstrap_mcp_servers(agent, config_path)
    
    # Load global state from disk before taking any actions
    try:
        await agent.load_state_from_disk()
    except Exception as e:
        logger.error(f"Failed to initialize agent state from disk: {e}")
    
    # Create channels
    channels = []
    wake_queues: dict[str, asyncio.Queue[Message]] = {}
    for ch_config in config.channels:
        if not ch_config.enabled:
            logger.info(f"Channel {ch_config.type} is disabled, skipping")
            continue

        if not _is_channel_allowed_for_mode(ch_config.type, normalized_mode):
            logger.info(
                "Channel %s is excluded in %s mode, skipping",
                ch_config.type,
                normalized_mode,
            )
            continue
        
        try:
            channel = create_channel(ch_config, config)
            channels.append(channel)
            wake_queues[ch_config.type.lower()] = asyncio.Queue()
            logger.info(f"Channel created: {ch_config.type}")
        except Exception as e:
            logger.error(f"Failed to create channel {ch_config.type}: {e}")
    
    if normalized_mode == "gateway":
        gateway_configs = [
            ch for ch in config.channels
            if ch.enabled and _is_channel_allowed_for_mode(ch.type, normalized_mode)
        ]
        for line in _build_gateway_startup_summary(gateway_configs):
            print(line)

    if not channels:
        logger.error(
            "No channels enabled for mode=%s. Please update config.yaml or startup mode.",
            normalized_mode,
        )
        print(f"[CloseClaw] No channels enabled for mode={normalized_mode}. Please check config.yaml")
        return
    
    async def _heartbeat_execute(tasks: str) -> dict[str, Any]:
        # S1 MVP: keep heartbeat execution side-effect-free until direct-turn adapter lands in S2/S3.
        logger.info("Heartbeat run requested (S1 MVP noop execute), tasks_preview=%s", tasks[:120])
        return {"status": "noop", "tasks": tasks}

    async def _heartbeat_notify(payload: Any) -> None:
        logger.info("Heartbeat notify (S1 MVP): %s", str(payload)[:200])

    heartbeat_service = HeartbeatService(
        workspace_root=config.workspace_root,
        enabled=config.heartbeat.enabled,
        interval_s=config.heartbeat.interval_s,
        on_execute=_heartbeat_execute,
        on_notify=_heartbeat_notify,
        notify_enabled=config.heartbeat.notify.enabled,
        quiet_hours_enabled=config.heartbeat.quiet_hours.enabled,
        quiet_hours_timezone=config.heartbeat.quiet_hours.timezone,
        quiet_hours_ranges=config.heartbeat.quiet_hours.ranges,
        queue_busy_guard_enabled=config.heartbeat.queue_busy_guard.enabled,
        max_queue_size=config.heartbeat.queue_busy_guard.max_queue_size,
        target_ttl_s=config.heartbeat.routing.target_ttl_s,
        fallback_channel=config.heartbeat.routing.fallback_channel,
        fallback_chat_id=config.heartbeat.routing.fallback_chat_id,
    )

    async def _on_cron_job(job):
        logger.info("Cron job triggered id=%s message=%s", job.id, job.message[:120])
        enqueue_result = await _enqueue_cron_wake_message(
            wake_queues=wake_queues,
            job=job,
        )
        return {
            "status": "queued" if enqueue_result.get("queued") else "noop",
            "job_id": job.id,
            **enqueue_result,
        }

    cron_service = CronService(
        store_file=str((Path(config.workspace_root) / config.cron.store_file).resolve()),
        enabled=config.cron.enabled,
        default_timezone=config.cron.default_timezone,
        on_job=_on_cron_job,
    )

    set_runtime_cron_service(cron_service)

    await heartbeat_service.start()
    await cron_service.start()

    logger.info(f"Starting {len(channels)} channel(s) via asyncio.gather()")

    try:
        # Run all channels concurrently
        tasks = [
            run_channel(
                agent,
                ch,
                config,
                wake_queue=wake_queues.get(ch.channel_type.value),
            )
            for ch in channels
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("All channels stopped. Agent shutting down.")
    finally:
        await cron_service.stop()
        await heartbeat_service.stop()
        mcp_pool = getattr(agent, "_runtime_mcp_client_pool", None)
        if mcp_pool is not None:
            close_all = getattr(mcp_pool, "close_all", None)
            if callable(close_all):
                try:
                    await close_all()
                except Exception as exc:
                    logger.debug("Ignoring MCP pool close error on shutdown: %s", exc)
        set_runtime_cron_service(None)
        set_runtime_subagent_manager(None)


class _PlaceholderLLM:
    """Placeholder LLM for testing without a real API key.
    
    Returns a simple echo response. Replace with real LLM provider
    (OpenAI, Claude, etc.) for production use.
    """
    
    async def generate(self, messages, tools, **kwargs):
        last_message = messages[-1]["content"] if messages else "Hello"
        return (f"[Placeholder LLM] Echo: {last_message}", None)


def _load_mcp_servers_from_config(config_path: str) -> list[dict[str, Any]]:
    """Load mcp.servers from raw YAML config for runtime bootstrap."""
    config_file = Path(config_path)
    if not config_file.exists():
        return []

    with open(config_file, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    mcp_section = raw.get("mcp", {}) or {}
    servers = mcp_section.get("servers", []) or []
    if not isinstance(servers, list):
        return []
    return [s for s in servers if isinstance(s, dict)]


async def _bootstrap_mcp_servers(agent: AgentCore, config_path: str) -> list[str]:
    """Register projected MCP tools from configured servers into the agent runtime."""
    servers = _load_mcp_servers_from_config(config_path)
    if not servers:
        return []

    pool = MCPClientPool()
    setattr(agent, "_runtime_mcp_client_pool", pool)
    bridge = MCPBridge(pool)
    registered_tool_names: list[str] = []

    for server in servers:
        server_id = str(server.get("id", "")).strip()
        if not server_id:
            logger.warning("Skipping MCP server without id")
            continue

        transport = str(server.get("transport", "http")).strip().lower()
        try:
            if transport == "http":
                pool.register(
                    server_id,
                    client=MCPHttpClient(
                        base_url=str(server.get("base_url", "")).strip(),
                        endpoint=str(server.get("endpoint", "/mcp")),
                        timeout_seconds=float(server.get("timeout_seconds", 15.0)),
                        max_retries=int(server.get("max_retries", 2)),
                        retry_backoff_seconds=float(server.get("retry_backoff_seconds", 0.2)),
                    ),
                )
            elif transport == "stdio":
                pool.register(
                    server_id,
                    client=MCPStdioClient(
                        command=str(server.get("command", "")).strip(),
                        args=[str(a) for a in (server.get("args", []) or [])],
                        timeout_seconds=float(server.get("timeout_seconds", 30.0)),
                    ),
                )
            else:
                logger.warning("Skipping MCP server '%s' with unknown transport '%s'", server_id, transport)
                continue

            names = await bridge.sync_server_tools(server_id, agent.tool_execution_service)
            registered_tool_names.extend(names)
            logger.info("MCP server '%s' synced %d tool(s)", server_id, len(names))
        except Exception as exc:
            logger.warning("Failed to sync MCP server '%s': %s", server_id, exc)

    if registered_tool_names:
        logger.info("MCP bootstrap complete: %d projected tool(s) available", len(registered_tool_names))

    return registered_tool_names


def main():
    """CLI entry point for runner."""
    parser = argparse.ArgumentParser(
        prog="closeclaw-runner",
        description="Start CloseClaw agent with configured channels",
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["all", "agent", "gateway"],
        default="all",
        help="Startup mode: all | agent (CLI-only) | gateway (non-CLI only)",
    )
    
    args = parser.parse_args()
    
    try:
        asyncio.run(run_agent(args.config, run_mode=args.mode))
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()


