# Memory Flush Bug - Root Cause Analysis & Repair Plan

**Status**: INVESTIGATION PHASE
**Date**: 2026-03-16
**Severity**: CRITICAL (Memory not persisting at all)

---

## Executive Summary

Agent successfully detects memory flush trigger and returns `[SILENT_REPLY]` marker, but **no tool calls are executed**. Memory files are not created (0 files saved). Investigation reveals the LLM response correctly contains `[SILENT_REPLY]` text but lacks `tool_calls` structure needed to invoke `write_memory_file`.

---

## Symptom Analysis

### What Works ✅
- Flush detection triggers correctly at 85%+ token usage
- LLM receives flush-only prompt with correct message format (system + user)
- LLM returns `[SILENT_REPLY]` text response
- History is cleared (11 → 1 message)

### What's Broken ❌
- `tool_calls` field is missing from LLM response
- `write_memory_file` tool is never invoked
- Memory files are never created
- Log: "NO TOOL CALLS in flush response"

### Log Evidence
```
[MEMORY_FLUSH] 📨 LLM Response: [SILENT_REPLY]...
[MEMORY_FLUSH] ✅ [SILENT_REPLY] marker detected
[MEMORY_FLUSH] ⚠️  NO TOOL CALLS in flush response
[MEMORY_FLUSH]    This means memory files were NOT saved!
[MEMORY_FLUSH] 📁 Collected 0 memory file(s)
```

---

## Root Cause Hypotheses (Ranked by Probability)

### Hypothesis 1: LLM Intent Problem [PROBABILITY: HIGHEST]
**Problem**: LLM chooses not to call tools despite receiving tools parameter

**Evidence Chain**:
1. Flush prompt asks LLM to call write_memory_file
2. LLM receives tools=[write_memory_file tool def]
3. LLM responds with text "[SILENT_REPLY]" but no tool_calls
4. API returns 200 OK with complete response

**Why This Happens**:
- LLM temperature might be 0.0 (deterministic mode) which may suppress tool calling
- LLM model might not properly support function calling at all
- LLM might interpret "[SILENT_REPLY]" as "that's the answer, no tools needed"
- Flush prompt might not be compelling enough to force tool calling

**Detection Method**:
```
1. Check self.config.temperature value
2. Log full API response JSON to see if tool_calls field exists but is empty
3. Try different models/providers to see if they support function calling
4. Adjust prompt to make tool calling mandatory
```

### Hypothesis 2: Tools Not Registered in self.tools [PROBABILITY: HIGH]
**Problem**: write_memory_file tool not in self.tools dict, so _format_tools_for_llm() returns empty list

**Evidence Chain**:
1. Tools registered via get_registered_tools() in runner.py
2. get_registered_tools() reads from _tool_registry
3. If write_memory_file decorator didn't execute, it won't be in registry
4. If self.tools is empty, no tools sent to LLM

**Why This Happens**:
- Tool registration happens in runner.py, but flush can be triggered before all tools loaded
- write_memory_file is in file_tools.py but might not be imported/executed
- Tool decorator might not have executed when module loads

**Detection Method**:
```
1. Add logging in _execute_pending_flush() to print self.tools.keys()
2. Verify write_memory_file is in the list
3. Check when/where tools are registered vs when flush is triggered
4. Add assertions that tools dict is not empty before calling LLM
```

### Hypothesis 3: Tools Parameter Not Sent to API [PROBABILITY: MEDIUM]
**Problem**: tools_for_llm list is formatted but not actually sent in API request

**Evidence Chain**:
1. _format_tools_for_llm() formats tools correctly
2. body["tools"] = tools assigned in generate()
3. body sent to API
4. API receives request without tools param or it's malformed

**Why This Happens**:
- Serialization bug (tools list has non-serializable objects)
- JSON encoding error
- API client strips unknown parameters
- URL encoding issue with tools parameter

**Detection Method**:
```
1. Already have debug output showing full request body
2. Check if "tools" field appears in debug output
3. Parse JSON sent to verify it's valid
4. Check if tools value is []  vs missing
```

### Hypothesis 4: API Response Parsing Error [PROBABILITY: MEDIUM]
**Problem**: tool_calls exist in API response but are not correctly parsed

**Evidence Chain**:
1. API returns complete response with tool_calls
2. _parse_response() looks for message.get("tool_calls")
3. Field is present but in unexpected format
4. Parsing fails silently, returns tool_calls=None

**Why This Happens**:
- Different LLM API implementations have different response structures
- ohmygpt API might use different field names or structure
- Tool call format might be non-standard
- Response has tool_calls but in wrong message field

**Detection Method**:
```
1. Log entire response JSON in _parse_response()
2. Check if choices[0].message has tool_calls field at all
3. Check if tool_calls is in different location (finish_reason, etc)
4. Verify JSON structure matches OpenAI format
```

---

## Technical Investigation Checklist

### Phase 1: Diagnosis (Add Logging)

#### 1.1 Check Tools Dictionary
**File**: closeclaw/agents/core.py → _execute_pending_flush()
**Action**: Add this logging at line 609 (before LLM call):
```python
logger.warning(f"[MEMORY_FLUSH] DEBUG: self.tools = {list(self.tools.keys())}")
logger.warning(f"[MEMORY_FLUSH] DEBUG: has write_memory_file? {'write_memory_file' in self.tools}")
```

**Expected**:
- Good: `['write_memory_file', 'read_file', 'execute_shell', ...]`
- Bad: `[]` (empty)

#### 1.2 Check Tools Formatting
**File**: closeclaw/agents/core.py → _execute_pending_flush()
**Action**: Add this logging at line 635 (after _format_tools_for_llm()):
```python
logger.warning(f"[MEMORY_FLUSH] DEBUG: tools_for_llm count = {len(tools_for_llm)}")
if tools_for_llm:
    logger.warning(f"[MEMORY_FLUSH] DEBUG: tool names = {[t['function']['name'] for t in tools_for_llm]}")
else:
    logger.warning(f"[MEMORY_FLUSH] DEBUG: tools_for_llm is EMPTY!")
```

**Expected**:
- Good: `tools_for_llm count = 7` (or more), includes write_memory_file
- Bad: `tools_for_llm count = 0`

#### 1.3 Check Raw API Response
**File**: closeclaw/agents/llm_providers.py → _parse_response()
**Action**: Add this logging at line 145 (before parsing message):
```python
logger.warning(f"[DEBUG] RAW API RESPONSE: {json.dumps(data, indent=2)[:2000]}")
message = choices[0].get("message", {})
logger.warning(f"[DEBUG] MESSAGE FIELDS: {list(message.keys())}")
logger.warning(f"[DEBUG] tool_calls field: {message.get('tool_calls', 'NOT FOUND')}")
```

**Expected**:
- Good: `"tool_calls": [{"id": "call_xxx", "function": {"name": "write_memory_file", ...}}]`
- Bad: `"tool_calls": null` or `tool_calls` key missing entirely

#### 1.4 Check Temperature Setting
**File**: closeclaw/agents/core.py → _execute_pending_flush()
**Action**: Add this logging at line 636 (in flush call):
```python
logger.warning(f"[MEMORY_FLUSH] DEBUG: temperature = {self.config.temperature}")
logger.warning(f"[MEMORY_FLUSH] DEBUG: tools param type = {type(tools_for_llm)}, len = {len(tools_for_llm)}")
```

**Expected**:
- Tool calling might not work well with temperature=0.0
- Check if tools param is being passed to generate()

---

### Phase 2: Root Cause Identification

Based on Phase 1 results:

| Finding | Root Cause | Next Step |
|---------|-----------|-----------|
| self.tools is empty | Tools not registered | Fix → Verify tool registration runs before flush |
| tools_for_llm is empty | _format_tools_for_llm bug | Fix → Implement fallback tool creation |
| tools param in API but empty | Serialization error | Fix → Validate tool JSON encoding |
| tool_calls missing in response | LLM doesn't support function calling | Fix → Disable tool calling, use manual parsing |
| tool_calls = null in response |  LLM chooses not to call | Fix → Modify prompt to force tool calls |
| tool_calls structure wrong | API format mismatch | Fix → Adapt parser for ohmygpt format |

---

### Phase 3: Repair Options

#### Option A: Force Tool Calling via Prompt
**If**: LLM understands tools but doesn't call them

**Fix**:
1. Modify flush prompt to be more imperative
2. Add "DO NOT respond without calling the tool" 
3. Add penalty for text-only responses

**Implementation**: memory_flush.py → _generate_memory_flush_prompt()

---

#### Option B: Register Tools at Flush Time
**If**: self.tools is empty at flush time

**Fix**:
1. Register write_memory_file tool directly in _execute_pending_flush()
2. Don't rely on get_registered_tools() which might not have been called
3. Create Tool object directly with all required fields

**Implementation**: core.py → _execute_pending_flush()

```python
# If tools missing, create write_memory_file on-the-fly
if not self.tools or 'write_memory_file' not in self.tools:
    from closeclaw.tools.file_tools import write_memory_file_impl
    # Register it dynamically
    self.register_tool(create_write_memory_tool())
```

---

#### Option C: Use Alternative Completion Method
**If**: Function calling not working for this LLM

**Fix**:
1. Fall back to structured completion (no tool calling)
2. Parse tool calls from text response manually
3. Use special markers like [TOOL: write_memory_file PATH CONTENT]

**Implementation**: llm_providers.py → New alternative parser

---

#### Option D: Try Different Temperature
**If**: temperature=0.0 suppresses tool calling

**Fix**:
1. Use higher temperature for flush-only calls (e.g., 0.2-0.5)
2. Keep agent temperature unchanged
3. Special case: flush calls get different temperature

**Implementation**: core.py → Pass special temperature to generate()

```python
llm_response, tool_calls = await self.llm_provider.generate(
    messages=flush_only_messages,
    tools=tools_for_llm,
    temperature=0.3,  # Override with higher value for tool calling
)
```

---

## Recommended Repair Sequence

1. **Week 1 (This Week)**:
   - Run Phase 1 diagnostics (add all logging)
   - Collect data about which hypothesis is correct
   - Identify root cause

2. **Week 2**:
   - Implement repair based on identified root cause
   - Test with new logging in place
   - Verify tool calls are now being extracted

3. **Week 3**:
   - Remove debug logging
   - Run full test suite
   - Verify memory files are created and persisted

---

## Testing Strategy

### Test 1: Verify Tool Registration
```python
def test_write_memory_file_registered():
    from closeclaw.tools.base import get_registered_tools
    tools = get_registered_tools()
    tool_names = [t.name for t in tools]
    assert 'write_memory_file' in tool_names, "write_memory_file not registered"
```

### Test 2: Verify Tools Passed to LLM
```python
def test_tools_passed_to_llm():
    agent = create_test_agent()
    tools_list = agent._format_tools_for_llm()
    tool_names = [t['function']['name'] for t in tools_list]
    assert 'write_memory_file' in tool_names, "write_memory_file not in LLM tools"
```

### Test 3: Verify Tool Calls Parsed
```python
def test_tool_calls_parsed():
    # Mock LLM response with tool_calls
    response_json = {
        "choices": [{
            "message": {
                "content": "[SILENT_REPLY]",
                "tool_calls": [{
                    "id": "call_123",
                    "function": {
                        "name": "write_memory_file",
                        "arguments": '{"path": "/tmp/test", "content": "test"}'
                    }
                }]
            }
        }]
    }
    text, tool_calls = provider._parse_response(response_json)
    assert tool_calls is not None and len(tool_calls) > 0
    assert tool_calls[0].name == 'write_memory_file'
```

---

## Prevention measures

1. **Add assertions**: Require self.tools not empty before flush
2. **Add golden path test**: Full end-to-end flush with memory file creation
3. **Monitor log output**: Alert if "NO TOOL CALLS" appears
4. **Timeout handling**: If flush hangs, fall back to manual logging

---

## Timeline Estimate

- Phase 1 (Add logging): 15 minutes
- Phase 2 (Identify root cause): 1-2 hours (waiting for user to run with logging)
- Phase 3 (Implement fix): 30 minutes - 1 hour
- Phase 4 (Test & verify): 1 hour

**Total**: 3-5 hours

