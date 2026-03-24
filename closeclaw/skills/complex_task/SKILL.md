---
name: complex-task-workflow
description: Workflow for handling multi-step and complex tasks using a Todo-Driven approach.
metadata: {"closeclaw": {"always": true}}
---

# CloseClaw Complex Task Workflow (Phase C)

You are equipped with a Todo-Driven working memory. Whenever you receive a multi-step or complex task, you MUST adhere to the following workflow:

## 1. PLAN (Initialize)
- Your FIRST action must be to create or update a file named `TODO.md` in the workspace.
- Break down the user's request into actionable, sequential steps.
- Use strict formatting: 
  - `[ ]` for Not Started
  - `[~]` for In Progress (or blocked)
  - `[x]` for Completed
  - `[!]` for Failed / Needs Replan

## 2. ACT (Execution)
- At each step, read `TODO.md` (if you lose track of it) and pick the FIRST incomplete task (`[ ]` or `[~]`).
- Focus ONLY on completing this single step using your tools (e.g., `run_terminal`, `edit_file`).
- Do NOT try to execute multiple separate steps in one response.

## 3. OBSERVE & DECIDE (Reflect & Update)
- After a tool returns a result, carefully examine the RAW OUTPUT.
- **If SUCCESS**: Use the `edit_file` tool to mark the current step as `[x]` in `TODO.md`, and then proceed to the next incomplete step.
- **If FAILED/ERROR**: Do NOT blindly retry the exact same command. You MUST update `TODO.md` to reflect the error (e.g., add a new `[ ] sub-task: fix bug in file XYZ`) before continuing.
- **If USER INTERVENES**: The user may manually modify `TODO.md`. Always trust the file's current state as the absolute truth.
