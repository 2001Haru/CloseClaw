# CloseClaw Skills

This directory stores workspace skills for CloseClaw.

CloseClaw is compatible with OpenClaw-style SKILL.md frontmatter, including fields such as homepage and metadata.

## 1) Folder Layout

Each skill must be placed in its own folder:

```text
closeclaw/skills/
	<skill-name>/
		SKILL.md
```

Rules:
- The folder name is the runtime skill id.
- The file name must be SKILL.md.
- Loader discovery is dynamic, so add/remove folders to plug/unplug skills.

## 2) Minimal SKILL.md Template

```markdown
---
name: my-skill
description: Short description with trigger words users may say.
homepage: https://example.com/my-skill
metadata: {"closeclaw":{"always": false}}
---

# My Skill

When to use:
- Trigger phrase A
- Trigger phrase B

Usage:
1. Step one
2. Step two
```

Frontmatter requirements:
- name: required
- description: required
- homepage: optional but recommended
- metadata: optional but recommended for behavior control

## 3) metadata Format and Compatibility

metadata is JSON text placed in frontmatter. 
Common examples:

Always-load skill in CloseClaw:

```yaml
metadata: {"closeclaw":{"always": true}}
```

Require CLI tools and env vars:

```yaml
metadata: {"closeclaw":{"requires":{"bins":["gh"],"env":["GITHUB_TOKEN"]}}}
```

Notes:
- always=true means the skill body is injected every turn.
- requires.bins and requires.env are checked by loader availability filtering.
- Extra metadata keys are preserved in frontmatter and can be extended.

## 4) Plug and Unplug Behavior

CloseClaw loader re-scans skills on each prompt build.

Plug in:
1. Create closeclaw/skills/<skill-name>/SKILL.md.
2. Start a new turn and the skill will be discovered.

Unplug:
1. Remove closeclaw/skills/<skill-name>.
2. Start a new turn and the skill disappears from the skills index.

No restart is required for discovery updates in normal prompt flow.

## 5) Authoring Best Practices

- Keep description concrete and searchable.
- Put trigger phrases in description or body section "When to use".
- Keep SKILL.md concise and task-oriented.
- Put long references into separate files and load on demand.
- Keep shell commands copy-paste ready.
- Use ASCII-friendly formatting unless Unicode is necessary.

## 6) Current Skills in This Workspace

| Skill | Description |
|-------|-------------|
| github | Interact with GitHub using the gh CLI |
| clawhub | Search and install skills from ClawHub registry |
| skill-creator | Guidance for creating and packaging new skills |
| complex_task | Always-on workflow guidance for multi-step tasks |