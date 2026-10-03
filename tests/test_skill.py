"""The installable Agent Skill is generated from the CLI's own declarations."""

from __future__ import annotations

from pathlib import Path

from hass_axi import skill
from hass_axi.cli import COMMAND_ORDER, command_specs

REPO_ROOT = Path(__file__).resolve().parents[1]


def rendered():
    return skill.render([command_specs()[name] for name in COMMAND_ORDER])


def test_the_skill_has_trigger_shaped_frontmatter():
    text = rendered()
    assert text.startswith("---\nname: hass-axi\ndescription: ")
    assert "Use whenever a task touches home automation" in text


def test_the_skill_documents_every_command():
    text = rendered()
    for name in COMMAND_ORDER:
        assert f"### `hass-axi {name}`" in text


def test_the_skill_omits_live_state_and_names_no_installation():
    text = rendered()
    assert "example.com" in text
    for leak in ("192.168.", "10.0.0.", "eyJ"):
        assert leak not in text


def test_the_skill_offers_a_form_that_needs_no_global_install():
    text = rendered()
    assert "uvx hass-axi" in text
    assert "pipx run hass-axi" in text


def test_the_committed_skill_is_current():
    """CI runs `hass-axi setup skill --check`; this fails first and faster."""
    committed = skill.target_path(REPO_ROOT)
    assert committed.exists(), "run `hass-axi setup skill` and commit the result"
    assert committed.read_text(encoding="utf-8") == rendered()


def test_setup_skill_writes_then_reports_current(run_cli, tmp_path):
    code, out = run_cli(["setup", "skill", "--path", str(tmp_path)], {})
    assert code == 0
    assert "status: written" in out
    assert (tmp_path / "skills" / "hass-axi" / "SKILL.md").exists()

    code, out = run_cli(["setup", "skill", "--path", str(tmp_path)], {})
    assert code == 0
    assert "status: current" in out


def test_setup_skill_check_detects_a_missing_or_stale_copy(run_cli, tmp_path):
    code, out = run_cli(["setup", "skill", "--path", str(tmp_path), "--check"], {})
    assert code == 1
    assert "status: missing" in out

    target = tmp_path / "skills" / "hass-axi" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("stale\n", encoding="utf-8")
    code, out = run_cli(["setup", "skill", "--path", str(tmp_path), "--check"], {})
    assert code == 1
    assert "status: stale" in out


def test_setup_skill_check_passes_on_this_repository(run_cli):
    code, out = run_cli(["setup", "skill", "--path", str(REPO_ROOT), "--check"], {})
    assert code == 0
    assert "status: current" in out
