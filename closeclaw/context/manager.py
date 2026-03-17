"""Token counting and context monitoring for Phase 4."""

import logging
import json
from typing import Optional, Tuple
from datetime import datetime

try:
    import tiktoken
except ImportError:
    tiktoken = None

logger = logging.getLogger(__name__)


class ContextManager:
    """Manages context window and token counting."""
    
    def __init__(self,
                 max_tokens: int = 100000,
                 warning_threshold: float = 0.75,
                 critical_threshold: float = 0.95,
                 summarize_window: int = 50,
                 active_window: int = 10,
                 model: str = "gpt-3.5-turbo"):
        """Initialize context manager.
        
        Args:
            max_tokens: Maximum tokens allowed in context
            warning_threshold: Soft threshold ratio (0-1)
            critical_threshold: Hard threshold ratio (0-1)
            summarize_window: Number of message rounds to summarize
            active_window: Keep last N rounds raw
            model: LLM model name for token encoding
        """
        self.max_tokens = max_tokens
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.summarize_window = summarize_window
        self.active_window = active_window
        self.model = model
        
        # Initialize tiktoken encoder
        self._encoder = None
        if tiktoken:
            try:
                self._encoder = tiktoken.get_encoding("cl100k_base")
            except Exception as e:
                logger.warning(f"Failed to load tiktoken encoder: {e}")
        
        # Track usage
        self.token_count = 0
        self.last_check_time = datetime.now()
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in text using tiktoken.
        
        Args:
            text: Text to count
            
        Returns:
            Token count
        """
        if not tiktoken or not self._encoder:
            # Fallback: rough approximation (1 token ≈ 4 chars)
            return len(text) // 4
        
        try:
            return len(self._encoder.encode(text))
        except Exception as e:
            logger.warning(f"Token counting failed: {e}, using fallback")
            return len(text) // 4
    
    def count_message_tokens(self, messages: list[dict]) -> int:
        """Count total tokens in a message list.
        
        Args:
            messages: List of message dicts (must have 'content' field)
            
        Returns:
            Total token count
        """
        total = 0
        for msg in messages:
            if isinstance(msg, dict) and 'content' in msg:
                content = msg.get('content', '')
                if content:
                    total += self.count_tokens(str(content))
        return total
    
    def get_usage_ratio(self, token_count: int) -> float:
        """Get context usage ratio.
        
        Args:
            token_count: Current token count
            
        Returns:
            Ratio (0-1)
        """
        return min(1.0, token_count / self.max_tokens)
    
    def check_thresholds(self, token_count: int) -> Tuple[str, bool]:
        """Check if context tokens exceed thresholds.
        
        Args:
            token_count: Current token count
            
        Returns:
            Tuple of (status, needs_flush) where:
            - status: "OK", "WARNING", or "CRITICAL"
            - needs_flush: Whether Memory Flush should trigger
        """
        usage_ratio = self.get_usage_ratio(token_count)
        self.token_count = token_count
        
        if usage_ratio >= self.critical_threshold:
            return "CRITICAL", True
        elif usage_ratio >= self.warning_threshold:
            return "WARNING", True
        else:
            return "OK", False
    
    def get_status_report(self, token_count: int) -> dict:
        """Generate a status report on context usage.
        
        Args:
            token_count: Current token count
            
        Returns:
            Status dict with metrics
        """
        usage_ratio = self.get_usage_ratio(token_count)
        status, needs_flush = self.check_thresholds(token_count)
        
        return {
            "current_tokens": token_count,
            "max_tokens": self.max_tokens,
            "usage_ratio": usage_ratio,
            "usage_percentage": f"{usage_ratio*100:.1f}%",
            "status": status,
            "needs_flush": needs_flush,
            "tokens_remaining": max(0, self.max_tokens - token_count),
            "warning_threshold_tokens": int(self.max_tokens * self.warning_threshold),
            "critical_threshold_tokens": int(self.max_tokens * self.critical_threshold),
            "timestamp": datetime.now().isoformat()
        }
    
    def json_report(self, token_count: int) -> str:
        """Generate JSON report of context usage."""
        return json.dumps(self.get_status_report(token_count), indent=2, ensure_ascii=False)
