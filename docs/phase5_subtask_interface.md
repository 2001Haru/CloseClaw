# Phase5 Subtask Interface (P4 Reserve Only)

更新时间: 2026-03-19

## 1. Scope

P4 仅冻结子任务接口与生命周期 registry，不引入子任务执行引擎。

## 2. API Surface

1. spawn_subtask(parent_run_id, spec) -> SubtaskHandle
2. wait_subtask(handle) -> SubtaskRecord
3. cancel_subtask(handle, reason) -> SubtaskRecord

## 3. Lifecycle

allowed:

1. created -> running
2. created -> cancelled
3. created -> failed
4. running -> completed
5. running -> failed
6. running -> cancelled

terminal:

1. completed
2. failed
3. cancelled

Terminal states forbid further transitions.

## 4. Error Codes

1. subtask_not_found
2. subtask_invalid_transition
3. subtask_already_terminal

## 5. Integration Constraints

1. P4 does not alter orchestrator ACT execution path.
2. Registry is in-memory only (single-process scope).
3. This interface is reserved for Phase5+ spawn/wait execution integration.
