"""Message compaction and summarization for context management."""

import logging
from typing import Optional, List, Tuple
import json
from datetime import datetime

logger = logging.getLogger(__name__)


class MessageCompactor:
    """Compacts and summarizes messages to reduce token usage."""
    
    def __init__(self,
                 summarize_window: int = 50,
                 active_window: int = 10,
                 chunk_size: int = 5000):
        """Initialize message compactor.
        
        Args:
            summarize_window: Number of rounds to summarize at once
            active_window: Number of recent rounds to preserve raw
            chunk_size: Max tokens per summarization chunk
        """
        self.summarize_window = summarize_window
        self.active_window = active_window
        self.chunk_size = chunk_size
        self.summarization_history: List[dict] = []
    
    def identify_compactable_messages(self,
                                      messages: List[dict],
                                      total_tokens: int,
                                      max_tokens: int) -> Tuple[List[int], List[int]]:
        """Identify which messages can be compacted.
        
        Args:
            messages: List of message dicts
            total_tokens: Current total token count
            max_tokens: Maximum token limit
            
        Returns:
            Tuple of (indices_to_summarize, indices_to_keep_raw)
        """
        n = len(messages)
        
        # Always keep the last active_window rounds raw
        keep_raw_start_idx = max(0, n - self.active_window)
        
        indices_to_keep_raw = list(range(keep_raw_start_idx, n))
        indices_to_summarize = list(range(0, keep_raw_start_idx))
        
        return indices_to_summarize, indices_to_keep_raw
    
    def extract_summary_content(self, messages: List[dict], indices: List[int]) -> str:
        """Extract content from messages for summarization.
        
        Args:
            messages: List of message dicts
            indices: Indices to extract
            
        Returns:
            Combined content string
        """
        content_parts = []
        
        for idx in indices:
            if idx < len(messages):
                msg = messages[idx]
                if isinstance(msg, dict):
                    sender = msg.get('role', 'unknown')
                    content = msg.get('content', '')
                    if content:
                        content_parts.append(f"[{sender}]: {content}")
        
        return "\n".join(content_parts)
    
    def create_summary_placeholder(self, 
                                   original_count: int,
                                   summary_text: str,
                                   indices: List[int]) -> dict:
        """Create a summary message placeholder.
        
        Args:
            original_count: Number of original messages summarized
            summary_text: The summary content
            indices: Original indices summarized
            
        Returns:
            Summary message dict
        """
        return {
            "role": "system",
            "content": f"[CONTEXT_SUMMARY] {original_count} rounds compressed:\n{summary_text}",
            "is_summary": True,
            "original_indices": indices,
            "created_at": datetime.now().isoformat(),
            "summary_metadata": {
                "original_message_count": original_count,
                "compression_ratio": len(summary_text) / max(1, sum(len(str(m)) for m in indices))
            }
        }
    
    def compact_messages(self,
                        messages: List[dict],
                        compression_method: str = "drop_oldest") -> List[dict]:
        """Compact message list using specified method.
        
        Args:
            messages: Original message list
            compression_method: "drop_oldest" or "summarize"
            
        Returns:
            Compacted message list
        """
        if not messages:
            return messages
        
        indices_to_summarize, indices_to_keep = self.identify_compactable_messages(
            messages, 0, 0
        )
        
        if compression_method == "drop_oldest":
            # Hard drop: keep only active window
            return [messages[i] for i in indices_to_keep]
        
        elif compression_method == "summarize":
            # Soft drop: create summary placeholder
            if indices_to_summarize:
                summary_content = self.extract_summary_content(messages, indices_to_summarize)
                summary_msg = self.create_summary_placeholder(
                    len(indices_to_summarize),
                    summary_content,
                    indices_to_summarize
                )
                self.summarization_history.append(summary_msg)
                
                # Return: [summary] + [raw messages]
                return [summary_msg] + [messages[i] for i in indices_to_keep]
            else:
                return messages
        
        return messages
    
    def apply_compression_strategy(self,
                                   messages: List[dict],
                                   token_count: int,
                                   usage_ratio: float,
                                   force: bool = False) -> Tuple[List[dict], str]:
        """Apply appropriate compression strategy based on usage ratio.
        
        Args:
            messages: Original message list
            token_count: Current token count
            usage_ratio: Usage ratio (0-1)
            force: Force hard truncation if True
            
        Returns:
            Tuple of (compressed_messages, action_taken)
        """
        if force:
            # Hard truncation: drop oldest messages
            result = self.compact_messages(messages, "drop_oldest")
            return result, "hard_truncate"
        
        if usage_ratio < 0.75:
            return messages, "none"
        
        if usage_ratio >= 0.95:
            # Hard truncation: drop oldest messages
            result = self.compact_messages(messages, "drop_oldest")
            return result, "hard_truncate"
        
        elif usage_ratio >= 0.75:
            # Soft compression: summarize oldest messages
            result = self.compact_messages(messages, "summarize")
            return result, "summarize"
        
        return messages, "none"
    
    def get_compaction_report(self) -> dict:
        """Get report on compaction history."""
        return {
            "total_summarizations": len(self.summarization_history),
            "recent_summaries": self.summarization_history[-5:] if self.summarization_history else [],
            "config": {
                "summarize_window": self.summarize_window,
                "active_window": self.active_window,
                "chunk_size": self.chunk_size
            }
        }

