"""Multi-channel agent runner.

Starts AgentCore with all enabled channels concurrently via asyncio.gather.
Each channel gets its own AgentCore.run() loop, sharing the same AgentCore instance
for state consistency.

From Planning.md:
  "本地 CLI 实现：嵌入式 CLI 驱动，与 Server 共享同一个 AgentCore 实例，
   通过 asyncio.gather 同时启动 Server 和 CLI 循环。"

Usage:
    python -m closeclaw.runner --config config.yaml
"""

import argparse
import asyncio
import logging
import sys
from typing import Any, Optional

from .config import ConfigLoader, CloseCrawlConfig, ChannelConfig
from .agents import AgentCore
from .agents.task_manager import TaskManager
from .agents.llm_providers import create_llm_provider, OpenAICompatibleProvider
from .channels import CLIChannel, get_telegram_channel, get_feishu_channel
from .channels.base import BaseChannel
from .middleware import MiddlewareChain, SafetyGuard, PathSandbox, ZoneBasedPermission
from .tools.base import get_registered_tools
from .types import Zone, AgentConfig

logger = logging.getLogger(__name__)


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
        TelegramChannel = get_telegram_channel()
        token = ch_config.token
        if not token:
            raise ValueError("Telegram channel requires 'token' in config")
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
    agent_config = AgentConfig(
        model=config.llm.model,
        max_iterations=config.max_iterations,
        timeout_seconds=config.timeout_seconds,
        temperature=config.llm.temperature,
        system_prompt=config.system_prompt,
    )
    
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
    )
    
    # Setup middleware chain (three-layer security)
    middleware_chain = MiddlewareChain()
    
    if config.safety.command_blacklist_enabled:
        middleware_chain.add_middleware(
            SafetyGuard(custom_rules=config.safety.custom_blacklist_rules)
        )
    
    middleware_chain.add_middleware(PathSandbox(workspace_root=config.workspace_root))
    
    auth_zones = [Zone(z) for z in config.safety.require_auth_for_zones]
    middleware_chain.add_middleware(ZoneBasedPermission(require_auth_for_zones=auth_zones))
    
    agent.set_middleware_chain(middleware_chain)
    
    # Register all tools
    for tool in get_registered_tools():
        agent.register_tool(tool)
    
    # Setup TaskManager
    task_manager = TaskManager(state_file=config.state_file)
    agent.set_task_manager(task_manager)
    
    return agent


async def run_channel(agent: AgentCore, 
                      channel: BaseChannel,
                      config: CloseCrawlConfig) -> None:
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
        
        async def input_fn():
            """Wrap channel.receive_message to capture metadata."""
            nonlocal last_message_metadata
            msg = await channel.receive_message()
            if msg and hasattr(msg, 'metadata'):
                last_message_metadata = msg.metadata or {}
            return msg
        
        async def output_fn(response: dict[str, Any]) -> None:
            """Inject platform-specific metadata into response before sending."""
            # Inject _chat_id from last received message
            if "_chat_id" not in response and "_chat_id" in last_message_metadata:
                response["_chat_id"] = last_message_metadata["_chat_id"]
            await channel.send_response(response)
        
        await agent.run(
            session_id=session_id,
            user_id=user_id,
            channel_type=channel.channel_type.value,
            message_input_fn=input_fn,
            message_output_fn=output_fn,
        )
        
    except Exception as e:
        logger.error(f"Channel {channel.channel_type.value} error: {e}", exc_info=True)
    finally:
        await channel.stop()


async def run_agent(config_path: str, llm_provider: Any = None) -> None:
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
    
    logger.info(f"Starting CloseClaw agent: {config.agent_id}")
    
    # Create shared agent
    agent = create_agent(config, llm_provider)
    
    # Create channels
    channels = []
    for ch_config in config.channels:
        if not ch_config.enabled:
            logger.info(f"Channel {ch_config.type} is disabled, skipping")
            continue
        
        try:
            channel = create_channel(ch_config, config)
            channels.append(channel)
            logger.info(f"Channel created: {ch_config.type}")
        except Exception as e:
            logger.error(f"Failed to create channel {ch_config.type}: {e}")
    
    if not channels:
        logger.error("No channels enabled. Please enable at least one channel in config.yaml")
        return
    
    logger.info(f"Starting {len(channels)} channel(s) via asyncio.gather()")
    
    # Run all channels concurrently
    tasks = [run_channel(agent, ch, config) for ch in channels]
    await asyncio.gather(*tasks, return_exceptions=True)
    
    logger.info("All channels stopped. Agent shutting down.")


class _PlaceholderLLM:
    """Placeholder LLM for testing without a real API key.
    
    Returns a simple echo response. Replace with real LLM provider
    (OpenAI, Claude, etc.) for production use.
    """
    
    async def generate(self, messages, tools, **kwargs):
        last_message = messages[-1]["content"] if messages else "Hello"
        return (f"[Placeholder LLM] Echo: {last_message}", None)


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
    
    args = parser.parse_args()
    
    try:
        asyncio.run(run_agent(args.config))
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
