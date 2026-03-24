"""Embedded CLI channel - Interactive terminal-based channel.

Shares the same AgentCore instance with other channels.
Provides stdin/stdout based message exchange and HITL confirmation.

From Planning.md:
  "鏈湴 CLI 瀹炵幇锛氬祵鍏ュ紡 CLI 椹卞姩锛屼笌 Server 鍏变韩鍚屼竴涓?AgentCore 瀹炰緥锛?   閫氳繃 asyncio.gather 鍚屾椂鍚姩 Server 鍜?CLI 寰幆銆?
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
        self._incoming_queue: asyncio.Queue[Optional[Message]] = asyncio.Queue()
        self._stdin_task: Optional[asyncio.Task] = None
        # Input gate controls when the next `You >` prompt is allowed.
        # It starts blocked and is opened by receive_message/send_response.
        self._input_gate = asyncio.Event()
    
    async def start(self) -> None:
        """Start CLI channel."""
        self._running = True
        self._print_banner()
        self._stdin_task = asyncio.create_task(self._stdin_loop())
        logger.info("CLI channel started")
    
    async def stop(self) -> None:
        """Stop CLI channel."""
        self._running = False
        if self._stdin_task:
            self._stdin_task.cancel()
            try:
                await self._stdin_task
            except asyncio.CancelledError:
                pass
            finally:
                self._stdin_task = None
        print(f"\n{Colors.GRAY}[CloseClaw] Session ended.{Colors.RESET}")
        logger.info("CLI channel stopped")

    async def _stdin_loop(self) -> None:
        """Background stdin reader that pushes user messages into incoming queue."""
        while self._running:
            try:
                await self._input_gate.wait()
                if not self._running:
                    return

                loop = asyncio.get_running_loop()
                user_input = await loop.run_in_executor(
                    None,
                    lambda: input(f"{Colors.CYAN}You > {Colors.RESET}")
                )
            except (EOFError, KeyboardInterrupt):
                await self._incoming_queue.put(None)
                return
            except asyncio.CancelledError:
                return

            user_input = user_input.strip()
            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "/quit", "/exit"):
                await self._incoming_queue.put(None)
                return

            self._message_counter += 1
            # Block next prompt until the current turn has output.
            self._input_gate.clear()
            await self._incoming_queue.put(
                self._create_message(
                    message_id=f"cli_msg_{self._message_counter}",
                    sender_id=self.user_id,
                    sender_name=self.user_name,
                    content=user_input,
                )
            )

    async def inject_message(self, message: Message) -> None:
        """Inject an external message (e.g., cron wake event) into CLI input stream."""
        await self._incoming_queue.put(message)
    
    async def receive_message(self) -> Optional[Message]:
        """Receive next queued message from stdin reader or injected events."""
        if not self._running:
            return None
        # Allow one prompt whenever runtime asks for the next user message.
        if not self._input_gate.is_set():
            self._input_gate.set()
        return await self._incoming_queue.get()
    
    async def send_response(self, response: dict[str, Any]) -> None:
        """Display response in terminal with formatting.
        
        Handles different response types:
        - "response": Normal agent reply
        - "auth_request": need_auth HITL confirmation
        - "task_completed": Background task result notification
        - "error": Error message
        """
        resp_type = response.get("type", "response")
        
        if resp_type in {"response", "assistant_message"}:
            text = response.get("response", "")
            tool_calls = response.get("tool_calls", [])
            tool_results = response.get("tool_results", [])
            
            # Show tool calls if any
            if tool_calls:
                for tc in tool_calls:
                    name = tc.get("name", "unknown") if isinstance(tc, dict) else str(tc)
                    print(f"  {Colors.DIM}[TOOL] {name}{Colors.RESET}")
            
            if tool_results:
                for tr in tool_results:
                    status = tr.get("status", "unknown") if isinstance(tr, dict) else str(tr)
                    icon = "[OK]" if status == "success" else ("[TASK]" if status == "task_created" else "[ERR]")
                    print(f"  {Colors.DIM}{icon} Result: {status}{Colors.RESET}")

                    if isinstance(tr, dict):
                        metadata = tr.get("metadata") or {}
                        auth_mode = metadata.get("auth_mode")
                        if auth_mode == "consensus":
                            decision = metadata.get("guardian_decision") or "approve"
                            print(f"  {Colors.DIM}[GUARDIAN] {decision}{Colors.RESET}")
            
            # Show main response
            print(f"{Colors.GREEN}Agent > {Colors.RESET}{text}\n")
            self._input_gate.set()
        
        elif resp_type == "auth_request":
            await self._handle_auth_request(response)
        
        elif resp_type == "task_completed":
            task_id = response.get("task_id", "?")
            status = response.get("status", "?")
            result = response.get("result", "")
            error = response.get("error")
            
            print(f"\n{Colors.MAGENTA}Background Task Completed{Colors.RESET}")
            print(f"  Task: {task_id} | Status: {status}")
            if error:
                print(f"  {Colors.RED}Error: {error}{Colors.RESET}")
            elif result:
                result_str = str(result)
                if len(result_str) > 200:
                    result_str = result_str[:200] + "..."
                print(f"  Result: {result_str}")
            print()
            self._input_gate.set()

        elif resp_type == "tool_progress":
            tool_name = response.get("tool_name", "unknown")
            status = response.get("status", "unknown")
            target_file = response.get("target_file")
            print(f"  {Colors.DIM}[TOOL] tool={tool_name} status={status}{Colors.RESET}")
            if target_file:
                print(f"  {Colors.DIM}          file={target_file}{Colors.RESET}")
        
        elif resp_type == "error":
            error = response.get("error", "Unknown error")
            print(f"{Colors.RED}Error: {error}{Colors.RESET}\n")
            self._input_gate.set()
        
        else:
            print(f"{Colors.GRAY}[{resp_type}] {response}{Colors.RESET}\n")
            self._input_gate.set()
    
    async def send_auth_request(self,
                                auth_request_id: str,
                                tool_name: str,
                                description: str,
                                diff_preview: Optional[str] = None,
                                reason: Optional[str] = None,
                                auth_mode: Optional[str] = None) -> None:
        """Display HITL confirmation in terminal."""
        print(f"\n{Colors.YELLOW}{'=' * 60}{Colors.RESET}")
        print(f"{Colors.YELLOW}{Colors.BOLD}Sensitive Operation - Authorization Required{Colors.RESET}")
        print(f"{Colors.YELLOW}{'=' * 60}{Colors.RESET}")
        print(f"  Tool: {Colors.BOLD}{tool_name}{Colors.RESET}")
        print(f"  Description: {description}")
        if auth_mode:
            print(f"  Auth Mode: {auth_mode}")
        if reason:
            print(f"  Reason: {reason}")
        
        if diff_preview:
            print(f"\n{Colors.CYAN}  Diff Preview:{Colors.RESET}")
            for line in diff_preview.split("\n"):
                if line.startswith("+"):
                    print(f"  {Colors.GREEN}{line}{Colors.RESET}")
                elif line.startswith("-"):
                    print(f"  {Colors.RED}{line}{Colors.RESET}")
                else:
                    print(f"  {Colors.GRAY}{line}{Colors.RESET}")
        
        print(f"{Colors.YELLOW}{'-' * 60}{Colors.RESET}")
    
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
            print(f"  {Colors.RED}Authorization timed out.{Colors.RESET}")
            return None
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {Colors.RED}Authorization cancelled.{Colors.RESET}")
            return None
        
        approved = answer.strip().lower() in ("y", "yes", "")
        
        if approved:
            print(f"  {Colors.GREEN}Approved{Colors.RESET}\n")
        else:
            print(f"  {Colors.RED}Rejected{Colors.RESET}\n")
        
        return AuthorizationResponse(
            auth_request_id=auth_request_id,
            user_id=self.user_id,
            approved=approved,
        )
    
    async def _handle_auth_request(self, response: dict[str, Any]) -> None:
        """Handle auth_request type response (display only).

        Input collection is handled by AgentCore via auth_response_fn.
        Keeping input here would cause duplicate prompts in CLI.
        """
        auth_request_id = response.get("auth_request_id", "unknown")
        tool_name = response.get("tool_name", "unknown")
        description = response.get("description", "")
        diff_preview = response.get("diff_preview")
        reason = response.get("reason")
        auth_mode = response.get("auth_mode")
        
        await self.send_auth_request(
            auth_request_id=auth_request_id,
            tool_name=tool_name,
            description=description,
            diff_preview=diff_preview,
            reason=reason,
            auth_mode=auth_mode,
        )
    
    def _print_banner(self) -> None:
        """Print startup banner."""
        print(f"""
{Colors.CYAN}{'=' * 60}
    CloseClaw - Interactive CLI Mode
  Type your message and press Enter.
  Commands: /exit, /quit
{'=' * 60}{Colors.RESET}
""")


