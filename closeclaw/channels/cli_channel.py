"""Embedded CLI channel - Interactive terminal-based channel.

Shares the same AgentCore instance with other channels.
Provides stdin/stdout based message exchange and HITL confirmation.

From Planning.md:
  "本地 CLI 实现：嵌入式 CLI 驱动，与 Server 共享同一个 AgentCore 实例，
   通过 asyncio.gather 同时启动 Server 和 CLI 循环。"
"""

import asyncio
import logging
import sys
from typing import Any, Optional
from datetime import datetime

from .base import BaseChannel
from ..types import Message, ChannelType, AuthorizationResponse

logger = logging.getLogger(__name__)

# ANSI color codes
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"
    DIM = "\033[2m"


class CLIChannel(BaseChannel):
    """Interactive CLI channel for local development.
    
    Features:
    - Async-compatible stdin reading (via run_in_executor)
    - Colored terminal output
    - HITL confirmation via [Y/n] prompt
    - Diff preview rendering with color highlighting
    """
    
    def __init__(self, 
                 user_id: str = "cli_user",
                 user_name: str = "Local User",
                 config: dict[str, Any] = None):
        super().__init__(channel_type=ChannelType.CLI, config=config)
        self.user_id = user_id
        self.user_name = user_name
        self._message_counter = 0
        self._auth_responses: dict[str, asyncio.Future] = {}
    
    async def start(self) -> None:
        """Start CLI channel."""
        self._running = True
        self._print_banner()
        logger.info("CLI channel started")
    
    async def stop(self) -> None:
        """Stop CLI channel."""
        self._running = False
        print(f"\n{Colors.GRAY}[CloseClaw] Session ended.{Colors.RESET}")
        logger.info("CLI channel stopped")
    
    async def receive_message(self) -> Optional[Message]:
        """Read user input from stdin (async-compatible).
        
        Uses run_in_executor to avoid blocking the event loop.
        Returns None on EOF or 'exit'/'quit' commands.
        """
        if not self._running:
            return None
        
        try:
            # Async-compatible stdin read
            loop = asyncio.get_event_loop()
            user_input = await loop.run_in_executor(
                None, 
                lambda: input(f"{Colors.CYAN}You > {Colors.RESET}")
            )
        except (EOFError, KeyboardInterrupt):
            return None
        
        # Handle exit commands
        user_input = user_input.strip()
        if not user_input:
            return await self.receive_message()  # Skip empty inputs
        
        if user_input.lower() in ("exit", "quit", "/quit", "/exit"):
            return None
        
        # Create Message
        self._message_counter += 1
        return self._create_message(
            message_id=f"cli_msg_{self._message_counter}",
            sender_id=self.user_id,
            sender_name=self.user_name,
            content=user_input,
        )
    
    async def send_response(self, response: dict[str, Any]) -> None:
        """Display response in terminal with formatting.
        
        Handles different response types:
        - "response": Normal agent reply
        - "auth_request": Zone C HITL confirmation
        - "task_completed": Background task result notification
        - "error": Error message
        """
        resp_type = response.get("type", "response")
        
        if resp_type == "response":
            text = response.get("response", "")
            tool_calls = response.get("tool_calls", [])
            tool_results = response.get("tool_results", [])
            
            # Show tool calls if any
            if tool_calls:
                for tc in tool_calls:
                    name = tc.get("name", "unknown") if isinstance(tc, dict) else str(tc)
                    print(f"  {Colors.DIM}🔧 Tool: {name}{Colors.RESET}")
            
            if tool_results:
                for tr in tool_results:
                    status = tr.get("status", "unknown") if isinstance(tr, dict) else str(tr)
                    icon = "✅" if status == "success" else "⏳" if status == "task_created" else "❌"
                    print(f"  {Colors.DIM}{icon} Result: {status}{Colors.RESET}")
            
            # Show main response
            print(f"{Colors.GREEN}Agent > {Colors.RESET}{text}\n")
        
        elif resp_type == "auth_request":
            await self._handle_auth_request(response)
        
        elif resp_type == "task_completed":
            task_id = response.get("task_id", "?")
            status = response.get("status", "?")
            result = response.get("result", "")
            error = response.get("error")
            
            print(f"\n{Colors.MAGENTA}📬 Background Task Completed{Colors.RESET}")
            print(f"  Task: {task_id} | Status: {status}")
            if error:
                print(f"  {Colors.RED}Error: {error}{Colors.RESET}")
            elif result:
                result_str = str(result)
                if len(result_str) > 200:
                    result_str = result_str[:200] + "..."
                print(f"  Result: {result_str}")
            print()
        
        elif resp_type == "error":
            error = response.get("error", "Unknown error")
            print(f"{Colors.RED}❌ Error: {error}{Colors.RESET}\n")
        
        else:
            print(f"{Colors.GRAY}[{resp_type}] {response}{Colors.RESET}\n")
    
    async def send_auth_request(self,
                                auth_request_id: str,
                                tool_name: str,
                                description: str,
                                diff_preview: Optional[str] = None) -> None:
        """Display HITL confirmation in terminal."""
        print(f"\n{Colors.YELLOW}{'═' * 60}{Colors.RESET}")
        print(f"{Colors.YELLOW}{Colors.BOLD}⚠️  Zone C Operation — Authorization Required{Colors.RESET}")
        print(f"{Colors.YELLOW}{'═' * 60}{Colors.RESET}")
        print(f"  Tool: {Colors.BOLD}{tool_name}{Colors.RESET}")
        print(f"  Description: {description}")
        
        if diff_preview:
            print(f"\n{Colors.CYAN}  Diff Preview:{Colors.RESET}")
            for line in diff_preview.split("\n"):
                if line.startswith("+"):
                    print(f"  {Colors.GREEN}{line}{Colors.RESET}")
                elif line.startswith("-"):
                    print(f"  {Colors.RED}{line}{Colors.RESET}")
                else:
                    print(f"  {Colors.GRAY}{line}{Colors.RESET}")
        
        print(f"{Colors.YELLOW}{'─' * 60}{Colors.RESET}")
    
    async def wait_for_auth_response(self,
                                      auth_request_id: str,
                                      timeout: float = 300.0) -> Optional[AuthorizationResponse]:
        """Prompt user for [Y/n] confirmation in terminal."""
        try:
            loop = asyncio.get_event_loop()
            answer = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: input(f"  {Colors.YELLOW}Approve? [Y/n]: {Colors.RESET}")
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            print(f"  {Colors.RED}⏰ Authorization timed out.{Colors.RESET}")
            return None
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {Colors.RED}❌ Authorization cancelled.{Colors.RESET}")
            return None
        
        approved = answer.strip().lower() in ("y", "yes", "")
        
        if approved:
            print(f"  {Colors.GREEN}✅ Approved{Colors.RESET}\n")
        else:
            print(f"  {Colors.RED}❌ Rejected{Colors.RESET}\n")
        
        return AuthorizationResponse(
            auth_request_id=auth_request_id,
            user_id=self.user_id,
            approved=approved,
        )
    
    async def _handle_auth_request(self, response: dict[str, Any]) -> None:
        """Handle auth_request type response (dispatches send + wait)."""
        auth_request_id = response.get("auth_request_id", "unknown")
        tool_name = response.get("tool_name", "unknown")
        description = response.get("description", "")
        diff_preview = response.get("diff_preview")
        
        await self.send_auth_request(
            auth_request_id=auth_request_id,
            tool_name=tool_name,
            description=description,
            diff_preview=diff_preview,
        )
        
        # Wait for user input inline
        auth_response = await self.wait_for_auth_response(auth_request_id)
        
        if auth_response:
            # Store for AgentCore to pick up
            self._auth_responses[auth_request_id] = auth_response
    
    def get_pending_auth_response(self, auth_request_id: str) -> Optional[AuthorizationResponse]:
        """Retrieve a stored auth response (used by AgentCore)."""
        return self._auth_responses.pop(auth_request_id, None)
    
    def _print_banner(self) -> None:
        """Print startup banner."""
        print(f"""
{Colors.CYAN}{'═' * 60}
  CloseClaw — Interactive CLI Mode
  Type your message and press Enter.
  Commands: /exit, /quit
{'═' * 60}{Colors.RESET}
""")
