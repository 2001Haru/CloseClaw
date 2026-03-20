"""Channel base class - Abstract interface for all communication channels.

All channels (Telegram, Feishu, CLI) must implement this interface.
The interface is designed to plug directly into AgentCore.run():
  - receive_message() 鈫?message_input_fn
  - send_response() 鈫?message_output_fn
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from ..types import Message, ChannelType, AuthorizationResponse

logger = logging.getLogger(__name__)


class BaseChannel(ABC):
    """Abstract base class for communication channels.
    
    Each channel implementation must:
    1. Convert external messages 鈫?internal Message objects
    2. Convert internal responses 鈫?external format
    3. Handle HITL confirmation (need_auth tools) via channel-native UI
    4. Support async start/stop lifecycle
    
    Usage with AgentCore.run():
        channel = TelegramChannel(config)
        await agent.run(
            ...,
            message_input_fn=channel.receive_message,
            message_output_fn=channel.send_response,
        )
    """
    
    def __init__(self, channel_type: ChannelType, config: dict[str, Any] = None):
        """Initialize channel.
        
        Args:
            channel_type: Type identifier (telegram/feishu/cli)
            config: Channel-specific configuration from config.yaml
        """
        self.channel_type = channel_type
        self.config = config or {}
        self._running = False
    
    @property
    def is_running(self) -> bool:
        """Whether the channel is currently active."""
        return self._running
    
    @abstractmethod
    async def start(self) -> None:
        """Start the channel (begin listening for messages).
        
        This method should set self._running = True and begin
        processing incoming messages.
        """
        ...
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel gracefully.
        
        This method should set self._running = False and clean up
        any resources (connections, polling loops, etc).
        """
        ...
    
    @abstractmethod
    async def receive_message(self) -> Optional[Message]:
        """Receive the next user message (blocking wait).
        
        This is used as `message_input_fn` for AgentCore.run().
        
        Returns:
            Message object, or None if channel is shutting down.
        
        Behavior:
            - Blocks until a message is available
            - Returns None to signal the agent loop should stop
            - Converts platform-specific message format to Message
        """
        ...
    
    @abstractmethod
    async def send_response(self, response: dict[str, Any]) -> None:
        """Send a response to the user.
        
        This is used as `message_output_fn` for AgentCore.run().
        
        Args:
            response: Response dict from AgentCore, contains:
                - type: "response" | "auth_request" | "task_completed" | "error"
                - response: str (for type="response")
                - auth_request_id: str (for type="auth_request")
                - tool_name: str (for auth requests)
                - diff_preview: str (for file operation auth requests)
                - task_id: str (for type="task_completed")
                - error: str (for type="error")
        """
        ...
    
    @abstractmethod
    async def send_auth_request(self,
                                auth_request_id: str,
                                tool_name: str,
                                description: str,
                                diff_preview: Optional[str] = None) -> None:
        """Send HITL confirmation request to user.
        
        For sensitive operations, display the operation details and
        wait for user confirmation via channel-native UI:
        - Telegram: Inline Keyboard buttons
        - Feishu: Interactive Card buttons
        - CLI: [Y/n] prompt
        
        Args:
            auth_request_id: Unique ID for this auth request
            tool_name: Name of the tool requesting authorization
            description: Human-readable description of the operation
            diff_preview: Structured diff preview (for file operations)
        """
        ...
    
    @abstractmethod
    async def wait_for_auth_response(self, 
                                      auth_request_id: str,
                                      timeout: float = 300.0) -> Optional[AuthorizationResponse]:
        """Wait for user's authorization response.
        
        Args:
            auth_request_id: ID of the pending auth request
            timeout: Maximum wait time in seconds (default: 5 min)
        
        Returns:
            AuthorizationResponse if user responded, None if timeout
        """
        ...
    
    def _create_message(self,
                        message_id: str,
                        sender_id: str,
                        sender_name: str,
                        content: str,
                        **kwargs) -> Message:
        """Helper: Create a Message object from channel-specific data.
        
        Args:
            message_id: Platform message ID
            sender_id: Platform user ID
            sender_name: User display name
            content: Message text content
            **kwargs: Additional metadata
        
        Returns:
            Standardized Message object
        """
        return Message(
            id=message_id,
            channel_type=self.channel_type.value,
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            metadata=kwargs,
        )


