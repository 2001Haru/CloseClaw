"""Context service for compaction, flush orchestration, and memory retrieval."""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional

from ..types import Message, ToolCall, ToolResult

logger = logging.getLogger(__name__)


class ContextService:
    """Encapsulates context/memory-related runtime workflows."""

    def __init__(
        self,
        context_manager: Any,
        message_compactor: Any,
        memory_flush_session: Any,
        memory_flush_coordinator: Any,
        memory_manager: Any,
        planning_service: Any,
        audit_logger: Any,
        compact_memory_max_chars: int = 3000,
    ) -> None:
        self.context_manager = context_manager
        self.message_compactor = message_compactor
        self.memory_flush_session = memory_flush_session
        self.memory_flush_coordinator = memory_flush_coordinator
        self.memory_manager = memory_manager
        self.planning_service = planning_service
        self.audit_logger = audit_logger
        self.compact_memory_max_chars = compact_memory_max_chars

    async def maybe_trigger_memory_flush_before_planning(
        self,
        messages_for_check: list[dict[str, Any]],
    ) -> Optional[str]:
        """Return flush session_id when warning threshold requires flush."""
        token_count = self.context_manager.count_message_tokens(messages_for_check)
        status, _ = self.context_manager.check_thresholds(token_count)
        usage_ratio = self.context_manager.get_usage_ratio(token_count)

        if not self.memory_flush_session.should_trigger_flush(status, usage_ratio):
            return None

        session_id = self.memory_flush_coordinator.generate_session_id()
        self.memory_flush_coordinator.pending_flush = True
        self.memory_flush_coordinator.last_flush_session_id = session_id

        logger.warning(
            "[MEMORY_FLUSH] Triggered before planning: session_id=%s, usage=%.1f%%",
            session_id,
            usage_ratio * 100,
        )
        return session_id

    async def execute_memory_flush_standalone(
        self,
        *,
        session_id: str,
        message_history: list[Message],
        agent_id: str,
        current_user_id: str,
        process_tool_call: Callable[[ToolCall], Awaitable[ToolResult]],
        format_tools_for_llm: Callable[[], list[dict[str, Any]]],
        format_conversation_for_llm: Callable[[], list[dict[str, Any]]],
        current_compact_memory_snapshot: Optional[str],
    ) -> tuple[list[Message], Optional[str]]:
        """Run flush mini-loop and return updated (message_history, compact_snapshot)."""
        compact_snapshot = current_compact_memory_snapshot

        try:
            temp_messages: list[dict[str, Any]] = [{
                "role": "system",
                "content": "You are an AI assistant preserving important conversation memory for future reference.",
            }]

            for msg in message_history:
                role = "user" if msg.sender_id != agent_id else "assistant"
                temp_messages.append({"role": role, "content": msg.content})

            temp_messages.append({
                "role": "user",
                "content": self.memory_flush_session.create_flush_system_prompt(),
            })

            tools_for_llm = format_tools_for_llm()

            for _ in range(5):
                llm_response, tool_calls = await self.planning_service.generate_plan_or_answer(
                    messages=temp_messages,
                    tools=tools_for_llm,
                    temperature=0.3,
                )

                compact_block = self.extract_compact_memory_block(llm_response or "")
                if compact_block:
                    normalized = self.normalize_compact_memory(compact_block)
                    if normalized:
                        compact_snapshot = normalized

                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": llm_response or "",
                }
                if tool_calls:
                    assistant_message["tool_calls"] = [
                        {
                            "id": tc.tool_id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments,
                            },
                        }
                        for tc in tool_calls
                    ]
                temp_messages.append(assistant_message)

                if self.memory_flush_session.check_for_silent_reply(llm_response):
                    break

                for tc in tool_calls or []:
                    result = await process_tool_call(tc)
                    tool_content = (
                        json.dumps(result.result)
                        if result.status == "success" and not isinstance(result.result, str)
                        else (result.result if result.status == "success" else f"Error: {result.error}")
                    )
                    temp_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.tool_id,
                            "content": tool_content or "",
                        }
                    )

            saved_files = self.memory_flush_session.collect_saved_memories()
            self.memory_flush_session.record_flush_event(
                user_id=current_user_id,
                session_id=session_id,
                saved_files=saved_files,
                context_ratio=self.context_manager.get_usage_ratio(
                    self.context_manager.count_message_tokens(format_conversation_for_llm())
                ),
                audit_logger=self.audit_logger,
            )

            keep = max(self.message_compactor.active_window * 2, 5)
            if len(message_history) > keep:
                message_history = message_history[-keep:]

            self.memory_flush_coordinator.clear_pending_flush()
            logger.warning(
                "[MEMORY_FLUSH] Completed: session_id=%s, files_saved=%d",
                session_id,
                len(saved_files),
            )
            return message_history, compact_snapshot
        except Exception as exc:
            logger.error("[MEMORY_FLUSH] Failed: %s", exc)
            self.memory_flush_coordinator.clear_pending_flush()
            return message_history, compact_snapshot

    def extract_compact_memory_block(self, text: str) -> Optional[str]:
        if not text:
            return None

        start = "[COMPACT_MEMORY_BLOCK]"
        end = "[/COMPACT_MEMORY_BLOCK]"
        s_idx = text.find(start)
        e_idx = text.find(end)
        if s_idx == -1 or e_idx == -1 or e_idx <= s_idx:
            return None

        block = text[s_idx + len(start):e_idx].strip()
        return block or None

    def normalize_compact_memory(self, text: str) -> Optional[str]:
        if not text:
            return None

        normalized_lines = []
        blank_streak = 0
        for raw_line in text.replace("\r\n", "\n").split("\n"):
            line = raw_line.strip()
            if not line:
                blank_streak += 1
                if blank_streak > 1:
                    continue
                normalized_lines.append("")
                continue
            blank_streak = 0
            normalized_lines.append(line)

        normalized = "\n".join(normalized_lines).strip()
        if not normalized:
            return None

        guard = (
            "This is a compressed memory summary and may be incomplete. "
            "If critical details are uncertain, verify via tools."
        )
        payload = f"{guard}\n\n{normalized}"

        if len(payload) > self.compact_memory_max_chars:
            payload = payload[: self.compact_memory_max_chars] + "..."

        return payload

    def capture_compact_memory_snapshot(self, message_history: list[Message], agent_id: str) -> Optional[str]:
        for msg in reversed(message_history):
            if msg.sender_id != agent_id:
                continue

            content = (msg.content or "").strip()
            if not content:
                continue

            extracted = self.extract_compact_memory_block(content)
            candidate = extracted or content
            normalized = self.normalize_compact_memory(candidate)
            if normalized:
                return normalized

        return None

    def build_compact_memory_pair(self, compact_memory_snapshot: Optional[str]) -> list[dict[str, Any]]:
        if not compact_memory_snapshot:
            return []

        return [
            {
                "role": "user",
                "content": (
                    "compact memory: previous context was compressed. "
                    "Please use the following summary as prior context before responding."
                ),
            },
            {
                "role": "assistant",
                "content": compact_memory_snapshot,
            },
        ]

    def build_memory_recall_block(self, has_retrieve_memory_tool: bool) -> str:
        if not has_retrieve_memory_tool:
            return ""

        return """[MEMORY RECALL POLICY]
Before answering questions that depend on earlier decisions, preferences, constraints, TODOs, or historical commitments, call retrieve_memory first.

When to recall first:
- The user asks what was decided before.
- The user asks to continue prior tasks or plans.
- The user asks about preferences, environment constraints, or remembered facts.

How to respond:
- Ground the answer in retrieved memory results when available.
- If memory is missing or uncertain, say so clearly and ask a clarifying follow-up.
- Do not fabricate prior decisions or commitments."""

    def build_system_prompt(
        self,
        *,
        base_prompt: str,
        has_retrieve_memory_tool: bool,
        suffix: str = "",
    ) -> str:
        """Build composed system prompt with memory recall policy and optional suffix."""
        memory_recall_block = self.build_memory_recall_block(has_retrieve_memory_tool)

        prompt_parts = [base_prompt.strip()] if base_prompt else []
        if memory_recall_block:
            prompt_parts.append(memory_recall_block)
        if suffix:
            prompt_parts.append(suffix.strip())

        return "\n\n".join(part for part in prompt_parts if part)

    def build_context_monitor_suffix(self, token_count: int, max_tokens: int, usage_percentage: str) -> str:
        """Build the context monitor suffix shown inside system prompt."""
        return (
            f"\n\n[CONTEXT MONITOR] Current token usage: "
            f"{token_count}/{max_tokens} ({usage_percentage})"
        )

    def analyze_context_usage(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Count tokens and evaluate context thresholds for a message list."""
        token_count = self.context_manager.count_message_tokens(messages)
        status, should_flush = self.context_manager.check_thresholds(token_count)
        context_report = self.context_manager.get_status_report(token_count)
        usage_ratio = self.context_manager.get_usage_ratio(token_count)
        token_usage_info = self.build_context_monitor_suffix(
            token_count=token_count,
            max_tokens=self.context_manager.max_tokens,
            usage_percentage=context_report["usage_percentage"],
        )
        return {
            "token_count": token_count,
            "status": status,
            "should_flush": should_flush,
            "context_report": context_report,
            "usage_ratio": usage_ratio,
            "token_usage_info": token_usage_info,
        }

    def apply_critical_trim_policy(
        self,
        *,
        message_history: list[Message],
        capture_compact_memory_snapshot: Callable[[], Optional[str]],
        keep_turns: int = 10,
    ) -> dict[str, Any]:
        """Apply deterministic CRITICAL fallback trimming policy to message history."""
        compact_snapshot = capture_compact_memory_snapshot()
        keep_messages = keep_turns * 2
        old_size = len(message_history)
        new_history = message_history[-keep_messages:] if old_size > keep_messages else message_history

        return {
            "compact_snapshot": compact_snapshot,
            "old_size": old_size,
            "new_size": len(new_history),
            "keep_turns": keep_turns,
            "message_history": new_history,
        }

    def log_context_threshold_warning(
        self,
        *,
        status: str,
        should_flush: bool,
        context_report: dict[str, Any],
        token_count: int,
        current_user_id: str,
    ) -> None:
        """Log context threshold warning and persist an audit record."""
        logger.warning("[CONTEXT_WARNING] Status=%s, should_flush=%s", status, should_flush)
        try:
            self.audit_logger.log(
                event_type="context_threshold_warning",
                status=status,
                user_id=current_user_id,
                tool_name="[system.context_manager]",
                arguments=context_report,
                result=f"Token count {token_count} exceeded {status.lower()} threshold",
            )
        except Exception as exc:
            logger.error("Failed to log context warning: %s", exc)

    def serialize_tool_result(self, tool_result: ToolResult) -> str:
        """Normalize tool results into transcript-safe text content."""
        if tool_result.status == "success":
            return (
                tool_result.result
                if isinstance(tool_result.result, str)
                else json.dumps(tool_result.result)
            )
        if tool_result.status == "auth_required":
            return "Operation requires user authorization. Waiting for approval."
        return f"Error or Blocked ({tool_result.status}): {tool_result.error}"

    def append_formatted_history_messages(
        self,
        *,
        target_messages: list[dict[str, Any]],
        message_history: list[Message],
        agent_id: str,
        max_result_chars: int = 10000,
    ) -> None:
        """Append conversation history in OpenAI-compatible role format."""
        for msg in message_history:
            role = "user" if msg.sender_id != agent_id else "assistant"

            msg_dict: dict[str, Any] = {
                "role": role,
                "content": msg.content,
            }

            if role == "assistant" and msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.tool_id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]

            target_messages.append(msg_dict)

            if msg.tool_results:
                for tr in msg.tool_results:
                    content = self.serialize_tool_result(tr)
                    if len(content) > max_result_chars:
                        content = (
                            content[:max_result_chars]
                            + f"\n\n... [Output truncated because it exceeded {max_result_chars} characters]"
                        )

                    target_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tr.tool_call_id,
                            "content": content,
                        }
                    )

    def repair_transcript(
        self,
        messages: list[dict[str, Any]],
        current_user_id: str = "system",
    ) -> list[dict[str, Any]]:
        """Repair transcript so tool_call and tool result pairs remain consistent."""
        repaired: list[dict[str, Any]] = []
        pending_tool_calls: dict[str, str] = {}

        stats = {
            "orphan_calls_removed": 0,
            "orphan_results_dropped": 0,
            "synthetic_results_added": 0,
        }

        for msg in messages:
            role = msg.get("role")

            if role in ["user", "assistant", "system"] and pending_tool_calls:
                for tc_id in pending_tool_calls:
                    repaired.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": (
                                "[System Repair] Tool execution interrupted or cancelled before "
                                "completion. Synthetic error injected to repair transcript."
                            ),
                        }
                    )
                    stats["synthetic_results_added"] += 1
                    stats["orphan_calls_removed"] += 1
                pending_tool_calls.clear()

            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    tc_id = tc["id"]
                    pending_tool_calls[tc_id] = tc["function"]["name"]

            if role == "tool":
                tc_id = msg.get("tool_call_id")
                if tc_id in pending_tool_calls:
                    del pending_tool_calls[tc_id]
                else:
                    stats["orphan_results_dropped"] += 1
                    continue

            repaired.append(msg)

        if pending_tool_calls:
            for tc_id in pending_tool_calls:
                repaired.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": (
                            "[System Repair] Tool execution interrupted before completion. "
                            "Synthetic error injected."
                        ),
                    }
                )
                stats["synthetic_results_added"] += 1
                stats["orphan_calls_removed"] += 1
            pending_tool_calls.clear()

        if stats["orphan_calls_removed"] > 0 or stats["orphan_results_dropped"] > 0:
            logger.info(
                "[TRANSCRIPT_REPAIR] orphan_calls_removed=%s orphan_results_dropped=%s synthetic_results_added=%s",
                stats["orphan_calls_removed"],
                stats["orphan_results_dropped"],
                stats["synthetic_results_added"],
            )
            try:
                self.audit_logger.log(
                    event_type="transcript_repair",
                    status="success",
                    user_id=current_user_id,
                    tool_name="[system.transcript_repair]",
                    arguments={
                        "orphan_calls_removed": stats["orphan_calls_removed"],
                        "orphan_results_dropped": stats["orphan_results_dropped"],
                        "synthetic_results_added": stats["synthetic_results_added"],
                    },
                    result=f"Repaired transcript: {len(repaired)} messages",
                )
            except Exception as exc:
                logger.error("Failed to log transcript repair: %s", exc)

        return repaired

    async def retrieve_memory(self, query: str, session_id: Optional[str]) -> str:
        logger.info("Retrieving memories for query: %s", query)

        try:
            memories = self.memory_manager.retrieve_memories(
                query=query,
                top_k=5,
                session_id=session_id,
            )

            if not memories:
                return "No relevant memories found."

            result = "Found relevant memories:\n\n"
            for i, mem in enumerate(memories, 1):
                result += f"{i}. [Score: {mem.score:.2f}] (Source: {mem.source})\n"
                result += f"{mem.content[:500]}...\n\n"

            return result
        except Exception as exc:
            logger.error("Error retrieving memories: %s", exc)
            return f"Error retrieving memories: {str(exc)}"
