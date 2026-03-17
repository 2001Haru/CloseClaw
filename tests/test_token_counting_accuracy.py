"""Token counting accuracy verification for Phase 4."""

import pytest
import tiktoken


class TestTokenCountingAccuracy:
    """Verify token counting accuracy meets >98% requirement."""
    
    @pytest.fixture
    def encoder(self):
        """Load tiktoken encoder."""
        return tiktoken.get_encoding("cl100k_base")
    
    @pytest.fixture
    def context_manager(self):
        """Create context manager."""
        from closeclaw.context import ContextManager
        return ContextManager()
    
    def test_token_counting_accuracy_simple_text(self, context_manager, encoder):
        """Test token counting accuracy on simple text."""
        texts = [
            "Hello, world!",
            "What is the capital of France?",
            "The quick brown fox jumps over the lazy dog.",
            "Python is a great programming language.",
        ]
        
        for text in texts:
            # Get true count from tiktoken
            true_count = len(encoder.encode(text))
            # Get count from context manager
            cm_count = context_manager.count_tokens(text)
            
            # Should be identical when tiktoken is available
            assert cm_count == true_count, f"Accuracy mismatch for '{text}': CM={cm_count}, True={true_count}"
    
    def test_token_counting_accuracy_long_text(self, context_manager, encoder):
        """Test token counting accuracy on longer text."""
        long_text = """
        CloseClaw is a framework for building safe and controlled AI agents.
        It emphasizes security, transparency, and human-in-the-loop decision making.
        The framework provides tools for managing agent workflows, monitoring execution,
        and implementing fine-grained security controls.
        
        Key features include:
        - Zone-based permission system (A/B/C zones)
        - Memory and context management
        - Tool adaptation and routing
        - Integrated audit logging
        - Multi-channel communication support
        """ * 10  # Repeat for longer text
        
        true_count = len(encoder.encode(long_text))
        cm_count = context_manager.count_tokens(long_text)
        
        # Accuracy: (1 - |cm - true| / true) * 100
        accuracy = (1 - abs(cm_count - true_count) / true_count) * 100
        
        # Require >98% accuracy
        assert accuracy >= 98, f"Accuracy {accuracy:.2f}% below 98% threshold. CM={cm_count}, True={true_count}"
    
    def test_token_counting_accuracy_unicode_text(self, context_manager, encoder):
        """Test token counting with unicode characters."""
        unicode_texts = [
            "你好，世界",  # Chinese
            "Привет, мир",  # Russian
            "مرحبا بالعالم",  # Arabic
            "こんにちは",  # Japanese
        ]
        
        for text in unicode_texts:
            true_count = len(encoder.encode(text))
            cm_count = context_manager.count_tokens(text)
            
            # Should handle unicode properly
            accuracy = (1 - abs(cm_count - true_count) / true_count) * 100 if true_count > 0 else 100
            assert accuracy >= 95, f"Unicode accuracy too low for {text}: {accuracy:.2f}%"
    
    def test_token_counting_accuracy_code_snippets(self, context_manager, encoder):
        """Test token counting accuracy on code."""
        code_snippets = [
            """
def calculate_fibonacci(n):
    if n <= 1:
        return n
    return calculate_fibonacci(n-1) + calculate_fibonacci(n-2)
            """,
            """
async def process_messages(messages):
    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            for tool_call in msg.tool_calls:
                result = await execute_tool(tool_call)
                yield result
            """,
        ]
        
        for code in code_snippets:
            true_count = len(encoder.encode(code))
            cm_count = context_manager.count_tokens(code)
            
            # Code is challenging for tokenizers
            accuracy = (1 - abs(cm_count - true_count) / true_count) * 100
            assert accuracy >= 95, f"Code accuracy {accuracy:.2f}% below 95%"
    
    def test_message_list_token_counting_accuracy(self, context_manager, encoder):
        """Test token counting on message lists matches sum of individual tokens."""
        messages = [
            {"role": "user", "content": "What is machine learning?"},
            {"role": "assistant", "content": "Machine learning is a branch of artificial intelligence..."},
            {"role": "user", "content": "Can you give me an example?"},
            {"role": "assistant", "content": "Sure, here's an example: decision trees..."},
        ]
        
        # Method 1: Count entire concatenated text
        full_text = " ".join(msg["content"] for msg in messages)
        true_count_full = len(encoder.encode(full_text))
        
        # Method 2: Sum individual counts
        individual_sum = sum(
            len(encoder.encode(msg["content"]))
            for msg in messages
        )
        
        # Method 3: Use context manager
        cm_count = context_manager.count_message_tokens(messages)
        
        # Method 3 should match Method 1 or Method 2 (depending on implementation)
        # Within reason due to tokenizer boundary effects
        error_vs_full = abs(cm_count - true_count_full)
        error_vs_sum = abs(cm_count - individual_sum)
        
        # Allow 5% error margin due to how tokens may be split
        max_error = max(0.05 * true_count_full, 5)
        assert error_vs_full <= max_error or error_vs_sum <= max_error, \
            f"Message token count inaccurate: CM={cm_count}, Full={true_count_full}, Sum={individual_sum}"
    
    def test_token_counting_consistency(self, context_manager, encoder):
        """Test that token counting is 100% consistent across calls."""
        test_cases = [
            "Simple test",
            "This is a longer test with multiple words.",
            "123456789",
            "!@#$%^&*()",
        ]
        
        for text in test_cases:
            true_count = len(encoder.encode(text))
            
            # Call multiple times
            counts = [context_manager.count_tokens(text) for _ in range(5)]
            
            # All should be identical
            assert all(c == counts[0] for c in counts), \
                f"Inconsistent counting for '{text}': {counts}"
            
            # And match true count
            assert counts[0] == true_count, \
                f"Count mismatch for '{text}': CM={counts[0]}, True={true_count}"
    
    def test_edge_cases_token_counting(self, context_manager, encoder):
        """Test edge cases in token counting."""
        edge_cases = [
            "",  # Empty string
            " ",  # Just space
            "\n\n\n",  # Just newlines
            "a",  # Single character
            "aaaaa...",  # Repeated character
        ]
        
        for text in edge_cases:
            true_count = len(encoder.encode(text))
            cm_count = context_manager.count_tokens(text)
            
            # Should match exactly
            assert cm_count == true_count, \
                f"Edge case mismatch for {repr(text)}: CM={cm_count}, True={true_count}"


class TestAccuracyReport:
    """Generate and report token counting accuracy metrics."""
    
    def test_accuracy_report_generation(self):
        """Generate a comprehensive accuracy report."""
        from closeclaw.context import ContextManager
        
        encoder = tiktoken.get_encoding("cl100k_base")
        cm = ContextManager()
        
        test_texts = [
            ("simple", "Hello world"),
            ("question", "What is the capital of France?"),
            ("poem", "The winter wind whispers through the trees " * 10),
            ("code", """
def fibonacci(n):
    if n <= 1: return n
    return fibonacci(n-1) + fibonacci(n-2)
"""),
            ("unicode", "你好世界 Привет مرحبا"),
        ]
        
        results = []
        total_accuracy = 0
        
        for label, text in test_texts:
            true_count = len(encoder.encode(text))
            cm_count = cm.count_tokens(text)
            accuracy = (1 - abs(cm_count - true_count) / max(true_count, 1)) * 100
            
            results.append({
                "type": label,
                "true_tokens": true_count,
                "cm_tokens": cm_count,
                "difference": abs(cm_count - true_count),
                "accuracy": accuracy,
            })
            
            total_accuracy += accuracy
        
        avg_accuracy = total_accuracy / len(results)
        
        # Print report
        print("\n=== Token Counting Accuracy Report ===")
        for r in results:
            print(f"  {r['type']:10}: True={r['true_tokens']:4} | CM={r['cm_tokens']:4} | " 
                  f"Diff={r['difference']:2} | Accuracy={r['accuracy']:.1f}%")
        print(f"Average Accuracy: {avg_accuracy:.2f}%")
        
        # Assert requirement
        assert avg_accuracy >= 98, f"Average accuracy {avg_accuracy:.2f}% below 98% threshold"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
