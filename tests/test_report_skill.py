from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml

from plan_mode import plugin as plugin_mod
from plan_mode.plugin import PlanModePlugin, REPORT_SKILL, REPORT_SKILL_DESCRIPTION, REPORT_SKILL_PATH
from test_plan_mode import FakeContext, session_env
from test_real_plugin_manager import _copy_plugin

REPO_ROOT = Path(__file__).resolve().parents[1]
BUG_FORM = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"


def _skill_text() -> str:
    return REPORT_SKILL_PATH.read_text(encoding="utf-8")


def test_registers_the_report_skill_with_name_description_and_frontmatter(session_env):
    ctx = FakeContext()
    PlanModePlugin(ctx).register()
    entry = ctx.skills[REPORT_SKILL]
    assert entry["path"] == REPO_ROOT / "skills" / "plan-mode-report-problem" / "SKILL.md"
    assert entry["path"].is_file()
    assert entry["description"] == REPORT_SKILL_DESCRIPTION
    assert entry["frontmatter"] == {"name": REPORT_SKILL, "description": REPORT_SKILL_DESCRIPTION}


def test_skill_file_frontmatter_matches_the_registration():
    match = re.match(r"---\n(.*?)\n---\n", _skill_text(), re.S)
    assert match
    assert yaml.safe_load(match.group(1)) == {"name": REPORT_SKILL, "description": REPORT_SKILL_DESCRIPTION}


@pytest.mark.parametrize("capability", ["missing", "raises"])
def test_skill_registration_fails_open(session_env, capability):
    def _raise(*args, **kwargs):
        raise FileNotFoundError("no skill")

    ctx = FakeContext()
    ctx.register_skill = None if capability == "missing" else _raise
    PlanModePlugin(ctx).register()
    assert "plan_mode" in ctx.tools and "pre_tool_call" in ctx.hooks and ctx.skills == {}


def test_skill_headings_match_the_bug_form():
    headings = re.findall(r"^### (.+)$", _skill_text(), re.M)
    form = yaml.safe_load(BUG_FORM.read_text(encoding="utf-8"))
    labels = [item["attributes"]["label"] for item in form["body"]
              if item.get("type") in {"textarea", "input", "dropdown"}]
    assert headings == labels


def test_skill_has_no_inline_shell_and_points_at_this_repo():
    text = _skill_text()
    assert "!`" not in text  # Hermes inline-shell syntax: viewing the skill must never run a command.
    repos = set(re.findall(r"github\.com/([\w.-]+/[\w.-]+)", text)) | set(re.findall(r"-R ([\w.-]+/[\w.-]+)", text))
    assert repos == {"100yenadmin/hermes-plan-mode"}
    assert "https://github.com/100yenadmin/hermes-plan-mode/security/advisories/new" in text
    assert "Only a yes after the user has seen it does." in text


def test_real_plugin_manager_registers_the_report_skill(tmp_path, monkeypatch):
    pytest.importorskip("hermes_cli.plugins")
    home = tmp_path / "hermes-home"
    empty_bundled = tmp_path / "empty-bundled"
    empty_bundled.mkdir()
    _copy_plugin(home / "plugins" / "plan-mode")
    (home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - plan-mode\n  load_timeout_seconds: 0\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(empty_bundled))
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "0")

    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_cli import plugins

    home_token = set_hermes_home_override(str(home))
    try:
        plugins._reset_plugin_managers_for_tests()
        manager = plugins.get_plugin_manager()
        manager.discover_and_load()
        if not hasattr(manager, "find_plugin_skill"):
            pytest.skip("Hermes build has no plugin skills")
        found = manager.find_plugin_skill(f"plan-mode:{REPORT_SKILL}")
        assert found is not None
        assert Path(found).resolve() == (home / "plugins" / "plan-mode" / "skills" / REPORT_SKILL / "SKILL.md").resolve()
        assert manager.list_plugin_skills("plan-mode") == [REPORT_SKILL]
    finally:
        plugins._reset_plugin_managers_for_tests()
        reset_hermes_home_override(home_token)
