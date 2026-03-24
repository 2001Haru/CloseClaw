"""Skills loader for agent capabilities.

Loads markdown skills from built-in and workspace locations,
filters by runtime requirements, and prepares content for prompt injection.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path


class SkillsLoader:
    """Load and summarize SKILL.md assets for prompt-time usage."""

    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """List all discovered skills from workspace and built-in locations.

        Workspace skills take precedence over built-in skills when names collide.
        """
        skills: list[dict[str, str]] = []

        if self.workspace_skills.exists():
            for skill_dir in self.workspace_skills.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if skill_file.exists():
                    skills.append(
                        {
                            "name": skill_dir.name,
                            "path": str(skill_file),
                            "source": "workspace",
                        }
                    )

        if self.builtin_skills and self.builtin_skills.exists():
            for skill_dir in self.builtin_skills.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if skill_file.exists() and not any(s["name"] == skill_dir.name for s in skills):
                    skills.append(
                        {
                            "name": skill_dir.name,
                            "path": str(skill_file),
                            "source": "builtin",
                        }
                    )

        if not filter_unavailable:
            return skills

        return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]

    def load_skill(self, name: str) -> str | None:
        """Load a skill by directory name, preferring workspace override."""
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill.read_text(encoding="utf-8")

        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")

        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """Load and format selected skills for direct prompt injection."""
        parts: list[str] = []
        for name in skill_names:
            content = self.load_skill(name)
            if not content:
                continue
            content = self._strip_frontmatter(content)
            parts.append(f"### Skill: {name}\n\n{content}")

        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self) -> str:
        """Build XML summary for on-demand progressive skill loading."""
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        def escape_xml(value: str) -> str:
            return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<skills>"]
        for skill in all_skills:
            name = escape_xml(skill["name"])
            path = skill["path"]
            description = escape_xml(self._get_skill_description(skill["name"]))
            skill_meta = self._get_skill_meta(skill["name"])
            available = self._check_requirements(skill_meta)

            lines.append(f"  <skill available=\"{str(available).lower()}\">")
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{description}</description>")
            lines.append(f"    <location>{path}</location>")

            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")

            lines.append("  </skill>")

        lines.append("</skills>")
        return "\n".join(lines)

    def get_always_skills(self) -> list[str]:
        """Return skills marked always=true and whose requirements are satisfied."""
        result: list[str] = []

        for skill in self.list_skills(filter_unavailable=True):
            metadata = self.get_skill_metadata(skill["name"]) or {}
            scoped_meta = self._parse_skill_metadata(metadata.get("metadata", ""))
            if scoped_meta.get("always") or metadata.get("always"):
                result.append(skill["name"])

        return result

    def get_skill_metadata(self, name: str) -> dict | None:
        """Extract frontmatter metadata from SKILL.md."""
        content = self.load_skill(name)
        if not content:
            return None

        if not content.startswith("---"):
            return None

        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return None

        metadata: dict[str, str] = {}
        for line in match.group(1).split("\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip().strip('"\'')

        return metadata

    def _get_skill_description(self, name: str) -> str:
        metadata = self.get_skill_metadata(name)
        if metadata and metadata.get("description"):
            return str(metadata["description"])
        return name

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        missing: list[str] = []
        requires = skill_meta.get("requires", {})

        for binary in requires.get("bins", []):
            if not shutil.which(binary):
                missing.append(f"CLI: {binary}")

        for env_name in requires.get("env", []):
            if not os.environ.get(env_name):
                missing.append(f"ENV: {env_name}")

        return ", ".join(missing)

    def _strip_frontmatter(self, content: str) -> str:
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content

    def _parse_skill_metadata(self, raw: str) -> dict:
        """Parse JSON metadata from frontmatter and read closeclaw/openclaw scopes."""
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {}
            return data.get("closeclaw", data.get("openclaw", {}))
        except (json.JSONDecodeError, TypeError):
            return {}

    def _check_requirements(self, skill_meta: dict) -> bool:
        requires = skill_meta.get("requires", {})

        for binary in requires.get("bins", []):
            if not shutil.which(binary):
                return False

        for env_name in requires.get("env", []):
            if not os.environ.get(env_name):
                return False

        return True

    def _get_skill_meta(self, name: str) -> dict:
        metadata = self.get_skill_metadata(name) or {}
        return self._parse_skill_metadata(metadata.get("metadata", ""))
