"""Tests for SkillsLoader and prompt injection wiring."""

from pathlib import Path
import shutil

from closeclaw.agents.core import AgentCore
from closeclaw.services.skills_loader import SkillsLoader
from closeclaw.types import AgentConfig


class _DummyLLM:
    async def generate(self, messages, tools, **kwargs):
        return "ok", None


def _write_skill(root: Path, name: str, body: str, metadata: str = '{"closeclaw": {}}') -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                "description: test skill",
                f"metadata: '{metadata}'",
                "---",
                "",
                body,
            ]
        ),
        encoding="utf-8",
    )


def test_skills_loader_reads_workspace_and_always_flag(tmp_path):
    _write_skill(
        tmp_path,
        "always_demo",
        "Always guidance",
        metadata='{"closeclaw": {"always": true}}',
    )
    _write_skill(
        tmp_path,
        "ondemand_demo",
        "On demand guidance",
    )

    loader = SkillsLoader(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin_skills")

    names = {s["name"] for s in loader.list_skills(filter_unavailable=True)}
    assert "always_demo" in names
    assert "ondemand_demo" in names

    always = loader.get_always_skills()
    assert always == ["always_demo"]

    summary = loader.build_skills_summary()
    assert "<skills>" in summary
    assert "always_demo" in summary
    assert "ondemand_demo" in summary


def test_agent_prompt_injects_always_skill_and_skills_index(tmp_path):
    _write_skill(
        tmp_path,
        "always_demo",
        "This is always injected",
        metadata='{"closeclaw": {"always": true}}',
    )

    config = AgentConfig(
        model="openai/gpt-4",
        system_prompt="Base system",
    )
    agent = AgentCore(
        agent_id="test-agent",
        llm_provider=_DummyLLM(),
        config=config,
        workspace_root=str(tmp_path),
    )

    prompt = agent._build_system_prompt()

    assert "[ALWAYS SKILLS]" in prompt
    assert "This is always injected" in prompt
    assert "[SKILLS INDEX]" in prompt
    assert "<skills>" in prompt


def test_agent_prompt_hot_reloads_skill_content(tmp_path):
    _write_skill(
        tmp_path,
        "always_demo",
        "version-one",
        metadata='{"closeclaw": {"always": true}}',
    )

    config = AgentConfig(
        model="openai/gpt-4",
        system_prompt="Base system",
    )
    agent = AgentCore(
        agent_id="test-agent",
        llm_provider=_DummyLLM(),
        config=config,
        workspace_root=str(tmp_path),
    )

    prompt_v1 = agent._build_system_prompt()
    assert "version-one" in prompt_v1

    # Update SKILL.md content without recreating AgentCore; next prompt build should reflect change.
    skill_file = tmp_path / "skills" / "always_demo" / "SKILL.md"
    text = skill_file.read_text(encoding="utf-8")
    skill_file.write_text(text.replace("version-one", "version-two"), encoding="utf-8")

    prompt_v2 = agent._build_system_prompt()
    assert "version-two" in prompt_v2
    assert "version-one" not in prompt_v2


def test_skills_loader_openclaw_frontmatter_compatibility(tmp_path):
    skill_name = "openclaw_style"
    skill_dir = tmp_path / "skills" / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {skill_name}",
                'description: "OpenClaw compatibility test"',
                "homepage: https://example.com/skill",
                'metadata: {"openclaw":{"always": true}}',
                "---",
                "",
                "Body",
            ]
        ),
        encoding="utf-8",
    )

    loader = SkillsLoader(workspace=tmp_path)

    metadata = loader.get_skill_metadata(skill_name)
    assert metadata is not None
    assert metadata.get("homepage") == "https://example.com/skill"

    always = loader.get_always_skills()
    assert skill_name in always


def test_skills_loader_supports_hot_plug_and_unplug(tmp_path):
    loader = SkillsLoader(workspace=tmp_path)

    _write_skill(
        tmp_path,
        "hotplug",
        "dynamic",
        metadata='{"closeclaw": {"always": false}}',
    )

    names_after_add = {s["name"] for s in loader.list_skills(filter_unavailable=False)}
    assert "hotplug" in names_after_add

    shutil.rmtree(tmp_path / "skills" / "hotplug")

    names_after_remove = {s["name"] for s in loader.list_skills(filter_unavailable=False)}
    assert "hotplug" not in names_after_remove
