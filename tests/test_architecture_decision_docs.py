"""Smoke tests for the Phase 8 architecture decision record."""

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_ADR_PATH = _REPO_ROOT / "docs" / "adr" / "0001-keep-custom-bedrock-orchestration.md"


def test_phase_8_adr_exists_and_accepts_custom_orchestration() -> None:
    adr = _ADR_PATH.read_text(encoding="utf-8")

    assert "Status: Accepted" in adr
    assert "Keep the project on custom Python orchestration" in adr
    assert "InvokeAgent" in adr
    assert "CreateAgentActionGroup" in adr


def test_readme_links_phase_8_adr() -> None:
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/adr/0001-keep-custom-bedrock-orchestration.md" in readme
