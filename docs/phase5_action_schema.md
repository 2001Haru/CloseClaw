# Phase5 Action Schema (P0 Freeze)

更新时间: 2026-03-18

## Action

```python
@dataclass
class Action:
    type: Literal["tool_call", "final_answer", "plan_update"]
    payload: dict[str, Any]
    reason: str
    confidence: float
```

## Observation

```python
@dataclass
class Observation:
    kind: Literal["tool_result", "final_answer", "plan_update", "error"]
    status: str
    data: dict[str, Any]
    error: Optional[str]
```

## Decision

```python
@dataclass
class Decision:
    stop: bool
    reason: str
    output: Optional[dict[str, Any]]
```

## Runtime Output Contract

所有结束输出必须包含:

1. response
2. tool_calls
3. tool_results
4. requires_auth
5. memory_flushed
