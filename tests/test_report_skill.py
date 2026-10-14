from __future__ import annotations

from pathlib import Path
import re
import shlex

import pytest

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
    yaml = pytest.importorskip("yaml")  # the hermes-compat jobs install no PyYAML; the plain test job does
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
    yaml = pytest.importorskip("yaml")
    headings = re.findall(r"^### (.+)$", _skill_text(), re.M)
    form = yaml.safe_load(BUG_FORM.read_text(encoding="utf-8"))
    labels = [item["attributes"]["label"] for item in form["body"]
              if item.get("type") in {"textarea", "input", "dropdown"}]
    assert headings == labels


def test_skill_has_no_inline_shell_and_files_only_to_this_repo():
    text = _skill_text()
    assert "!`" not in text  # Hermes inline-shell syntax: viewing the skill must never run a command.
    assert set(re.findall(r"-R ([\w.-]+/[\w.-]+)", text)) == {"100yenadmin/hermes-plan-mode"}  # every gh call
    links = set(re.findall(r"github\.com/([\w.-]+/[\w.-]+)", text))
    assert links == {"100yenadmin/hermes-plan-mode", "NousResearch/hermes-agent"}  # Hermes bugs are only pointed to
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


_FILE_SUBST = re.compile(r"\$\(cat '<[a-z ]+ file>'\)")
_FILE_PATH = re.compile(r"<[a-z ]+ file>")


def _unsafe_gh_arguments(line: str) -> list[str]:
    """Values of --title/--search/--body-file in the `gh issue` command on this line that are not read from a file.

    shlex removes one level of quoting, so the safe forms arrive as `$(cat '<x file>')` and `<x file>`; anything
    else (typed text, an unquoted path, a substitution other than reading the file) is reported."""
    command = line[line.index("gh issue"):].split("`")[0]
    words = shlex.split(command)
    unsafe = []
    pairs = list(zip(words, words[1:]))
    pairs += [tuple(word.split("=", 1)) for word in words if word.startswith("--") and "=" in word]
    for flag, value in pairs:
        if flag in ("--title", "--search") and not _FILE_SUBST.fullmatch(value):
            unsafe.append(f"{flag} {value}")
        elif flag == "--body-file" and not _FILE_PATH.fullmatch(value):
            unsafe.append(f"{flag} {value}")
    return unsafe


def test_unsafe_gh_argument_check_catches_inline_values():
    assert _unsafe_gh_arguments('gh issue list -R o/r --search "plan mode $(command)"') == [
        "--search plan mode $(command)"
    ]
    assert _unsafe_gh_arguments("gh issue create -R o/r --title 'Plan Mode stalls' --body-file '<draft file>'") == [
        "--title Plan Mode stalls"
    ]
    assert _unsafe_gh_arguments("gh issue comment 7 -R o/r --body-file <draft file>") == ["--body-file <draft"]
    assert _unsafe_gh_arguments('gh issue create -R o/r --title="x $(id)" --body-file=notes.md') == [
        "--title x $(id)", "--body-file notes.md"
    ]
    assert _unsafe_gh_arguments("gh issue list -R o/r --search \"$(cat '<search file>')\"") == []
    assert _unsafe_gh_arguments("gh issue list -R o/r --search=\"$(cat '<search file>')\"") == []


@pytest.mark.parametrize("path", [REPORT_SKILL_PATH, REPO_ROOT / "AGENTS.md"], ids=["skill", "agents"])
def test_model_written_text_never_becomes_shell_source(path):
    """Titles, search words and drafts are read from files: command substitution output is not re-parsed, and a
    quoted path keeps its spaces. Typing model text into the command would let quotes or `$()` escape."""
    gh_lines = [line for line in path.read_text(encoding="utf-8").splitlines() if "gh issue" in line]
    assert gh_lines
    assert [arg for line in gh_lines for arg in _unsafe_gh_arguments(line)] == []
    assert any("--search \"$(cat '<search file>')\"" in line for line in gh_lines)
    if path == REPORT_SKILL_PATH:
        assert any("--title \"$(cat '<title file>')\" --body-file '<draft file>'" in line for line in gh_lines)
        assert any("gh issue comment" in line and "--body-file '<draft file>'" in line for line in gh_lines)


def test_skill_routes_hermes_security_problems_and_offers_a_comment_fallback():
    text = _skill_text()
    assert "https://github.com/NousResearch/hermes-agent/security" in text
    assert "as a comment on the matching issue (give its link)" in text
