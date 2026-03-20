"""Memory Flush Session for Phase 4 Step 2.

Handles:
- Detecting when context is about to be compressed
- Injecting system prompt to trigger LLM to save important discussions
- Capturing [SILENT_REPLY] marker
- Post-flush notification to user
"""

import logging
import os
import json
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class MemoryFlushSession:
    """Manages automatic session memory flushing with user-transparent workflow."""
    
    # Marker that LLM responds with when flushing is complete
    SILENT_REPLY_MARKER = "[SILENT_REPLY]"
    
    # System prompt to inject when triggering flush
    MEMORY_FLUSH_SYSTEM_PROMPT = None  # Set dynamically with workspace path
    
    def _generate_memory_flush_prompt(self) -> str:
        """Generate flush prompt with automatic execution requirement."""
        return f"""[!!! CRITICAL ACTIVITY: CONTEXT COMPRESSION !!!]

Your context window is almost full and will be compressed momentarily. You MUST flush key memories to persistent storage so they aren't lost.

馃敶 CRITICAL PROCEDURE: READ BEFORE WRITE

You must NOT blindly write new memory files if similar topics already exist.

馃搵 REQUIRED STEPS:

1. IDENTIFY: Determine the 1-3 core subjects discussed.
2. SEARCH: Call `search_memory` for each subject to see if we already have memories about this.
3. CONSOLIDATE/WRITE:
   - If memories exist: Call `read_memory` to see their exact content, then use `write_memory_file` to update/overwrite them with combined information.
   - If NO memories exist: Use `write_memory_file` to create new files (e.g., `{self.memory_dir}/memory_config.md`).
4. CONTEXT SUMMARY: After all memory read/write actions are done, output a compact context summary in the exact format below:
    [COMPACT_MEMORY_BLOCK]
    # Recent user goal(s)
    - Provide detailed intent, scope, and success criteria from this session.

    # Important decisions/constraints
    - Include concrete decisions, rejected options, environment/tool limits, and any approvals required.

    # Completed actions
    - List major operations with outcomes (tools used, key results, files touched when relevant).

    # Pending next action
    - Describe the immediate next step with enough detail that execution can resume without guessing.

    # Open risks/questions
    - Include unresolved uncertainties, assumptions, or potential failure points.

    REQUIREMENTS:
    - Be detailed and specific; do NOT output a one-line summary.
    - Prefer bullet lists with concrete facts.
    - Keep as much critical detail as possible within model limits.
    [/COMPACT_MEMORY_BLOCK]
5. CONFIRM: ONLY when you have fully completed all searches, readings, and writings, you must append EXACTLY:
    [SILENT_REPLY]

Note: You can use tools multiple times. Keep going until all critical information is safely stored.
[END CRITICAL COMMAND]"""

    def __init__(self, workspace_root: str, memory_subdir: str = "memory"):
        """Initialize memory flush session manager.
        
        Args:
            workspace_root: Root workspace directory
            memory_subdir: Subdirectory for flushed memories (relative to workspace)
        """
        self.workspace_root = workspace_root
        self.memory_dir = os.path.join(workspace_root, memory_subdir)
        self.flush_history: list[dict] = []
        
        # Create memory directory if not exists
        os.makedirs(self.memory_dir, exist_ok=True)
        logger.info(f"Memory flush manager initialized. Memory dir: {self.memory_dir}")
    
    def should_trigger_flush(self, context_status: str, usage_ratio: float) -> bool:
        """Determine if memory flush should be triggered.
        
        Args:
            context_status: Status from ContextManager ("OK", "WARNING", "CRITICAL")
            usage_ratio: Token usage ratio (0-1)
            
        Returns:
            True if flush should trigger
        """
        # Trigger at WARNING threshold (75%) but not yet at CRITICAL (95%)
        # This gives us a window to flush before hard truncation
        return context_status == "WARNING" and 0.75 <= usage_ratio < 0.95
    
    def create_flush_system_prompt(self) -> str:
        """Generate the system prompt to inject for memory flushing.
        
        Returns:
            System prompt string with flush trigger marker
        """
        return self._generate_memory_flush_prompt()
    
    def check_for_silent_reply(self, response_text: str) -> bool:
        """Check if LLM response contains the silent reply marker.
        
        Args:
            response_text: Response from LLM
            
        Returns:
            True if silent reply marker found
        """
        return self.SILENT_REPLY_MARKER in response_text if response_text else False
    
    def extract_silent_reply_content(self, response_text: str) -> str:
        """Extract actual content from response before silent reply marker.
        
        Args:
            response_text: Full response from LLM
            
        Returns:
            Content before the [SILENT_REPLY] marker (typically tool calls)
        """
        if not response_text:
            return ""
        
        marker_idx = response_text.find(self.SILENT_REPLY_MARKER)
        if marker_idx == -1:
            return response_text
        
        return response_text[:marker_idx].strip()
    
    def collect_saved_memories(self) -> list[dict]:
        """Collect list of memory files saved in the memory directory.
        
        Returns:
            List of memory file info dicts
        """
        try:
            memory_files = []
            if os.path.exists(self.memory_dir):
                for filename in os.listdir(self.memory_dir):
                    if filename.endswith('.md'):
                        file_path = os.path.join(self.memory_dir, filename)
                        # Get file info
                        stat_info = os.stat(file_path)
                        memory_files.append({
                            "name": filename,
                            "path": file_path,
                            "size": stat_info.st_size,
                            "modified": datetime.fromtimestamp(stat_info.st_mtime).isoformat()
                        })
            
            # Sort by modification time (newest first)
            memory_files.sort(key=lambda x: x['modified'], reverse=True)
            return memory_files
        except Exception as e:
            logger.error(f"Failed to collect saved memories: {e}")
            return []
    
    def generate_post_flush_notification(self, 
                                        saved_files: list[Dict],
                                        flush_session_id: str) -> str:
        """Generate a user-friendly post-flush notification.
        
        Args:
            saved_files: List of saved memory file info dicts
            flush_session_id: Unique session ID for this flush
            
        Returns:
            Notification message for user
        """
        if not saved_files:
            notification = f"""鉁?**[System] Auto Memory Flush Completed**
馃搵 Session ID: {flush_session_id}
鈿狅笍 No files were saved during this flush.
馃攧 Context will now be compressed to make room for new conversations.
"""
        else:
            notification = f"""鉁?**[System] Auto Memory Flush Completed**
馃搵 Session ID: {flush_session_id}
馃搧 Saved {len(saved_files)} memory file(s):
"""
            
            for i, file_info in enumerate(saved_files[:3]):  # Show first 3
                file_name = file_info['name']
                file_size_kb = file_info['size'] / 1024
                
                # Extract preview
                try:
                    with open(file_info['path'], 'r', encoding='utf-8') as f:
                        content = f.read(150)
                        if len(content) > 150:
                            content = content + "..."
                        content = content.replace("\n", " ")
                except:
                    content = "[Unable to read]"
                
                notification += f"\n   {i+1}. **{file_name}** ({file_size_kb:.1f} KB)\n"
                notification += f"      _Preview: {content}_\n"
            
            if len(saved_files) > 3:
                notification += f"\n   ... and {len(saved_files) - 3} more files"
            
            notification += f"\n\n馃敆 View all: `ls memory/` or check workspace memory directory\n"
            notification += f"馃棏锔?To remove: Delete files from workspace/memory/ directory\n"
        
        notification += f"""
馃攧 **Action**: Context is now being compressed. New conversation window is ready.
鈴憋笍 Timestamp: {datetime.now().isoformat()}"""
        
        return notification
    
    def record_flush_event(self,
                          user_id: str,
                          session_id: str,
                          saved_files: list[Dict],
                          context_ratio: float,
                          audit_logger: Optional[Any] = None) -> None:
        """Record memory flush event to audit log and history.
        
        Args:
            user_id: User ID who triggered the flush
            session_id: Unique flush session identifier
            saved_files: List of saved memory file info
            context_ratio: Context usage ratio when flush triggered
            audit_logger: Optional audit logger instance
        """
        event = {
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
            "user_id": user_id,
            "context_ratio": context_ratio,
            "files_saved": len(saved_files),
            "saved_file_names": [f['name'] for f in saved_files]
        }
        
        self.flush_history.append(event)
        
        # Record in audit log
        if audit_logger:
            try:
                audit_logger.log(
                    event_type="memory_flush_session",
                    status="success",
                    user_id=user_id,
                    tool_name="[system.memory_flush]",
                    arguments={
                        "session_id": session_id,
                        "context_ratio": context_ratio,
                        "files_saved": len(saved_files)
                    },
                    result=f"Flushed and saved {len(saved_files)} memory files"
                )
            except Exception as e:
                logger.error(f"Failed to record flush event in audit log: {e}")
        
        logger.info(f"Memory flush event recorded: session_id={session_id}, files_saved={len(saved_files)}")
    
    def get_flush_history(self, limit: int = 10) -> list[dict]:
        """Get recent memory flush history.
        
        Args:
            limit: Maximum number of recent events to return
            
        Returns:
            List of flush history events
        """
        return self.flush_history[-limit:]
    
    def json_report(self) -> str:
        """Generate JSON report of flush history."""
        return json.dumps({
            "memory_directory": self.memory_dir,
            "total_flushes": len(self.flush_history),
            "recent_events": self.get_flush_history(5),
            "saved_files": self.collect_saved_memories()
        }, indent=2, ensure_ascii=False)


class MemoryFlushCoordinator:
    """Coordinates memory flush workflow with agent core."""
    
    def __init__(self, memory_flush_session: MemoryFlushSession):
        """Initialize coordinator.
        
        Args:
            memory_flush_session: MemoryFlushSession instance
        """
        self.flush_session = memory_flush_session
        self.pending_flush = False
        self.last_flush_session_id: Optional[str] = None
    
    def generate_session_id(self) -> str:
        """Generate unique session ID for this flush."""
        from datetime import datetime
        return f"flush_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    def mark_flush_pending(self, context_status: str, usage_ratio: float) -> bool:
        """Mark a flush as pending based on context status.
        
        Args:
            context_status: From ContextManager
            usage_ratio: Current usage ratio
            
        Returns:
            True if flush was marked pending
        """
        if self.flush_session.should_trigger_flush(context_status, usage_ratio):
            self.pending_flush = True
            self.last_flush_session_id = self.generate_session_id()
            logger.warning(f"Memory flush pending: session_id={self.last_flush_session_id}, usage_ratio={usage_ratio}")
            return True
        return False
    
    def has_pending_flush(self) -> bool:
        """Check if flush is pending."""
        return self.pending_flush
    
    def clear_pending_flush(self) -> None:
        """Clear pending flush flag."""
        self.pending_flush = False


