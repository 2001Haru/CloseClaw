from pathlib import Path

from closeclaw.memory.workspace_layout import migrate_legacy_memory_artifacts


def test_migrate_removes_empty_legacy_memory_dir(tmp_path):
    legacy_dir = tmp_path / "memory"
    legacy_dir.mkdir(parents=True, exist_ok=True)

    migrate_legacy_memory_artifacts(str(tmp_path))

    assert not legacy_dir.exists()
    assert (tmp_path / "CloseClaw Memory" / "memory").exists()
